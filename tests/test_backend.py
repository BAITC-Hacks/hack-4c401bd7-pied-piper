"""HTTP contracts, durable lifecycle and recovery, independent of frontend."""
from concurrent.futures import ThreadPoolExecutor
from codecs import BOM_UTF8
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pandas as pd
import pytest

from backend.api import create_app
from backend.config import Settings
from backend.datasets import dataset_document, hashes
from backend.errors import ApiProblem
from backend.store import Store
from backend.worker import Worker
from backend.core.results import load_bundle


@pytest.fixture
def api(sample, tmp_path):
    run, data, gids = sample
    # UI fixture permits text dates; pipeline input requires calendar dates.
    tx_path = data / 'transactions.parquet'
    tx = pd.read_parquet(tx_path)
    tx['date'] = pd.to_datetime(tx.date).dt.date
    tx.to_parquet(tx_path, index=False)
    manifest = json.loads((run / 'run.json').read_text(encoding='utf-8'))
    manifest['input_sha256']['transactions.parquet'] = sha256(tx_path.read_bytes()).hexdigest()
    (run / 'run.json').write_text(json.dumps(manifest), encoding='utf-8')
    settings = Settings(tmp_path / 'state', data, run)
    app = create_app(settings)
    with TestClient(app) as client:
        datasets = client.get('/api/v1/datasets').json()['items']
        yield client, app.state.store, settings, run.name, datasets[0]['dataset_id'], gids


def url(run, suffix=''):
    return f'/api/v1/runs/{run}{suffix}'


def test_bootstrap_schema_and_summary(api):
    client, store, settings, run, dataset, gids = api
    assert client.get('/api/v1/health').json() == {'status': 'ok'}
    result = client.get(url(run, '/summary'))
    assert result.status_code == 200, result.text
    assert result.json()['counts']['nodes'] == 24
    assert result.json()['data_kind'] == 'synthetic'
    assert result.headers['x-request-id']
    assert client.get('/openapi.json').status_code == 200
    with TestClient(create_app(settings)) as other:
        assert other.get('/api/v1/datasets').json()['total'] == 1
        assert other.get('/api/v1/runs', params={'dataset_id': dataset}).json()['total'] == 1


def test_node_details_ids_and_warnings(api):
    client, _, _, run, _, gids = api
    nodes = client.get(url(run, '/nodes')).json()['items']
    assert len(nodes) == 24
    assert all(isinstance(n['gid'], str) and int(n['gid']) > 2**53 for n in nodes)
    for gid in (gids[0], gids[-1], gids[-2]):
        response = client.get(url(run, f'/nodes/{gid}'))
        assert response.status_code == 200, response.text
        node = response.json()
        assert node['gid'] == gid
        assert sum(v['contribution'] for v in node['priority'].values()) == pytest.approx(node['priority_score'])
        codes = {w['code'] for w in node['warnings']}
        if gid == gids[-1]:
            assert {'ISOLATED_NODE', 'SEED_INFLOW_INCOMPLETE'} <= codes
            assert node['metrics']['pass_through'] is None
        if gid == gids[-2]:
            assert 'BOUNDARY_DEPTH' in codes
    assert client.get(url(run, '/nodes/123')).status_code == 404


@pytest.mark.parametrize('suffix', ['/nodes?unknown=1', '/nodes?is_seed=1', '/nodes?role=bad',
                                    '/nodes?limit=0', '/nodes?limit=201', '/nodes?limit=2&limit=3',
                                    '/summary?x=1', '/nodes/09007199254741000'])
def test_query_contract(api, suffix):
    response = api[0].get(url(api[3], suffix))
    assert response.status_code == 422, response.text
    assert response.json()['error']['code'] == 'INVALID_REQUEST'
    assert response.json()['request_id'] == response.headers['x-request-id']


def test_filters_paging_and_top(api):
    client, _, _, run, _, _ = api
    response = client.get(url(run, '/nodes?is_seed=true&sort=gid_asc&limit=1'))
    assert response.status_code == 200, response.text
    assert response.json()['total'] == 2
    assert len(response.json()['items']) == 1
    assert client.get(url(run, '/nodes?offset=100')).json()['items'] == []
    assert client.get(url(run, '/nodes?min_priority=1')).json()['total'] == 0
    top = client.get(url(run, '/top?limit=5&offset=5')).json()
    assert [r['rank'] for r in top['items']] == list(range(6, 11))
    for item in top['items']:
        assert item['why']


