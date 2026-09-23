from hashlib import sha256
import json
import subprocess
import sys

import pandas as pd
import pytest

from frontend.view import load_bundle, make_graph, select_ego, ego_figure, overview_figure
from backend.validate import ValidationError


def rewrite_csv(directory, name, change):
    frame = pd.read_csv(directory/name, dtype={'gid': str, 'top_gids': str})
    change(frame)
    frame.to_csv(directory/name, index=False)
    resign(directory, name)


def resign(directory, name):
    manifest = json.loads((directory/'run.json').read_text())
    manifest['output_sha256'][name] = sha256((directory/name).read_bytes()).hexdigest()
    (directory/'run.json').write_text(json.dumps(manifest))


def test_valid_bundle_and_big_gid(sample):
    out, _, gids = sample
    bundle = load_bundle(out)
    assert bundle.nodes.gid.tolist() == gids
    assert bundle.report['n_isolates'] == 1
    assert bundle.report['n_components'] == 2
    assert len(bundle.top) == 20


def test_hash_mismatch_rejected(sample):
    out, _, _ = sample
    with (out/'nodes_roles.csv').open('a') as f:
        f.write('\n')
    with pytest.raises(ValidationError, match='SHA256'):
        load_bundle(out)


@pytest.mark.parametrize(('name', 'column', 'index', 'value', 'error'), [
    ('nodes_roles.csv', 'role', 22, 'terminal', 'depth=4'),
    ('nodes_roles.csv', 'role', 0, 'transit', 'seed'),
    ('nodes_roles.csv', 'role', 0, 'unknown', 'неизвестная роль'),
    ('nodes_roles.csv', 'role_score', 0, 1.1, r'\[0,1\]'),
    ('nodes_roles.csv', 'priority_score', 0, float('inf'), 'NaN/Inf'),
    ('nodes_roles.csv', 'evidence', 0, '', 'пустое поле'),
    ('nodes_roles.csv', 'evidence', 0, '1'*201, '200'),
    ('clusters.csv', 'n_nodes', 0, 24, 'n_nodes'),
    ('clusters.csv', 'n_seed', 0, 2, 'n_seed'),
    ('clusters.csv', 'sum_kzt_internal', 0, 0, 'оборот'),
    ('clusters.csv', 'top_gids', 0, '9007199254741023', 'top_gids'),
    ('top_nodes.csv', 'priority_score', 0, .1, 'priority отличается'),
    ('top_nodes.csv', 'rank', 0, 2, 'rank'),
])
def test_semantic_corruption_rejected(sample, name, column, index, value, error):
    out, _, _ = sample
    rewrite_csv(out, name, lambda f: f.__setitem__(column, f[column].astype(object).mask(f.index == index, value)))
    with pytest.raises(ValidationError, match=error):
        load_bundle(out)


def test_missing_node_rejected(sample):
    out, _, _ = sample
    rewrite_csv(out, 'nodes_roles.csv', lambda f: f.drop(index=23, inplace=True))
    with pytest.raises(ValidationError, match='покрывать'):
        load_bundle(out)


def test_duplicate_node_rejected(sample):
    out, _, gids = sample
    rewrite_csv(out, 'nodes_roles.csv', lambda f: f.loc.__setitem__((1, 'gid'), gids[0]))
    with pytest.raises(ValidationError, match='покрывать'):
        load_bundle(out)


def test_metric_mismatch_rejected(sample):
    out, _, _ = sample
    metrics = pd.read_parquet(out/'node_metrics.parquet')
    metrics.loc[0, 'out_kzt'] = 1
    metrics.to_parquet(out/'node_metrics.parquet', index=False)
    resign(out, 'node_metrics.parquet')
    with pytest.raises(ValidationError, match='out_kzt'):
        load_bundle(out)


def test_additional_columns_allowed(sample):
    out, _, _ = sample
    metrics = pd.read_parquet(out/'node_metrics.parquet')
    rewrite_csv(out, 'nodes_roles.csv', lambda f: f.__setitem__('depth', metrics.depth))
    assert len(load_bundle(out).nodes) == 24


def test_directed_ego_includes_isolate_and_hops(sample):
    out, _, gids = sample
    bundle = load_bundle(out)
    graph = make_graph(bundle.nodes, bundle.edges)
    isolated, hidden = select_ego(graph, gids[-1], bundle.nodes)
    assert list(isolated) == [gids[-1]] and hidden == 0
    one, _ = select_ego(graph, gids[2], bundle.nodes, hops=1)
    two, _ = select_ego(graph, gids[2], bundle.nodes, hops=2)
    assert gids[0] not in one and gids[0] in two
    assert one.has_edge(gids[1], gids[2]) and not one.has_edge(gids[2], gids[1])
    limited, hidden = select_ego(graph, gids[0], bundle.nodes, limit=3)
    assert len(limited) == 3 and hidden > 0 and gids[0] in limited
    fig = ego_figure(one, bundle.nodes, gids[2])
    assert len(fig.layout.annotations) == one.number_of_edges()
    assert any(trace.customdata is not None for trace in fig.data)
    fig, hidden, _ = overview_figure(bundle)
    assert hidden == 0
    with pytest.raises(ValidationError, match='не найден'):
        select_ego(graph, '0', bundle.nodes)


def test_cli_success_and_real_count_gate(sample):
    out, data, _ = sample
    args = [sys.executable, '-m', 'backend.validate', '--data', str(data), '--out', str(out), '--bundle']
    result = subprocess.run(args+['--expected-nodes', '24'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['bundle'] == 'ok'
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 1 and '2248' in result.stderr
