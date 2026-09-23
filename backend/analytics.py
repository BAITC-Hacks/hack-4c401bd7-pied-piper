"""Adapters over validated immutable bundles; never recompute roles in HTTP."""
import json
import networkx as nx
import pandas as pd

from backend import models
from backend.errors import ApiProblem
from src.contracts import PRIORITY_WEIGHTS, SUBSTANTIVE_ROLES
from src.results import load_bundle


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if value is None or pd.isna(value):
        return None
    return value.item() if hasattr(value, 'item') else value


def node_summary(row):
    result = {key: clean(row[key]) for key in models.NodeSummary.model_fields if key != 'warnings'}
    warnings = []
    for flag, code, message in (
        (row['at_boundary'], 'BOUNDARY_DEPTH', 'Depth=4: продолжение переводов неизвестно.'),
        (row['is_seed'], 'SEED_INFLOW_INCOMPLETE', 'Seed: входящие потоки неполны.'),
        (row['in_kzt'] == 0, 'ZERO_OBSERVED_INFLOW', 'Входящий поток не наблюдался; отношение не определено.'),
        (row['in_deg'] + row['out_deg'] == 0, 'ISOLATED_NODE', 'Наблюдаемых связей нет.'),
    ):
        if flag:
            warnings.append(dict(code=code, message=message))
    result['warnings'] = warnings
    return result


def page(items, query, run_id):
    return dict(run_id=run_id, items=items[query.offset:query.offset + query.limit],
                total=len(items), limit=query.limit, offset=query.offset)


