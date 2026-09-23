"""Backend scenario matrix: routes, boundaries, data integrity and lifecycle."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from backend.models import ApiError
from backend.worker import Worker
from backend.core.results import load_bundle
from test_backend import api, upload_files, url  # Reuse isolated API fixture.


def assert_problem(response, status, code):
    assert response.status_code == status, response.text
    error = ApiError.model_validate(response.json())
    assert error.error.code == code
    assert error.request_id == response.headers['x-request-id']
    return error


RESULT_PATHS = [
    '/summary', '/nodes', '/nodes/{gid}', '/nodes/{gid}/edges',
    '/nodes/{gid}/graph', '/top', '/clusters', '/clusters/0',
    '/cluster-graph', '/selection', '/selection/export',
    '/exports/nodes_roles.csv', '/exports/clusters.csv', '/exports/top_nodes.csv',
]


def test_every_documented_operation_has_a_scenario(api):
    client, _, _, run, dataset, gids = api
    cluster = client.get(url(run, '/clusters')).json()['items'][0]['cluster_id']
    paths = [('/api/v1/health', {}), ('/api/v1/datasets', {}),
             ('/api/v1/datasets/' + dataset, {}),
             ('/api/v1/runs', {'dataset_id': dataset}), (url(run), {})]
    paths.extend((url(run, suffix.format(gid=gids[0]).replace('/clusters/0', f'/clusters/{cluster}')), {})
                 for suffix in RESULT_PATHS)
    for path, params in paths:
        response = client.get(path, params=params)
        assert response.status_code == 200, (path, response.text)
        assert response.headers['x-request-id']
    # Catalogue assertion catches new operations that need a new matrix entry.
    documented = {(method.upper(), path) for path, methods in client.get('/openapi.json').json()['paths'].items()
                  for method in methods if method in {'get', 'post', 'put', 'delete', 'patch'}}
    expected_get = {
        '/api/v1/health', '/api/v1/datasets', '/api/v1/datasets/{dataset_id}',
        '/api/v1/runs', '/api/v1/runs/{run_id}',
        *('/api/v1/runs/{run_id}' + suffix for suffix in RESULT_PATHS
          if not suffix.startswith('/exports/') and suffix != '/clusters/0'),
        '/api/v1/runs/{run_id}/clusters/{cluster_id}',
        '/api/v1/runs/{run_id}/exports/{filename}',
    }
    assert documented == {('GET', path) for path in expected_get} | {
        ('POST', '/api/v1/datasets'), ('POST', '/api/v1/runs'),
        ('PUT', '/api/v1/runs/{run_id}/selection'),
    }


@pytest.mark.parametrize('suffix', RESULT_PATHS)
def test_all_results_reject_queued_and_unknown_runs(api, suffix):
    client, store, _, _, dataset, gids = api
    run = store.create_run(dataset, str(uuid4()))['run_id']
    suffix = suffix.format(gid=gids[0])
    assert_problem(client.get(url(run, suffix)), 409, 'RUN_NOT_READY')
    assert_problem(client.get(url(str(uuid4()), suffix)), 404, 'NOT_FOUND')


@pytest.mark.parametrize('suffix', [
    '/nodes?offset=-1', '/nodes?limit=1.5', '/nodes?min_priority=nan',
    '/nodes?min_priority=inf', '/nodes?min_priority=-0.1', '/nodes?min_priority=1.1',
    '/nodes?cluster_id=-1', '/nodes?component_id=-1', '/nodes?at_boundary=True',
    '/nodes?sort=unknown', '/top?limit=201', '/clusters?offset=-1',
    '/clusters/-1', '/clusters/abc', '/cluster-graph?node_limit=101',
    '/cluster-graph?edge_limit=351', '/nodes/{gid}/edges?direction=sideways',
    '/nodes/{gid}/graph?hops=0', '/nodes/{gid}/graph?hops=3',
    '/nodes/{gid}/graph?node_limit=0', '/nodes/{gid}/graph?edge_limit=0',
    '/nodes/{gid}/graph?hops=1&hops=2', '/exports/unknown.csv',
])
def test_invalid_result_parameters(api, suffix):
    client, _, _, run, _, gids = api
    assert_problem(client.get(url(run, suffix.format(gid=gids[0]))), 422, 'INVALID_REQUEST')


def test_filters_and_sorts_match_exported_nodes(api):
    client, store, _, run, _, _ = api
    rows = load_bundle(store.get('runs', run)['bundle_path']).nodes.to_dict('records')
    scenarios = [('role', role) for role in {row['role'] for row in rows}]
    scenarios += [(key, value) for key in ('cluster_id', 'component_id') for value in {r[key] for r in rows}]
    scenarios += [(key, value) for key in ('is_seed', 'at_boundary') for value in (True, False)]
    for key, value in scenarios:
        params = {key: str(value).lower(), 'limit': 200, 'sort': 'gid_asc'}
        response = client.get(url(run, '/nodes'), params=params)
        assert response.status_code == 200, response.text
        expected = sorted((r['gid'] for r in rows if r[key] == value), key=int)
        assert [r['gid'] for r in response.json()['items']] == expected
        assert response.json()['total'] == len(expected)
    for sort, metric in [('priority_desc', 'priority_score'), ('role_score_desc', 'role_score'), ('gid_asc', None)]:
        expected = sorted(rows, key=lambda r: (-r[metric], int(r['gid'])) if metric else (int(r['gid']),))
        result = client.get(url(run, '/nodes'), params={'sort': sort, 'limit': 7, 'offset': 3}).json()
        assert [r['gid'] for r in result['items']] == [r['gid'] for r in expected[3:10]]
    roles = sorted({r['role'] for r in rows})[:2]
    params = [('role', role) for role in roles] + [('min_priority', '0.2'), ('is_seed', 'false'), ('limit', '200')]
    result = client.get(url(run, '/nodes'), params=params)
    assert result.status_code == 200, result.text
    assert {r['gid'] for r in result.json()['items']} == {
        r['gid'] for r in rows if r['role'] in roles and r['priority_score'] >= 0.2 and not r['is_seed']}


def test_edges_and_cluster_graph_preserve_direction_amount_and_count(api):
    client, store, _, run, _, gids = api
    bundle = load_bundle(store.get('runs', run)['bundle_path'])
    edges = bundle.edges.to_dict('records')
    for gid in gids:
        for direction in ('in', 'out', 'both'):
            expected = [r for r in edges if (direction in ('in', 'both') and r['dst'] == gid)
                        or (direction in ('out', 'both') and r['src'] == gid)]
            expected.sort(key=lambda r: (-r['sum_kzt'], int(r['src']), int(r['dst'])))
            result = client.get(url(run, f'/nodes/{gid}/edges'), params={'direction': direction, 'limit': 200})
            assert result.status_code == 200, result.text
            assert result.json()['items'] == expected
            assert result.json()['total'] == len(expected)
    cluster_of = dict(zip(bundle.nodes.gid, bundle.nodes.cluster_id))
    expected = defaultdict(lambda: [0.0, 0])
    for edge in edges:
        pair = (cluster_of[edge['src']], cluster_of[edge['dst']])
        if pair[0] != pair[1]:
            expected[pair][0] += edge['sum_kzt']
            expected[pair][1] += edge['n_tx']
    graph = client.get(url(run, '/cluster-graph')).json()
    assert len(graph['edges']) == len(expected)
    for edge in graph['edges']:
        amount, count = expected[(edge['src_cluster_id'], edge['dst_cluster_id'])]
        assert edge['sum_kzt'] == pytest.approx(amount)
        assert edge['n_tx'] == count
    assert sum(n['n_nodes'] for n in graph['nodes']) == len(gids)


@pytest.mark.parametrize('body', [{}, {'gids': None}, {'gids': '123'}, {'gids': [1]},
                                  {'gids': [None]}, {'gids': ['01']}, {'gids': [], 'extra': 1}])
def test_invalid_selection_never_overwrites_existing_value(api, body):
    client, _, _, run, _, gids = api
    path = url(run, '/selection')
    before = client.put(path, json={'gids': gids[:2]}).json()
    assert_problem(client.put(path, json=body), 422, 'INVALID_REQUEST')
    assert client.get(path).json() == before


def test_create_run_http_validation_and_conflicts(api):
    client, _, _, _, dataset, _ = api
    key = str(uuid4())
    headers = {'Idempotency-Key': key}
    for body in ({}, {'dataset_id': 1}, {'dataset_id': 'invalid'}, {'dataset_id': dataset, 'extra': True}):
        assert_problem(client.post('/api/v1/runs', json=body, headers=headers), 422, 'INVALID_REQUEST')
    assert_problem(client.post('/api/v1/runs', json={'dataset_id': dataset}), 422, 'INVALID_REQUEST')
    assert_problem(client.post('/api/v1/runs', content='{', headers={**headers, 'Content-Type': 'application/json'}),
                   422, 'INVALID_REQUEST')
    assert_problem(client.post('/api/v1/runs', json={'dataset_id': str(uuid4())}, headers=headers), 404, 'NOT_FOUND')
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.post('/api/v1/runs', json={'dataset_id': dataset}, headers=headers), range(4)))
    assert all(r.status_code == 202 for r in responses)
    run = responses[0].json()['run_id']
    assert {r.json()['run_id'] for r in responses} == {run}
    assert all(r.headers['location'] == url(run) for r in responses)
    assert_problem(client.post('/api/v1/runs', json={'dataset_id': str(uuid4())}, headers=headers), 409, 'IDEMPOTENCY_CONFLICT')
    error = assert_problem(client.post('/api/v1/runs', json={'dataset_id': dataset},
                                      headers={'Idempotency-Key': str(uuid4())}), 409, 'ACTIVE_RUN_EXISTS')
    assert error.error.related_run_id == run


@pytest.mark.parametrize(('field', 'value'), [('name', ''), ('name', '   '), ('name', 'x' * 121), ('profile', 'other')])
def test_invalid_upload_metadata_leaves_no_dataset(api, field, value):
    client, _, settings, _, _, _ = api
    before = client.get('/api/v1/datasets').json()['total']
    directories = set((settings.state / 'datasets').iterdir())
    metadata = {'name': 'Valid', 'profile': 'hackathon-v1', field: value}
    assert_problem(client.post('/api/v1/datasets', data=metadata, files=upload_files(settings.data)), 422, 'INVALID_REQUEST')
    assert client.get('/api/v1/datasets').json()['total'] == before
    assert set((settings.state / 'datasets').iterdir()) == directories


def test_upload_failure_cleans_partial_directory(api, monkeypatch):
    client, store, settings, _, _, _ = api
    before = set((settings.state / 'datasets').iterdir())

    def fail(*args, **kwargs):
        raise OSError('private storage failure')

    monkeypatch.setattr(store, 'add_dataset', fail)
    response = client.post('/api/v1/datasets', data={'name': 'Valid', 'profile': 'hackathon-v1'}, files=upload_files(settings.data))
    assert_problem(response, 500, 'INTERNAL_ERROR')
    assert 'private' not in response.text
    assert set((settings.state / 'datasets').iterdir()) == before


def test_changed_input_fails_run_without_replacing_previous_result(api):
    client, store, settings, old, dataset, _ = api
    run = store.create_run(dataset, str(uuid4()))['run_id']
    snapshot = Path(store.get('datasets', dataset)['path']) / 'nodes.parquet'
    snapshot.write_bytes(snapshot.read_bytes() + b'changed')
    assert Worker(settings).once()
    assert client.get(url(run)).json()['status'] == 'failed'
    assert_problem(client.get(url(run, '/summary')), 409, 'RUN_NOT_READY')
    assert client.get(url(old, '/summary')).status_code == 200
    assert not (settings.results / 'runs' / run).exists()


def test_export_headers_schema_and_full_coverage(api):
    client, _, _, run, _, gids = api
    for filename, minimum in [('nodes_roles.csv', len(gids)), ('clusters.csv', 1), ('top_nodes.csv', 20)]:
        response = client.get(url(run, '/exports/' + filename))
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/csv')
        assert response.headers['content-disposition'] == f'attachment; filename="{filename}"'
        frame = pd.read_csv(BytesIO(response.content), dtype={'gid': str})
        assert len(frame) >= minimum
        if filename == 'nodes_roles.csv':
            assert set(frame.gid) == set(gids) and frame.gid.is_unique


def test_empty_registry_and_unknown_resources(tmp_path):
    from fastapi.testclient import TestClient
    from backend.api import create_app
    from backend.config import Settings

    with TestClient(create_app(Settings(tmp_path / 'state', tmp_path / 'data', tmp_path / 'out', bootstrap=False))) as client:
        assert client.get('/api/v1/health').json() == {'status': 'ok'}
        assert client.get('/api/v1/datasets').json() == {'items': [], 'total': 0, 'limit': 50, 'offset': 0}
        assert_problem(client.get('/api/v1/datasets/' + str(uuid4())), 404, 'NOT_FOUND')
        assert_problem(client.get('/api/v1/runs', params={'dataset_id': str(uuid4())}), 404, 'NOT_FOUND')
        assert_problem(client.get('/api/v1/runs'), 422, 'INVALID_REQUEST')
        assert_problem(client.get('/api/v1/datasets/invalid'), 422, 'INVALID_REQUEST')
        assert_problem(client.delete('/api/v1/datasets'), 405, 'INVALID_REQUEST')
