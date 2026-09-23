"""Independent CSV acceptance checks; does not import the role engine."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

ROLES = ('consolidator', 'transit', 'distributor', 'terminal', 'coordinator', 'peripheral')
SCHEMAS = {
    'nodes_roles.csv': ['gid', 'role', 'role_score', 'cluster_id', 'priority_score', 'evidence'],
    'clusters.csv': ['cluster_id', 'n_nodes', 'n_seed', 'sum_kzt_internal', 'top_gids', 'hypothesis'],
    'top_nodes.csv': ['rank', 'gid', 'role', 'priority_score', 'why'],
}


class ValidationError(ValueError):
    """An actionable input/output contract failure."""


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def columns(frame, required, label):
    missing = set(required) - set(frame.columns)
    require(not missing, f'{label}: отсутствуют колонки {sorted(missing)}')
    require(not frame[required].isna().any().any(), f'{label}: NULL в обязательных полях')
    for name in required:
        require(not frame[name].astype(str).str.strip().eq('').any(), f'{label}: пустое поле {name}')


def ids(values, label='gid'):
    """Canonical decimal int64 text: never convert client identifiers through float."""
    result = []
    for value in values:
        text = str(value)
        require(bool(re.fullmatch(r'-?\d+', text)), f'{label}: требуется целый int64, получено {text!r}')
        number = int(text)
        require(-(2**63) <= number < 2**63, f'{label}: вне диапазона int64')
        result.append(str(number))
    return pd.Series(result, index=values.index, dtype='string')


def numeric(frame, names, label, integer=False):
    for name in names:
        try:
            value = pd.to_numeric(frame[name], errors='raise')
        except (TypeError, ValueError) as exc:
            raise ValidationError(f'{label}: {name} должен быть числом') from exc
        require(np.isfinite(value).all(), f'{label}: {name} содержит NaN/Inf')
        if integer:
            require(value.eq(np.floor(value)).all(), f'{label}: {name} должен быть целым')
        frame[name] = value.astype('int64') if integer else value.astype(float)


def read_csv(source, name):
    try:
        frame = pd.read_csv(source, dtype=str, keep_default_na=False)
    except (ValueError, OSError, pd.errors.ParserError) as exc:
        raise ValidationError(f'{name}: не удалось прочитать CSV: {exc}') from exc
    columns(frame, SCHEMAS[name], name)
    return frame


def validate_frames(roles, clusters, top, nodes, edges, expected_nodes=2248):
    """Return normalized copies and diagnostics, independently recomputing graph facts."""
    roles, clusters, top, nodes, edges = (f.copy() for f in (roles, clusters, top, nodes, edges))
    for frame, name in ((roles, 'nodes_roles.csv'), (clusters, 'clusters.csv'), (top, 'top_nodes.csv')):
        columns(frame, SCHEMAS[name], name)
    columns(nodes, ['gid', 'depth', 'is_seed'], 'nodes')
    columns(edges, ['src', 'dst', 'sum_kzt', 'n_tx', 'depth'], 'edges')
    for frame in (roles, top, nodes):
        frame['gid'] = ids(frame.gid)
    for name in ('src', 'dst'):
        edges[name] = ids(edges[name], name)
    numeric(nodes, ['depth'], 'nodes', integer=True)
    require(nodes.depth.between(0, 4).all(), 'nodes: depth вне 0..4')
    seeds = nodes.is_seed.astype(str).str.lower()
    require(seeds.isin(['true', 'false', '0', '1']).all(), 'nodes: is_seed должен быть bool/0/1')
    nodes['is_seed'] = seeds.isin(['true', '1'])
    require(nodes.is_seed.eq(nodes.depth.eq(0)).all(), 'nodes: seed не согласован с depth=0')
    numeric(edges, ['sum_kzt'], 'edges')
    numeric(edges, ['n_tx', 'depth'], 'edges', integer=True)
    require(edges.sum_kzt.ge(5000).all() and edges.n_tx.gt(0).all(), 'edges: сумма <5000 или n_tx<=0')
    require(edges.depth.between(1, 4).all(), 'edges: depth вне 1..4')
    require(not edges.duplicated(['src', 'dst']).any(), 'edges: повторные направленные пары')
    require(len(nodes) > 0 and not nodes.gid.duplicated().any(), 'nodes: пустой набор или повторные gid')
    if expected_nodes:
        require(len(nodes) == expected_nodes, f'nodes: ожидалось {expected_nodes}, получено {len(nodes)}')
    node_ids = set(nodes.gid)
    require((set(edges.src) | set(edges.dst)) <= node_ids, 'edges: неизвестный gid')
    require(not roles.gid.duplicated().any() and set(roles.gid) == node_ids,
            'nodes_roles: gid должны точно покрывать входные nodes без повторов')
    require(roles.role.isin(ROLES).all(), 'nodes_roles: неизвестная роль')
    numeric(roles, ['role_score', 'priority_score'], 'nodes_roles')
    numeric(roles, ['cluster_id'], 'nodes_roles', integer=True)
    require(roles.cluster_id.ge(0).all(), 'nodes_roles: cluster_id должен быть >=0')
    for name in ('role_score', 'priority_score'):
        require(roles[name].between(0, 1).all(), f'nodes_roles: {name} вне [0,1]')
    require(roles.evidence.str.len().between(1, 200).all(), 'nodes_roles: evidence длиннее 200 символов')
    require(roles.evidence.str.contains(r'\d', regex=True).all(), 'nodes_roles: evidence должен содержать фактические числа')
    facts = roles[SCHEMAS['nodes_roles.csv']].merge(nodes[['gid', 'depth', 'is_seed']], on='gid', validate='one_to_one')
    require(not (facts.role.eq('terminal') & facts.depth.eq(4)).any(), 'depth=4: terminal запрещён')
    require(not (facts.role.eq('transit') & facts.is_seed).any(), 'seed: transit по неполному входу запрещён контрактом P0')
    out_degree = edges.groupby('src').dst.nunique()
    require(not (facts.role.eq('terminal') & facts.gid.map(out_degree).fillna(0).gt(0)).any(),
            'terminal: есть наблюдаемые исходящие связи')
    in_degree = edges.groupby('dst').src.nunique()
    require(not (facts.role.eq('terminal') & facts.gid.map(in_degree).fillna(0).eq(0)).any(),
            'terminal: нет входящих связей')
    numeric(clusters, ['cluster_id', 'n_nodes', 'n_seed'], 'clusters', integer=True)
    numeric(clusters, ['sum_kzt_internal'], 'clusters')
    require(len(clusters) > 0 and not clusters.cluster_id.duplicated().any(), 'clusters: пустые или повторные cluster_id')
    require(set(clusters.cluster_id) == set(roles.cluster_id), 'clusters: множество cluster_id не совпадает')
    require(clusters.n_nodes.gt(0).all() and clusters.n_seed.ge(0).all(), 'clusters: неверные размеры')
    membership = roles.set_index('gid').cluster_id
    mapped_edges = edges.assign(sc=edges.src.map(membership), dc=edges.dst.map(membership))
    internal = mapped_edges[mapped_edges.sc.eq(mapped_edges.dc)].groupby('sc').sum_kzt.sum()
    counts = facts.groupby('cluster_id').agg(size=('gid', 'size'), seeds=('is_seed', 'sum'))
    ranked = roles.assign(sort_gid=roles.gid.map(int)).sort_values(
        ['priority_score', 'sort_gid'], ascending=[False, True]).drop(columns='sort_gid')
    for row in clusters.itertuples():
        require(row.n_nodes == counts.loc[row.cluster_id, 'size'], f'cluster {row.cluster_id}: неверное n_nodes')
        require(row.n_seed == counts.loc[row.cluster_id, 'seeds'], f'cluster {row.cluster_id}: неверное n_seed')
        require(np.isclose(row.sum_kzt_internal, internal.get(row.cluster_id, 0), rtol=1e-9, atol=.01),
                f'cluster {row.cluster_id}: неверный внутренний оборот')
        listed = ids(pd.Series(str(row.top_gids).split('|')), 'top_gids').tolist()
        expected = ranked[ranked.cluster_id.eq(row.cluster_id)].head(5).gid.tolist()
        require(listed == expected, f'cluster {row.cluster_id}: top_gids не соответствуют Top-5 по priority')
    graph = nx.DiGraph()
    graph.add_nodes_from(node_ids)
    graph.add_edges_from(zip(edges.src, edges.dst))
    component = {gid: i for i, group in enumerate(nx.weakly_connected_components(graph)) for gid in group}
    require(roles.assign(component=roles.gid.map(component)).groupby('cluster_id').component.nunique().le(1).all(),
            'clusters: сообщество объединяет несвязанные компоненты')
    numeric(top, ['rank'], 'top_nodes', integer=True)
    numeric(top, ['priority_score'], 'top_nodes')
    require(len(top) >= min(20, len(nodes)), 'top_nodes: нужно минимум 20 строк (или все узлы малого тестового графа)')
    require(not top.gid.duplicated().any() and set(top.gid) <= node_ids, 'top_nodes: повторный/неизвестный gid')
    require(top['rank'].tolist() == list(range(1, len(top) + 1)), 'top_nodes: rank должен идти от 1 подряд')
    expected = ranked.head(len(top)).reset_index(drop=True)
    require(top.gid.tolist() == expected.gid.tolist(), 'top_nodes: неверная сортировка priority desc / числовой gid asc')
    require(top.role.tolist() == expected.role.tolist(), 'top_nodes: роль отличается от nodes_roles')
    require(np.allclose(top.priority_score, expected.priority_score, rtol=1e-9, atol=1e-9),
            'top_nodes: priority отличается от nodes_roles')
    warnings = []
    if roles.role.nunique() == 1:
        warnings.append('Все узлы имеют одну роль: требуется методологический разбор.')
    volume = nodes.gid.map(edges.groupby('src').sum_kzt.sum()).fillna(0) + nodes.gid.map(edges.groupby('dst').sum_kzt.sum()).fillna(0)
    volume_ids = nodes.assign(volume=volume, sort_gid=nodes.gid.map(int)).sort_values(
        ['volume', 'sort_gid'], ascending=[False, True]).head(20).gid
    overlap = len(set(volume_ids) & set(top.head(20).gid))
    if overlap >= min(18, len(nodes)):
        warnings.append('Top почти совпадает с Top по обороту: проверьте вклады других компонент.')
    report = {'status': 'ok', 'n_nodes': len(nodes), 'n_edges': len(edges), 'n_seed': int(nodes.is_seed.sum()),
              'n_clusters': len(clusters), 'n_top': len(top), 'n_components': nx.number_weakly_connected_components(graph),
              'n_isolates': nx.number_of_isolates(graph), 'role_distribution': roles.role.value_counts().to_dict(),
              'top20_volume_overlap': overlap, 'warnings': warnings}
    return roles, clusters, top, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--out', type=Path, default=Path('outputs'))
    parser.add_argument('--expected-nodes', type=int, default=2248)
    parser.add_argument('--bundle', action='store_true', help='Также проверить UI bundle и SHA256 manifest')
    args = parser.parse_args()
    try:
        frames = [read_csv(args.out / name, name) for name in SCHEMAS]
        nodes = pd.read_parquet(args.data / 'nodes.parquet')
        edges = pd.read_parquet(args.data / 'edges.parquet')
        *_, report = validate_frames(*frames, nodes, edges, args.expected_nodes)
        if args.bundle:
            from src.view import load_bundle
            bundle = load_bundle(args.out)
            require(set(bundle.nodes.gid) == set(ids(nodes.gid)), 'bundle: другие входные nodes')
            bundle_edges = bundle.edges.sort_values(['src', 'dst']).reset_index(drop=True)
            original = edges.copy()
            for key in ('src', 'dst'):
                original[key] = ids(original[key])
            pd.testing.assert_frame_equal(bundle_edges[original.columns], original.sort_values(['src', 'dst']).reset_index(drop=True), check_dtype=False)
            report['bundle'] = 'ok'
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (ValidationError, OSError, ValueError, KeyError, AssertionError) as exc:
        parser.exit(1, f'Ошибка проверки: {exc}\n')


if __name__ == '__main__':
    main()
