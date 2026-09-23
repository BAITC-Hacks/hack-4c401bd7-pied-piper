"""Read-only UI adapter and directed graph views. No role calculations."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from backend.validate import SCHEMAS, ValidationError, columns, ids, numeric, read_csv, require, validate_frames

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


def load_bundle(directory: Path, candidate=False) -> Bundle:
    from backend.core.bundle_io import read_bundle_files
    from backend.core.contracts import OUTPUT_SCHEMAS
    from backend.validate import check_schema, check_metrics, SCORE_ATOL
    try:
        _, manifest, raw = read_bundle_files(directory, candidate=candidate)
        frames = [read_csv(BytesIO(raw[name]), name) for name in SCHEMAS]
        for frame, name in zip(frames, SCHEMAS):
            check_schema(frame, OUTPUT_SCHEMAS[name], name, csv=True)
        metrics = pd.read_parquet(BytesIO(raw['node_metrics.parquet']))
        edges = pd.read_parquet(BytesIO(raw['edges.parquet']))
        for name, frame in [('node_metrics.parquet',metrics), ('edges.parquet',edges)]:
            check_schema(frame, OUTPUT_SCHEMAS[name], name)
        roles, clusters, top, report = validate_frames(*frames, metrics[['gid','depth','is_seed']], edges, manifest['counts']['nodes'])
        for frame in (roles, metrics):
            require(frame.gid.map(int).tolist() == sorted(frame.gid.map(int)), 'Строки nodes/metrics должны быть отсортированы по числовому gid')
        require(clusters.cluster_id.tolist() == sorted(clusters.cluster_id), 'clusters: неверная сортировка')
        require(list(zip(edges.src,edges.dst)) == sorted(zip(edges.src,edges.dst)), 'edges: неверная сортировка')
        metrics['gid'] = ids(metrics.gid)
        for col in ('src','dst'):
            edges[col] = ids(edges[col],col)
        require(not metrics.gid.duplicated().any(), 'node_metrics: повторные gid')
        for name in SCHEMAS['nodes_roles.csv'][1:]:
            left = metrics.set_index('gid').loc[roles.gid,name].reset_index(drop=True)
            right = roles[name].reset_index(drop=True)
            if pd.api.types.is_numeric_dtype(right):
                require(np.allclose(left,right,rtol=0,atol=SCORE_ATOL), f'node_metrics: отличается {name}')
            else:
                require(left.astype(str).tolist()==right.astype(str).tolist(), f'node_metrics: отличается {name}')
        check_metrics(metrics, edges, manifest, report)
        return Bundle(metrics, edges, clusters, top, manifest, report, raw)
    except ValidationError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
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


def overview_graph(bundle, visible_clusters=None, limit=100):
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
    return graph, attrs, len(ordered)-len(shown)


def overview_figure(bundle, visible_clusters=None, limit=100):
    graph, attrs, hidden = overview_graph(bundle, visible_clusters, limit)
    return _figure(graph, attrs, 'cluster_id', title='Сообщества и потоки между ними'), hidden, max(0, graph.number_of_edges()-350)
