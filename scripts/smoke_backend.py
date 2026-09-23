"""Exercise a running API/worker using the official Parquet inputs."""
import argparse
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx


def wait(client, resource, success, failed, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(resource)
        response.raise_for_status()
        body = response.json()
        if body['status'] in success:
            return body
        if body['status'] in failed:
            raise RuntimeError(json.dumps(body, ensure_ascii=False))
        time.sleep(.5)
    raise TimeoutError(resource)


def exercise(base_url, data):
    with httpx.Client(base_url=base_url.rstrip('/') + '/', timeout=30) as client:
        client.get('health').raise_for_status()
        with ExitStack() as stack:
            files = {key: (key + '.parquet', stack.enter_context((data / (key + '.parquet')).open('rb')),
                           'application/octet-stream') for key in ('nodes', 'edges', 'transactions')}
            response = client.post('datasets', data={'name': 'HTTP smoke', 'profile': 'hackathon-v1'}, files=files)
        response.raise_for_status()
        dataset = wait(client, 'datasets/' + response.json()['dataset_id'], {'ready'}, {'invalid', 'failed'})
        key = str(uuid4())
        request = dict(json={'dataset_id': dataset['dataset_id']}, headers={'Idempotency-Key': key})
        response = client.post('runs', **request)
        response.raise_for_status()
        run_id = response.json()['run_id']
        assert client.post('runs', **request).json()['run_id'] == run_id
        run = wait(client, 'runs/' + run_id, {'succeeded'}, {'failed'})
        prefix = 'runs/' + run_id
        summary = client.get(prefix + '/summary')
        summary.raise_for_status()
        top = client.get(prefix + '/top').json()
        assert top['total'] >= 20
        chosen = [r['gid'] for r in top['items'][:3]]
        for gid in chosen:
            card = client.get(prefix + '/nodes/' + gid)
            card.raise_for_status()
            assert card.json()['gid'] == gid
            graph = client.get(prefix + '/nodes/' + gid + '/graph?hops=2&node_limit=10&edge_limit=20')
            graph.raise_for_status()
            assert gid in {n['gid'] for n in graph.json()['nodes']}
        client.put(prefix + '/selection', json={'gids': chosen}).raise_for_status()
        assert client.get(prefix + '/selection').json()['gids'] == chosen
        digests = {}
        for filename in ('nodes_roles.csv', 'clusters.csv', 'top_nodes.csv'):
            response = client.get(prefix + '/exports/' + filename)
            response.raise_for_status()
            digests[filename] = sha256(response.content).hexdigest()
        selected = client.get(prefix + '/selection/export')
        selected.raise_for_status()
        assert len(selected.text.splitlines()) == 4
        return dict(status='passed', run_id=run_id, dataset_id=dataset['dataset_id'],
                    elapsed_seconds=run['elapsed_seconds'], counts=summary.json()['counts'],
                    exports_sha256=digests)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000/api/v1/')
    parser.add_argument('--data', type=Path, default=Path('data'))
    args = parser.parse_args()
    print(json.dumps(exercise(args.base_url, args.data), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