class Analysis:
    def __init__(self, store, run_id):
        record = store.get('runs', run_id)
        if record['status'] != 'succeeded':
            raise ApiProblem(409, 'RUN_NOT_READY', 'Результат ещё не готов.')
        try:
            self.bundle = load_bundle(record['bundle_path'])
            if self.bundle.manifest['run_id'] != run_id:
                raise ValueError('Run mismatch')
        except (ValueError, OSError, TypeError):
            raise ApiProblem(503, 'RESULT_INVALID', 'Результат повреждён или не прошёл проверку.') from None
        self.run_id = run_id
        self.record = record
        self.nodes = {row['gid']: row for row in self.bundle.nodes.to_dict('records')}

    def node(self, gid):
        if gid not in self.nodes:
            raise ApiProblem(404, 'NOT_FOUND', 'Клиент не найден в этом расчёте.')
        return self.nodes[gid]

    def summary(self):
        m = self.bundle.manifest
        return dict(run_id=self.run_id, dataset_id=self.record['dataset_id'],
                    data_kind=m.get('data_kind', 'official'), schema_version=m['schema_version'],
                    methodology_version=m['versions']['methodology'], counts=m['counts'],
                    role_distribution=m['role_distribution'], boundary_nodes=int(self.bundle.nodes.at_boundary.sum()),
                    isolated_nodes=sum(r['in_deg'] + r['out_deg'] == 0 for r in self.nodes.values()),
                    warnings=[*m['warnings'], 'Наблюдаются исходящие ветви, до 4 колен, переводы от 5000 KZT.'],
                    priority_weights=m['priority_weights'], thresholds=m['thresholds'])

    def list_nodes(self, query):
        rows = list(self.nodes.values())
        for key in ('cluster_id', 'component_id', 'is_seed', 'at_boundary'):
            expected = getattr(query, key)
            if expected is not None:
                expected = expected == 'true' if key in ('is_seed', 'at_boundary') else expected
                rows = [r for r in rows if r[key] == expected]
        if query.role:
            rows = [r for r in rows if r['role'] in query.role]
        if query.min_priority is not None:
            rows = [r for r in rows if r['priority_score'] >= query.min_priority]
        key = {'priority_desc': 'priority_score', 'role_score_desc': 'role_score'}.get(query.sort)
        rows.sort(key=lambda r: (-r[key], int(r['gid'])) if key else (int(r['gid']),))
        return page([node_summary(r) for r in rows], query, self.run_id)

    def detail(self, gid):
        row = self.node(gid)
        return dict(**node_summary(row), run_id=self.run_id,
                    metrics=clean({k: row[k] for k in models.Metrics.model_fields}),
                    alternative_role=clean(row['alternative_role']), alternative_score=row['alternative_score'],
                    role_margin=row['role_margin'],
                    role_support={role: clean(dict(eligible=row[f'eligible_{role}'], score=row[f'score_{role}'],
                                                   ineligible_reason=row[f'ineligible_reason_{role}']))
                                  for role in SUBSTANTIVE_ROLES},
                    priority={key: dict(value=row[f'priority_{key}'], weight=weight,
                                        contribution=row[f'contribution_{key}'])
                              for key, weight in self.bundle.manifest['priority_weights'].items()})

    def top(self, query):
        items = [dict(**node_summary(self.node(r.gid)), rank=int(r.rank), why=r.why)
                 for r in self.bundle.top.itertuples(index=False)]
        return page(items, query, self.run_id)

    def clusters(self):
        rows = self.bundle.clusters.to_dict('records')
        component_of = {r['cluster_id']: r['component_id'] for r in self.nodes.values()}
        for row in rows:
            row['component_id'] = component_of[row['cluster_id']]
            row['top_gids'] = [value.strip() for value in str(row['top_gids']).split('|') if value.strip()]
        return sorted(rows, key=lambda r: (-r['n_nodes'], r['cluster_id']))

    def edges(self, gid, query):
        self.node(gid)
        items = [r for r in self.bundle.edges.to_dict('records')
                 if ((query.direction in ('in', 'both') and r['dst'] == gid)
                     or (query.direction in ('out', 'both') and r['src'] == gid))]
        items.sort(key=lambda r: (-r['sum_kzt'], int(r['src']), int(r['dst'])))
        return page(items, query, self.run_id)

    def graph(self, gid, query):
        self.node(gid)
        graph = nx.DiGraph()
        graph.add_nodes_from(self.nodes)
        graph.add_edges_from(zip(self.bundle.edges.src, self.bundle.edges.dst))
        distance = nx.single_source_shortest_path_length(graph.to_undirected(as_view=True), gid, cutoff=query.hops)
        ordered = sorted(distance, key=lambda n: (distance[n], -self.nodes[n]['priority_score'], int(n)))
        selected = ordered[:query.node_limit]
        chosen = set(selected)
        all_edges = [r for r in self.bundle.edges.to_dict('records') if r['src'] in distance and r['dst'] in distance]
        edges = [r for r in all_edges if r['src'] in chosen and r['dst'] in chosen]
        edges.sort(key=lambda r: (-r['sum_kzt'], int(r['src']), int(r['dst'])))
        edges = edges[:query.edge_limit]
        return dict(run_id=self.run_id, center_gid=gid, hops=query.hops,
                    nodes=[node_summary(self.nodes[n]) for n in selected], edges=edges,
                    truncation=truncation(len(ordered), len(all_edges), len(selected), len(edges), query))

    def cluster_graph(self, query):
        clusters = self.clusters()
        nodes = clusters[:query.node_limit]
        chosen = {r['cluster_id'] for r in nodes}
        aggregated = {}
        for r in self.bundle.edges.itertuples(index=False):
            src, dst = self.nodes[r.src]['cluster_id'], self.nodes[r.dst]['cluster_id']
            if src == dst:
                continue
            edge = aggregated.setdefault((src, dst), dict(src_cluster_id=src, dst_cluster_id=dst, sum_kzt=0.0, n_tx=0))
            edge['sum_kzt'] += r.sum_kzt
            edge['n_tx'] += r.n_tx
        edges = sorted((e for e in aggregated.values() if e['src_cluster_id'] in chosen and e['dst_cluster_id'] in chosen),
                       key=lambda e: (-e['sum_kzt'], e['src_cluster_id'], e['dst_cluster_id']))[:query.edge_limit]
        return dict(run_id=self.run_id, nodes=nodes, edges=edges,
                    truncation=truncation(len(clusters), len(aggregated), len(nodes), len(edges), query))


def truncation(total_nodes, total_edges, shown_nodes, shown_edges, query):
    return dict(total_nodes=total_nodes, total_edges=total_edges, hidden_nodes=total_nodes - shown_nodes,
                hidden_edges=total_edges - shown_edges, node_limit=query.node_limit, edge_limit=query.edge_limit)
