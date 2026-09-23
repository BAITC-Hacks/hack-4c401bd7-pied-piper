"""Read-only UI adapter and directed graph views. No role calculations."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from validate import SCHEMAS, ValidationError, columns, ids, numeric, read_csv, require, validate_frames

FILES = (*SCHEMAS, 'node_metrics.parquet', 'edges.parquet')
COLORS = {'consolidator': '#f7b955', 'transit': '#49c9bd', 'distributor': '#679eff',
          'terminal': '#ce8ff0', 'coordinator': '#ff7f8a', 'peripheral': '#96a7be'}
LABELS = {'consolidator': 'Консолидация', 'transit': 'Транзит', 'distributor': 'Распределение',
          'terminal': 'Конечный получатель', 'coordinator': 'Структурный посредник', 'peripheral': 'Без выраженной роли'}


@dataclass
class Bundle:
    nodes: pd.DataFrame
    edges: pd.DataFrame
    clusters: pd.DataFrame
    top: pd.DataFrame
    manifest: dict
    report: dict
    raw: dict[str, bytes]


def load_bundle(directory: Path) -> Bundle:
    directory = Path(directory)
    try:
        manifest_bytes = (directory / 'run.json').read_bytes()
        manifest = json.loads(manifest_bytes)
        require(isinstance(manifest, dict), 'run.json: ожидается объект')
        require(manifest.get('schema_version') == 1, 'Несовместимая schema_version: поддерживается 1')
        hashes = manifest.get('output_sha256', {})
        require(isinstance(hashes, dict), 'run.json: output_sha256 должен быть объектом')
        raw = {}
        for name in FILES:
            require(name in hashes, f'run.json: отсутствует SHA256 для {name}')
            raw[name] = (directory / name).read_bytes()
            require(sha256(raw[name]).hexdigest() == hashes[name], f'{name}: SHA256 не совпадает; расчёт не завершён или файлы смешаны')
        require((directory / 'run.json').read_bytes() == manifest_bytes, 'Расчёт обновился во время чтения; перезагрузите результаты')
        frames = [read_csv(BytesIO(raw[name]), name) for name in SCHEMAS]
        metrics = pd.read_parquet(BytesIO(raw['node_metrics.parquet']))
        edges = pd.read_parquet(BytesIO(raw['edges.parquet']))
        required = ['gid', 'depth', 'is_seed', 'in_deg', 'out_deg', 'in_kzt', 'out_kzt', 'in_tx', 'out_tx', 'pagerank']
        columns(metrics, required, 'node_metrics')
        metrics['gid'] = ids(metrics.gid)
        require(not metrics.gid.duplicated().any(), 'node_metrics: повторные gid')
        for col in ('src', 'dst'):
            edges[col] = ids(edges[col], col)
        numeric(metrics, ['depth', 'in_deg', 'out_deg', 'in_tx', 'out_tx'], 'node_metrics', integer=True)
        numeric(metrics, ['in_kzt', 'out_kzt', 'pagerank'], 'node_metrics')
        require(metrics[['in_deg', 'out_deg', 'in_tx', 'out_tx', 'in_kzt', 'out_kzt', 'pagerank']].ge(0).all().all(), 'node_metrics: отрицательная метрика')
        require(type(manifest.get('n_nodes')) is int and manifest['n_nodes'] > 0, 'run.json: нужен n_nodes > 0')
        roles, clusters, top, report = validate_frames(*frames, metrics[required[:3]], edges, manifest['n_nodes'])
        metrics['is_seed'] = metrics.is_seed.astype(str).str.lower().isin(['true', '1'])
        for field, endpoint, source, aggregation in (
            ('in_deg', 'dst', 'src', 'nunique'), ('out_deg', 'src', 'dst', 'nunique'),
            ('in_kzt', 'dst', 'sum_kzt', 'sum'), ('out_kzt', 'src', 'sum_kzt', 'sum'),
            ('in_tx', 'dst', 'n_tx', 'sum'), ('out_tx', 'src', 'n_tx', 'sum')):
            calculated = metrics.gid.map(edges.groupby(endpoint)[source].agg(aggregation)).fillna(0)
            require(np.allclose(metrics[field], calculated, rtol=1e-9, atol=.01 if 'kzt' in field else 0),
                    f'node_metrics: {field} не соответствует edges')
        for name in set(metrics.columns) & set(roles.columns) - {'gid'}:
            left = metrics.set_index('gid').loc[roles.gid, name].reset_index(drop=True)
            right = roles[name].reset_index(drop=True)
            if pd.api.types.is_numeric_dtype(right):
                require(np.allclose(pd.to_numeric(left, errors='raise'), right, rtol=1e-9, atol=1e-9), f'node_metrics: отличается {name}')
            else:
                require(left.astype(str).tolist() == right.astype(str).tolist(), f'node_metrics: отличается {name}')
        base_roles = roles[SCHEMAS['nodes_roles.csv']]
        joined = base_roles.merge(metrics.drop(columns=[c for c in base_roles.columns if c != 'gid' and c in metrics]), on='gid', validate='one_to_one')
        graph = make_graph(joined, edges)
        groups = sorted(nx.weakly_connected_components(graph), key=lambda group: (-len(group), min(map(int, group))))
        membership = {gid: i for i, group in enumerate(groups) for gid in group}
        if 'component_id' not in joined:
            joined['component_id'] = joined.gid.map(membership)
        else:
            columns(joined, ['component_id'], 'node_metrics')
            numeric(joined, ['component_id'], 'node_metrics', integer=True)
            check = joined.assign(actual_component=joined.gid.map(membership))
            require(check.groupby('component_id').actual_component.nunique().eq(1).all() and
                    check.groupby('actual_component').component_id.nunique().eq(1).all(), 'node_metrics: неверные компоненты')
        return Bundle(joined, edges, clusters, top, manifest, report, raw)
    except ValidationError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValidationError(f'Нет корректного завершённого расчёта в {directory}: {exc}') from exc


def make_graph(nodes, edges):
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes.gid.astype(str))
    for row in edges.itertuples(index=False):
        graph.add_edge(str(row.src), str(row.dst), sum_kzt=float(row.sum_kzt), n_tx=int(row.n_tx))
    return graph


def select_ego(graph, gid, nodes, hops=1, limit=100):
    require(gid in graph, f'gid {gid} не найден')
    distance = nx.single_source_shortest_path_length(graph.to_undirected(as_view=True), gid, cutoff=hops)
    priorities = nodes.set_index('gid').priority_score.to_dict()
    ordered = sorted(distance, key=lambda node: (distance[node], -priorities[node], int(node)))
    chosen = ordered[:limit]
    return graph.subgraph(chosen).copy(), len(ordered) - len(chosen)


def _figure(graph, attributes, color_by='role', selected=None, title='', edge_limit=350):
    figure = go.Figure()
    if not graph:
        figure.add_annotation(text='Нет узлов для выбранных фильтров', showarrow=False)
        return figure
    positions = nx.spring_layout(graph, seed=42, weight=None, iterations=45)
    edges = sorted(graph.edges(data=True), key=lambda e: (-e[2].get('sum_kzt', 0), str(e[0]), str(e[1])))[:edge_limit]
    # Arrows are in data coordinates and point payer -> recipient. Nodes remain clickable.
    xs, ys = [], []
    hover_x, hover_y, hover_text = [], [], []
    arrows = []
    for source, target, data in edges:
        x0, y0 = positions[source]
        x1, y1 = positions[target]
        xs.extend([x0, x1, None]); ys.extend([y0, y1, None])
        arrows.append(dict(x=float(x0 + .78 * (x1-x0)), y=float(y0 + .78 * (y1-y0)),
                              ax=float(x0 + .56 * (x1-x0)), ay=float(y0 + .56 * (y1-y0)),
                              xref='x', yref='y', axref='x', ayref='y', text='', showarrow=True,
                              arrowhead=3, arrowsize=1, arrowwidth=1.2, arrowcolor='#71849f'))
        hover_x.append((x0+x1)/2); hover_y.append((y0+y1)/2)
        hover_text.append(f'{source} → {target}<br>{data.get("sum_kzt", 0):,.0f} KZT · {data.get("n_tx", 0)} переводов')
    figure.add_trace(go.Scatter(x=xs, y=ys, mode='lines', line={'width': 1, 'color': '#495973'}, hoverinfo='skip', showlegend=False))
    figure.add_trace(go.Scatter(x=hover_x, y=hover_y, text=hover_text, mode='markers', marker={'size': 8, 'opacity': .01},
                                hovertemplate='%{text}<extra></extra>', showlegend=False))
    palette = ['#679eff', '#f7b955', '#49c9bd', '#ce8ff0', '#ff7f8a', '#96a7be', '#c2d66b']
    categories = sorted({str(attributes[n].get(color_by, 'peripheral')) for n in graph})
    for i, category in enumerate(categories):
        members = [n for n in graph if str(attributes[n].get(color_by, 'peripheral')) == category]
        color = COLORS.get(category, palette[(int(category) if category.lstrip('-').isdigit() else i) % len(palette)])
        text = []
        for n in members:
            a = attributes[n]
            text.append(f'{n}<br>{a.get("label", LABELS.get(a.get("role"), ""))}<br>Приоритет {a.get("priority_score", 0):.3f}')
        figure.add_trace(go.Scatter(
            x=[positions[n][0] for n in members], y=[positions[n][1] for n in members],
            mode='markers+text' if len(graph) <= 30 else 'markers',
            text=[str(n) for n in members], textposition='top center', textfont={'size': 10},
            hovertext=text, hovertemplate='%{hovertext}<extra></extra>', customdata=[[str(n)] for n in members],
            marker={'size': [24 if n == selected else 11 + 9*attributes[n].get('priority_score', 0) for n in members],
                    'color': color, 'line': {'width': [3 if n == selected else 1 for n in members], 'color': '#eef4ff'}},
            name=LABELS.get(category, f'Кластер {category}'), showlegend=len(categories) <= 12))
    figure.update_layout(annotations=arrows, title=title, height=520, margin={'l': 10, 'r': 10, 't': 45, 'b': 10},
                         paper_bgcolor='#101b2d', plot_bgcolor='#101b2d', font={'color': '#e6edf8'},
                         xaxis={'visible': False}, yaxis={'visible': False, 'scaleanchor': 'x'},
                         legend={'orientation': 'h', 'y': -.02}, hovermode='closest', dragmode='pan')
    return figure


def ego_figure(graph, nodes, gid, color_by='role'):
    attrs = nodes.set_index('gid').to_dict('index')
    return _figure(graph, attrs, color_by, gid, 'Связи выбранного клиента')


def overview_figure(bundle, visible_clusters=None, limit=100):
    clusters = bundle.clusters
    if visible_clusters is not None:
        clusters = clusters[clusters.cluster_id.isin(visible_clusters)]
    ordered = clusters.sort_values(['n_nodes', 'cluster_id'], ascending=[False, True])
    shown = ordered.head(limit)
    graph = nx.DiGraph()
    graph.add_nodes_from(str(c) for c in shown.cluster_id)
    membership = bundle.nodes.set_index('gid').cluster_id
    edges = bundle.edges.assign(sc=bundle.edges.src.map(membership), dc=bundle.edges.dst.map(membership))
    edges = edges[edges.sc.ne(edges.dc)]
    for (source, target), group in edges.groupby(['sc', 'dc']):
        if str(source) in graph and str(target) in graph:
            graph.add_edge(str(source), str(target), sum_kzt=group.sum_kzt.sum(), n_tx=int(group.n_tx.sum()))
    scores = bundle.nodes.groupby('cluster_id').priority_score.max()
    attrs = {str(row.cluster_id): {'cluster_id': row.cluster_id, 'priority_score': scores[row.cluster_id],
             'label': f'{row.n_nodes} узлов · {row.n_seed} seed · внутри {row.sum_kzt_internal:,.0f} KZT'}
             for row in shown.itertuples()}
    return _figure(graph, attrs, 'cluster_id', title='Сообщества и потоки между ними'), len(ordered)-len(shown), max(0, graph.number_of_edges()-350)
