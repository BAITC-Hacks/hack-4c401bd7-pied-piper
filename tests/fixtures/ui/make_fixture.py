"""Generate an explicitly synthetic UI contract fixture; never uses real client data."""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from pathlib import Path
import pandas as pd


def make_fixture(output: Path, data: Path | None = None):
    output.mkdir(parents=True, exist_ok=True)
    # Large IDs exercise lossless int64 handling above JavaScript's exact integer range.
    gids = [str(9007199254741000 + i) for i in range(24)]
    nodes = pd.DataFrame({'gid': gids, 'depth': [0, 1, 2, 3] + [1]*18 + [4, 0], 'is_seed': [True]+[False]*22+[True]})
    edges = pd.DataFrame([{'src': gids[0], 'dst': gid, 'sum_kzt': float((i+1)*10000), 'n_tx': 1, 'depth': 1}
                          for i, gid in enumerate([gids[1]] + gids[4:22])] +
                         [{'src': gids[a], 'dst': gids[b], 'sum_kzt': 10000., 'n_tx': 1, 'depth': depth}
                          for a, b, depth in [(1, 2, 2), (2, 3, 3), (3, 22, 4)]])
    metrics = nodes.copy()
    metrics['cluster_id'] = [0]*23+[1]
    metrics['component_id'] = [0]*23+[1]
    for field, endpoint, source, aggregation in (
        ('in_deg', 'dst', 'src', 'nunique'), ('out_deg', 'src', 'dst', 'nunique'),
        ('in_kzt', 'dst', 'sum_kzt', 'sum'), ('out_kzt', 'src', 'sum_kzt', 'sum'),
        ('in_tx', 'dst', 'n_tx', 'sum'), ('out_tx', 'src', 'n_tx', 'sum')):
        metrics[field] = metrics.gid.map(edges.groupby(endpoint)[source].agg(aggregation)).fillna(0)
    for field in ('in_deg', 'out_deg', 'in_tx', 'out_tx'):
        metrics[field] = metrics[field].astype(int)
    metrics['pagerank'] = 1/24
    roles = pd.DataFrame({'gid': gids, 'role': ['distributor']+['transit']*3+['terminal']*18+['peripheral']*2,
                          'role_score': [.8]*22+[.5, .5], 'cluster_id': metrics.cluster_id,
                          'priority_score': [round(.9-i*.02, 4) for i in range(24)],
                          'evidence': [f'Синтетический пример {i}: вход {int(r.in_deg)}, выход {int(r.out_deg)}.' for i, r in enumerate(metrics.itertuples())]})
    metrics['priority_structure'] = roles.priority_score * .5
    metrics['priority_magnitude'] = roles.priority_score * .5
    for role in ('consolidator', 'transit', 'distributor', 'terminal', 'coordinator', 'peripheral'):
        metrics[f'score_{role}'] = roles.role.eq(role).astype(float)*.8
    top = roles.head(20)[['gid', 'role', 'priority_score']].copy()
    top.insert(0, 'rank', range(1, 21))
    top['why'] = 'Тестовая очередь для проверки UI; не результат AML-анализа.'
    clusters = pd.DataFrame([
        {'cluster_id': 0, 'n_nodes': 23, 'n_seed': 1, 'sum_kzt_internal': edges.sum_kzt.sum(),
         'top_gids': '|'.join(gids[:5]), 'hypothesis': 'Синтетическое сообщество для проверки интерфейса.'},
        {'cluster_id': 1, 'n_nodes': 1, 'n_seed': 1, 'sum_kzt_internal': 0.,
         'top_gids': gids[-1], 'hypothesis': 'Синтетический изолированный seed.'},
    ])
    # Physical storage follows the int64 dataset contract; reader must preserve it as text for UI.
    for frame in (nodes, metrics, roles, top):
        frame['gid'] = frame.gid.astype('int64')
    for name in ('src', 'dst'):
        edges[name] = edges[name].astype('int64')
    for name, frame in [('nodes_roles.csv', roles), ('clusters.csv', clusters), ('top_nodes.csv', top)]:
        frame.to_csv(output/name, index=False)
    metrics.to_parquet(output/'node_metrics.parquet', index=False)
    edges.to_parquet(output/'edges.parquet', index=False)
    files = ['nodes_roles.csv', 'clusters.csv', 'top_nodes.csv', 'node_metrics.parquet', 'edges.parquet']
    manifest = {'schema_version': 1, 'data_kind': 'synthetic', 'n_nodes': 24, 'n_edges': len(edges),
                'n_transactions': len(edges), 'seed': 42, 'thresholds': {}, 'stage_runtimes': {},
                'warnings': ['Только синтетический пример для проверки UI.'],
                'output_sha256': {name: sha256((output/name).read_bytes()).hexdigest() for name in files}}
    (output/'run.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    if data:
        data.mkdir(parents=True, exist_ok=True)
        nodes.to_parquet(data/'nodes.parquet', index=False)
        edges.to_parquet(data/'edges.parquet', index=False)
    return gids


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('demo_outputs'))
    parser.add_argument('--data', type=Path, default=Path('demo_data'))
    args = parser.parse_args()
    make_fixture(args.out, args.data)
    print(f'SYNTHETIC: создан пример в {args.out}, исходные тестовые таблицы в {args.data}')
