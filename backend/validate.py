"""Independent CSV acceptance checks; does not import the role engine."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from backend.core.contracts import (ROLES, SUBSTANTIVE_ROLES, OUTPUT_SCHEMAS, INPUT_SCHEMAS,
                           PRIORITY_WEIGHTS, SCORE_ATOL, SUM_RTOL, SUM_ATOL_KZT)
from backend.core.bundle_io import ValidationError, require

VALIDATOR_VERSION = '1.0.0'
SCHEMAS = {name: [f.name for f in schema] for name, schema in OUTPUT_SCHEMAS.items() if name.endswith('.csv')}


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
        require(1 <= len(listed) <= min(5, row.n_nodes) and listed == expected[:len(listed)], f'cluster {row.cluster_id}: top_gids не соответствуют Top-5 по priority')
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
    require(np.allclose(top.priority_score, expected.priority_score, rtol=0, atol=SCORE_ATOL),
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


def check_schema(frame, schema, label, csv=False):
    required = [field.name for field in schema]
    require(list(frame.columns[:len(required)]) == required, f'{label}: неверный порядок или отсутствуют обязательные колонки')
    for field in schema:
        values = frame[field.name]
        if not field.nullable:
            require(not values.isna().any(), f'{label}: NULL в {field.name}')
        present = values.dropna()
        if field.dtype == 'string':
            require(present.map(lambda v: isinstance(v, str) and bool(v.strip())).all(), f'{label}: пустое/нестроковое поле {field.name}')
        elif field.dtype.startswith('int'):
            if csv:
                converted = ids(present, field.name).map(int)
                frame[field.name] = converted.astype(field.dtype)
            else:
                require(pd.api.types.is_integer_dtype(values.dtype) and values.dtype.kind == 'i' and values.dtype.itemsize == int(field.dtype[3:])//8,
                        f'{label}: {field.name} должен иметь тип {field.dtype}, получено {values.dtype}')
        elif field.dtype == 'float64':
            if csv:
                numeric(frame, [field.name], label)
            else:
                require(pd.api.types.is_float_dtype(values.dtype) and values.dtype.itemsize == 8, f'{label}: {field.name} должен быть float64')
            require(np.isfinite(frame[field.name].dropna().to_numpy(dtype=float)).all(), f'{label}: {field.name} содержит NaN/Inf')
        elif field.dtype == 'bool':
            require(pd.api.types.is_bool_dtype(values.dtype), f'{label}: {field.name} должен быть bool')
        elif field.dtype == 'date':
            dates = pd.to_datetime(values, errors='raise')
            require(dates.notna().all() and dates.eq(dates.dt.normalize()).all(), f'{label}: ожидаются даты без времени')
            require(dates.between('2026-07-01', '2026-07-31').all(), f'{label}: дата вне июля 2026')


def close(actual, expected, label, money=False):
    require(np.allclose(np.asarray(actual, dtype=float), np.asarray(expected, dtype=float),
                        rtol=SUM_RTOL if money else 0, atol=SUM_ATOL_KZT if money else SCORE_ATOL), label)


def check_metrics(metrics, edges, manifest, report):
    """Independent graph identities, role confidence and priority arithmetic."""
    f = metrics.set_index('gid')
    graph = nx.DiGraph()
    graph.add_nodes_from(sorted(f.index, key=int))
    graph.add_edges_from(zip(edges.src, edges.dst))
    for name, endpoint, source, operation in (
        ('in_deg','dst','src','nunique'), ('out_deg','src','dst','nunique'),
        ('in_kzt','dst','sum_kzt','sum'), ('out_kzt','src','sum_kzt','sum'),
        ('in_tx','dst','n_tx','sum'), ('out_tx','src','n_tx','sum')):
        expected = metrics.gid.map(edges.groupby(endpoint)[source].agg(operation)).fillna(0)
        close(metrics[name], expected, f'node_metrics: {name} не соответствует edges', money='kzt' in name)
    for field in ('in_deg','out_deg','in_tx','out_tx','in_kzt','out_kzt','pagerank','betweenness','cross_cluster_degree'):
        require(f[field].ge(0).all(), f'node_metrics: отрицательный {field}')
    require(f.pass_through.isna().eq(f.in_kzt.eq(0)).all(), 'pass_through: NULL должен быть ровно при нулевом входе')
    has_input = f.in_kzt.gt(0)
    close(f.loc[has_input, 'pass_through'], (f.out_kzt/f.in_kzt)[has_input], 'pass_through: неверное отношение')
    for field, expected in [('at_boundary', f.depth.eq(4)), ('truncated_by_depth', f.depth.eq(4)&f.out_deg.eq(0)),
                            ('ratio_usable', has_input & ~f.is_seed & f.depth.lt(4))]:
        require(f[field].eq(expected).all(), f'{field}: неверный флаг')
    groups = sorted(nx.weakly_connected_components(graph), key=lambda c: (-len(c), min(map(int,c))))
    membership = {gid:i for i, group in enumerate(groups) for gid in group}
    require(all(f.loc[gid,'component_id']==cid for gid,cid in membership.items()), 'component_id: неверная связность или нумерация')
    communities = [set(group.index) for _,group in f.groupby('cluster_id')]
    communities.sort(key=lambda c: (-len(c), min(map(int,c))))
    require(all(f.loc[gid,'cluster_id']==cid for cid,group in enumerate(communities) for gid in group), 'cluster_id: неверная нумерация')
    seeds = f.index[f.is_seed].tolist()
    distances = nx.multi_source_dijkstra_path_length(graph, seeds, weight=None) if seeds else {}
    expected_distance = pd.Series(distances).reindex(f.index)
    require(f.seed_distance.isna().equals(expected_distance.isna()), 'seed_distance: неверные NULL')
    close(f.seed_distance.fillna(-1), expected_distance.fillna(-1), 'seed_distance: неверное расстояние')
    neighbors = {gid:set(graph.predecessors(gid))|set(graph.successors(gid)) for gid in f.index}
    cross = pd.Series({gid:sum(f.loc[v,'cluster_id']!=f.loc[gid,'cluster_id'] for v in values) for gid,values in neighbors.items()}).reindex(f.index)
    close(f.cross_cluster_degree, cross, 'cross_cluster_degree: неверное число соседей')
    fractions = pd.Series({gid:cross[gid]/max(1,len(v)) for gid,v in neighbors.items()}).reindex(f.index)
    close(f.bridge_fraction, fractions, 'bridge_fraction: неверная доля')
    normalized = {}
    for key, scale in manifest['normalization_scales'].items():
        if key not in f and key != 'turnover_kzt':
            continue
        values = f.in_kzt+f.out_kzt if key=='turnover_kzt' else f[key]
        logged = np.log1p(values.astype(float))
        expected_scale = float(logged[logged.gt(0)].quantile(.95)) if logged.gt(0).any() else 0.
        close([scale], [expected_scale], f'normalization_scales: неверный {key}')
        normalized[key] = (logged/scale).clip(0,1) if scale else logged*0
    close(f.bridge, fractions*normalized['cross_cluster_degree'], 'bridge: неверный показатель')
    for role in ROLES:
        require(f[f'score_{role}'].between(0,1).all(), f'score_{role}: вне [0,1]')
    for role in SUBSTANTIVE_ROLES:
        eligible = f[f'eligible_{role}']
        reason = f[f'ineligible_reason_{role}']
        require(reason.isna().eq(eligible).all(), f'{role}: причина недопуска не согласована с eligible')
        require(f.loc[~eligible,f'score_{role}'].eq(0).all(), f'{role}: score недопустимой роли должен быть 0')
    for row in f.itertuples():
        eligible = [r for r in SUBSTANTIVE_ROLES if getattr(row,f'eligible_{r}')]
        eligible.sort(key=lambda r: -getattr(row,f'score_{r}'))  # stable contract order
        chosen = eligible[0] if eligible else 'peripheral'
        require(row.role == chosen, f'{row.Index}: role не соответствует допустимым score')
        alternative = eligible[1] if len(eligible)>1 else None
        require((alternative is None and pd.isna(row.alternative_role)) or row.alternative_role == alternative,
                f'{row.Index}: неверная alternative_role')
        second = getattr(row,f'score_{alternative}') if alternative else 0.
        first = getattr(row,f'score_{chosen}')
        close([row.score_peripheral], [float(not eligible)], 'score_peripheral: неверное значение')
        expected_score = first*(1-.5*second)
        if row.at_boundary or row.in_deg+row.out_deg == 0:
            expected_score = min(.5,expected_score)
        close([row.role_score,row.alternative_score,row.role_margin],
              [expected_score,second,first-second if eligible else 0.], f'{row.Index}: неверная уверенность/неоднозначность')
    expected_priority = {
        'structure': .6*normalized['betweenness']+.25*normalized['pagerank']+.15*f.bridge,
        'seed_proximity': expected_distance.map(lambda d: 1/(1+d) if pd.notna(d) else 0),
        'magnitude': normalized['turnover_kzt'],
        'role_support': f.role_score.where(f.role.ne('peripheral'),0),
    }
    for key, weight in PRIORITY_WEIGHTS.items():
        require(f[f'priority_{key}'].between(0,1).all(), f'priority_{key}: вне [0,1]')
        close(f[f'priority_{key}'], expected_priority[key], f'priority_{key}: неверная компонента')
        close(f[f'contribution_{key}'], weight*f[f'priority_{key}'], f'contribution_{key}: неверный вклад')
    close(f.priority_score, f[[f'contribution_{k}' for k in PRIORITY_WEIGHTS]].sum(axis=1), 'priority_score: не равен сумме вкладов')
    actual_counts = {'nodes':len(f), 'edges':len(edges), 'transactions':int(edges.n_tx.sum()), 'seeds':int(f.is_seed.sum()),
                     'components':len(groups), 'clusters':f.cluster_id.nunique()}
    require(all(manifest['counts'][k] == v for k,v in actual_counts.items()), 'counts: manifest не согласован с данными')
    require(manifest['role_distribution'] == {role:int(f.role.eq(role).sum()) for role in ROLES}, 'role_distribution: manifest не согласован с ролями')


def check_inputs(bundle, data, expected_nodes):
    from hashlib import sha256
    from io import BytesIO
    tables = {}
    for name, schema in INPUT_SCHEMAS.items():
        raw = (data/name).read_bytes()
        require(sha256(raw).hexdigest() == bundle.manifest['input_sha256'][name], f'{name}: входной SHA256 не совпадает')
        frame = pd.read_parquet(BytesIO(raw))
        check_schema(frame, schema, name)
        tables[name] = frame
    nodes, edges, tx = (tables[name] for name in INPUT_SCHEMAS)
    require(expected_nodes > 0, '--expected-nodes должен быть >0')
    require(len(nodes) == expected_nodes, f'nodes: ожидалось {expected_nodes}, получено {len(nodes)}')
    if expected_nodes != 2248:
        require(bundle.manifest.get('data_kind') == 'synthetic', 'Малый набор должен быть явно помечен synthetic')
    else:
        require(bundle.manifest.get('data_kind') != 'synthetic', 'Synthetic не является официальным результатом')
        require(len(edges)==3119 and len(tx)==4840 and int(nodes.is_seed.sum())==81, 'Официальный набор: неверные counts')
    require(not nodes.gid.duplicated().any(), 'nodes: повторные gid')
    raw_nodes = nodes.assign(gid=ids(nodes.gid)).set_index('gid').sort_index()
    stored = bundle.nodes.set_index('gid')[['depth','is_seed']].sort_index()
    pd.testing.assert_frame_equal(raw_nodes[['depth','is_seed']], stored, check_dtype=False)
    for key in ('src','dst'):
        edges[key] = ids(edges[key],key)
    pd.testing.assert_frame_equal(edges.sort_values(['src','dst']).reset_index(drop=True),
                                  bundle.edges[edges.columns].sort_values(['src','dst']).reset_index(drop=True), check_dtype=False)
    require(tx.sum_kzt.ge(5000).all(), 'transactions: сумма ниже 5000 KZT')
    require((set(tx.src)|set(tx.dst)) <= set(nodes.gid), 'transactions: неизвестные gid')
    agg = tx.groupby(['src','dst']).agg(amount=('sum_kzt','sum'), count=('sum_kzt','size')).reset_index()
    for key in ('src','dst'):
        agg[key] = ids(agg[key],key)
    merged = edges.merge(agg,on=['src','dst'],how='outer',indicator=True)
    require(merged['_merge'].eq('both').all(), 'transactions: пары не совпадают с edges')
    close(merged.sum_kzt,merged.amount,'transactions: суммы не совпадают с edges',money=True)
    require(merged.n_tx.eq(merged['count']).all(), 'transactions: n_tx не совпадает с edges')


def main():
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--out', type=Path, default=Path('outputs'))
    parser.add_argument('--expected-nodes', type=int, default=2248)
    parser.add_argument('--bundle', action='store_true', help='Совместимость CLI: bundle теперь проверяется всегда')
    parser.add_argument('--candidate', action='store_true', help='Проверить staging/candidate.json до публикации')
    args = parser.parse_args()
    try:
        from backend.core.results import load_bundle
        bundle = load_bundle(args.out, candidate=args.candidate)
        check_inputs(bundle, args.data, args.expected_nodes)
        if args.candidate:
            print(json.dumps(bundle.report, ensure_ascii=False), file=sys.stderr)
            print(json.dumps({'status': 'passed', 'validator_version': VALIDATOR_VERSION}))
        else:
            print(json.dumps({**bundle.report, 'status': 'passed', 'validator_version': VALIDATOR_VERSION, 'bundle': 'ok'}, ensure_ascii=False, indent=2))
    except (ValidationError, OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
        parser.exit(1, f'Ошибка проверки: {exc}\n')


if __name__ == '__main__':
    main()