def test_graphs_edges_and_clusters(api):
    client, _, _, run, _, gids = api
    graph = client.get(url(run, f'/nodes/{gids[0]}/graph?hops=2&node_limit=2&edge_limit=1'))
    assert graph.status_code == 200, graph.text
    graph = graph.json()
    chosen = {n['gid'] for n in graph['nodes']}
    assert gids[0] in chosen
    assert len(graph['nodes']) == 2 and len(graph['edges']) == 1
    assert all(e['src'] in chosen and e['dst'] in chosen for e in graph['edges'])
    assert graph['truncation']['hidden_nodes'] == graph['truncation']['total_nodes'] - 2
    assert graph['truncation']['hidden_edges'] == graph['truncation']['total_edges'] - 1
    edges = client.get(url(run, f'/nodes/{gids[0]}/edges?direction=out')).json()
    assert edges['total'] > 1
    assert all(e['src'] == gids[0] for e in edges['items'])
    isolate = client.get(url(run, f'/nodes/{gids[-1]}/graph')).json()
    assert len(isolate['nodes']) == 1 and isolate['edges'] == []
    clusters = client.get(url(run, '/clusters'))
    assert clusters.status_code == 200, clusters.text
    assert isinstance(clusters.json()['items'][0]['top_gids'], list)
    cgraph = client.get(url(run, '/cluster-graph?node_limit=1&edge_limit=1'))
    assert cgraph.status_code == 200, cgraph.text
    assert cgraph.json()['truncation']['total_nodes'] == clusters.json()['total']


def test_selection_is_persistent_atomic_and_exports(api):
    client, store, settings, run, _, gids = api
    path = url(run, '/selection')
    assert client.get(path).json()['gids'] == []
    selected = [gids[1], gids[0]]
    assert client.put(path, json={'gids': [*selected, gids[1]]}).json()['gids'] == selected
    assert client.put(path, json={'gids': [gids[0], '123']}).status_code == 422
    assert client.put(path, json={'gids': [int(gids[0])]}).status_code == 422
    assert client.get(path).json()['gids'] == selected
    with TestClient(create_app(settings)) as other:
        assert other.get(path).json()['gids'] == selected
    exported = client.get(path + '/export')
    assert exported.content.startswith(BOM_UTF8)
    frame = pd.read_csv(BytesIO(exported.content), dtype={'gid': str})
    assert frame.gid.tolist() == selected
    bundle = load_bundle(store.get('runs', run)['bundle_path'])
    for name in ('nodes_roles.csv', 'clusters.csv', 'top_nodes.csv'):
        response = client.get(url(run, '/exports/' + name))
        assert response.status_code == 200
        assert response.content.startswith(BOM_UTF8)
        assert response.content.decode('utf-8-sig') == bundle.raw[name].decode('utf-8-sig')
    assert client.get(url(run, '/exports/run.json')).status_code == 422
    client.put(path, json={'gids': []})
    assert len(pd.read_csv(BytesIO(client.get(path + '/export').content))) == 0


def test_bad_bundle_is_not_replaced_with_old_result(api):
    client, store, _, run, _, _ = api
    path = Path(store.get('runs', run)['bundle_path']) / 'nodes_roles.csv'
    path.write_bytes(path.read_bytes() + b'\n')
    response = client.get(url(run, '/nodes'))
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'RESULT_INVALID'
    assert str(path.parent) not in response.text


def test_idempotency_and_concurrent_claims(api):
    client, store, _, run, dataset, _ = api
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.create_run(dataset, key), range(2)))
    identifier = results[0]['run_id']
    assert results[1]['run_id'] == identifier
    assert client.get(url(identifier, '/nodes')).status_code == 409
    with pytest.raises(ApiProblem) as conflict:
        store.create_run(dataset, str(uuid4()))
    assert conflict.value.body['related_run_id'] == identifier
    with pytest.raises(ApiProblem) as conflict:
        store.create_run(str(uuid4()), key)
    assert conflict.value.body['code'] == 'IDEMPOTENCY_CONFLICT'
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda owner: store.claim('runs', owner), ['a', 'b']))
    assert sum(r is not None for r in claimed) == 1


def test_worker_calculates_same_id_and_preserves_selection(api):
    client, store, settings, old, dataset, gids = api
    key = str(uuid4())
    response = client.post('/api/v1/runs', json={'dataset_id': dataset}, headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    run = response.json()['run_id']
    assert Worker(settings).once()
    result = client.get(url(run)).json()
    assert result['status'] == 'succeeded', result
    assert client.get(url(run, '/summary')).json()['run_id'] == run
    assert client.get(url(run, '/selection')).json()['gids'] == []
    assert client.get(url(old, '/nodes/' + gids[0])).status_code == 200
    repeated = client.post('/api/v1/runs', json={'dataset_id': dataset}, headers={'Idempotency-Key': key})
    assert repeated.json()['run_id'] == run and repeated.json()['status'] == 'succeeded'


def test_stale_run_recovery_and_fencing(api):
    _, store, settings, _, dataset, _ = api
    run = store.create_run(dataset, str(uuid4()))['run_id']
    store.claim('runs', 'dead')
    with store.connect(write=True) as db:
        db.execute('UPDATE runs SET heartbeat=0 WHERE id=?', (run,))
    Worker(settings).recover()
    assert store.get('runs', run)['status'] == 'failed'
    with pytest.raises(RuntimeError):
        with store.publication_guard(run, 'dead'):
            pytest.fail('Lost owner published')
    new_run = store.create_run(dataset, str(uuid4()))
    assert new_run['run_id'] != run


def test_recovery_after_files_published_before_database_commit(api):
    _, store, settings, old, dataset, _ = api
    import shutil
    run = store.create_run(dataset, str(uuid4()))['run_id']
    store.claim('runs', 'dead')
    path = settings.results / 'runs' / run
    shutil.copytree(store.get('runs', old)['bundle_path'], path)
    manifest = json.loads((path / 'run.json').read_text())
    manifest['run_id'] = run
    (path / 'run.json').write_text(json.dumps(manifest))
    with store.connect(write=True) as db:
        db.execute('UPDATE runs SET heartbeat=0 WHERE id=?', (run,))
    Worker(settings).recover()
    assert store.get('runs', run)['status'] == 'succeeded'


def upload_files(data):
    return {key: (key + '.parquet', (data / (key + '.parquet')).read_bytes(), 'application/octet-stream')
            for key in ('nodes', 'edges', 'transactions')}


def test_import_failure_and_upload_boundaries(api):
    client, store, settings, _, _, _ = api
    data = {'name': 'test', 'profile': 'hackathon-v1'}
    files = upload_files(settings.data)
    response = client.post('/api/v1/datasets', data=data, files=files)
    assert response.status_code == 202, response.text
    identifier = response.json()['dataset_id']
    assert client.post('/api/v1/runs', json={'dataset_id': identifier}, headers={'Idempotency-Key': str(uuid4())}).status_code == 409
    Worker(settings).once()
    result = client.get('/api/v1/datasets/' + identifier).json()
    assert result['status'] == 'invalid'  # public import may not label a small fixture official
    assert result['validation']['errors']
    assert client.post('/api/v1/datasets', data=data, files={'nodes': files['nodes']}).status_code == 422
    assert client.post('/api/v1/datasets', json=data).status_code == 415
    small = replace(settings, file_limit=1, body_limit=10000, bootstrap=False)
    with TestClient(create_app(small)) as restricted:
        assert restricted.post('/api/v1/datasets', data=data, files=files).status_code == 413
        assert restricted.post('/api/v1/datasets', content=b'x' * 10001).status_code == 413


def test_official_import_and_real_recalculation(tmp_path):
    root = Path(__file__).resolve().parents[1]
    settings = Settings(tmp_path / 'state', root / 'data', root / 'outputs')
    with TestClient(create_app(settings)) as client:
        response = client.post('/api/v1/datasets', data={'name': 'July', 'profile': 'hackathon-v1'}, files=upload_files(settings.data))
        assert response.status_code == 202, response.text
        dataset = response.json()['dataset_id']
        assert Worker(settings).once()
        assert client.get('/api/v1/datasets/' + dataset).json()['status'] == 'ready'
        run = client.post('/api/v1/runs', json={'dataset_id': dataset}, headers={'Idempotency-Key': str(uuid4())}).json()['run_id']
        assert Worker(settings).once()
        status = client.get(url(run)).json()
        assert status['status'] == 'succeeded', status
        assert status['elapsed_seconds'] < 300
        summary = client.get(url(run, '/summary')).json()
        assert summary['counts']['nodes'] == 2248 and summary['counts']['edges'] == 3119
        assert client.get(url(run, '/top')).json()['total'] >= 20


def test_worker_failure_has_no_publication(api, monkeypatch):
    client, store, settings, _, dataset, _ = api
    run = store.create_run(dataset, str(uuid4()))['run_id']
    def fail(*args, **kwargs):
        raise RuntimeError('private implementation detail')
    monkeypatch.setattr('backend.worker.run_pipeline', fail)
    Worker(settings).once()
    doc = client.get(url(run)).json()
    assert doc['status'] == 'failed'
    assert 'private' not in json.dumps(doc)
    assert not (settings.results / 'runs' / run).exists()
    assert client.get(url(run, '/summary')).status_code == 409


def test_live_lease_is_not_recovered(api):
    _, store, settings, _, dataset, _ = api
    run = store.create_run(dataset, str(uuid4()))['run_id']
    store.claim('runs', 'live')
    Worker(settings).recover()
    assert store.get('runs', run)['status'] == 'running'
    assert store.get('runs', run)['owner'] == 'live'


def test_stale_dataset_becomes_failed(api):
    client, store, settings, _, _, _ = api
    doc = dataset_document(str(uuid4()), 'abandoned')
    store.add_dataset(doc, settings.data, 2248, hashes(settings.data))
    store.claim('datasets', 'dead')
    with store.connect(write=True) as db:
        db.execute('UPDATE datasets SET heartbeat=0 WHERE id=?', (doc['dataset_id'],))
    Worker(settings).recover()
    result = client.get('/api/v1/datasets/' + doc['dataset_id']).json()
    assert result['status'] == 'failed' and result['failure']['code'] == 'INTERNAL_ERROR'


def test_selection_limit_and_run_isolation(api):
    client, store, settings, old, dataset, gids = api
    response = client.put(url(old, '/selection'), json={'gids': [str(n) for n in range(501)]})
    assert response.status_code == 422 and response.json()['error']['code'] == 'SELECTION_LIMIT'
    assert store.selection(old)['gids'] == []
    client.put(url(old, '/selection'), json={'gids': [gids[0]]})
    # Durable registry scoping is independent of whether the second run is finished.
    other = store.create_run(dataset, str(uuid4()))['run_id']
    assert store.selection(other)['gids'] == []
    assert store.selection(old)['gids'] == [gids[0]]


def test_corrupted_import_and_duplicate_fields(api):
    client, _, settings, _, _, _ = api
    files = upload_files(settings.data)
    files['nodes'] = ('nodes.parquet', b'invalid parquet', 'application/octet-stream')
    response = client.post('/api/v1/datasets', data={'name': 'Bad', 'profile': 'hackathon-v1'}, files=files)
    identifier = response.json()['dataset_id']
    Worker(settings).once()
    result = client.get('/api/v1/datasets/' + identifier).json()
    assert result['status'] == 'invalid'
    assert result['validation']['errors'][0]['file'] == 'nodes.parquet'
    repeated = [(k, value) for k, value in upload_files(settings.data).items()]
    repeated.append(repeated[0])
    response = client.post('/api/v1/datasets', data={'name': 'Bad', 'profile': 'hackathon-v1'}, files=repeated)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'INVALID_REQUEST'


def test_internal_error_is_safe_and_traceable(api, monkeypatch):
    client, store, _, _, _, _ = api
    def fail(*args, **kwargs):
        raise RuntimeError('secret local path')
    monkeypatch.setattr(store, 'list', fail)
    response = client.get('/api/v1/datasets')
    assert response.status_code == 500
    assert response.json()['request_id'] == response.headers['x-request-id']
    assert 'secret' not in response.text


def test_invalid_ids_and_error_shapes(api):
    client, _, _, run, _, _ = api
    for gid in ('+1', '01', '-0', str(2**63), '1.5'):
        assert client.get(url(run, '/nodes/' + gid)).status_code == 422
    # Smallest adversarial ID above JS exact range is accepted as a string.
    assert client.get(url(run, '/nodes/9007199254740993')).status_code == 404
    response = client.get('/api/v1/does-not-exist')
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'NOT_FOUND'


def test_stage_callback_and_reproducible_results(api):
    _, store, settings, _, dataset, _ = api
    from backend.pipeline import run_pipeline
    phases = []
    first_id, second_id = str(uuid4()), str(uuid4())
    source = Path(store.get('datasets', dataset)['path'])
    first = run_pipeline(source, settings.results, 24, run_id=first_id, on_stage=phases.append)
    second = run_pipeline(source, settings.results, 24, run_id=second_id)
    assert phases == ['load', 'features', 'roles', 'export', 'validation', 'publish']
    first_bundle, second_bundle = load_bundle(first), load_bundle(second)
    assert first_bundle.raw == second_bundle.raw
    assert first_bundle.manifest['run_id'] == first_id
