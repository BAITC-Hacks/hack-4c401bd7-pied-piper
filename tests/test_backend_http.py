"""Real HTTP and process restart, with isolated state and official input files."""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

from scripts.smoke_backend import exercise


@contextmanager
def services(root, state, port, attempt):
    env = {**os.environ, 'PYTHONUTF8': '1', 'MONEY_GRAPH_STATE': str(state),
           'MONEY_GRAPH_DATA': str(root / 'data'), 'MONEY_GRAPH_OUTPUTS': str(root / 'outputs'),
           'MONEY_GRAPH_BOOTSTRAP': '1'}
    processes = []
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    with (state.parent / f'http-{attempt}.log').open('w', encoding='utf-8') as log:
        try:
            commands = [
                ['-m', 'uvicorn', 'backend.api:create_app', '--factory', '--host', '127.0.0.1', '--port', str(port)],
                ['-m', 'backend.worker'],
            ]
            for command in commands:
                processes.append(subprocess.Popen([sys.executable, *command], cwd=root, env=env,
                                                  stdout=log, stderr=log, creationflags=flags))
            with httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1/', timeout=10) as client:
                deadline = time.monotonic() + 40
                while True:
                    assert all(p.poll() is None for p in processes), 'API/worker exited; see HTTP test log'
                    try:
                        if client.get('health').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, 'API startup timed out'
                    time.sleep(0.1)
                yield client
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


def test_official_http_workflow_and_api_worker_restart(tmp_path):
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    state = tmp_path / 'state'
    with services(root, state, port, 1) as client:
        result = exercise(f'http://127.0.0.1:{port}/api/v1/', root / 'data')
        assert result['counts'] == dict(nodes=2248, edges=3119, transactions=4840, seeds=81, components=35, clusters=105)
        prefix = 'runs/' + result['run_id']
        selection = client.get(prefix + '/selection').json()
        datasets = client.get('datasets').json()
        run = client.get(prefix).json()
        assert len(selection['gids']) == 3
    with services(root, state, port, 2) as client:
        assert client.get(prefix + '/selection').json() == selection
        assert client.get('datasets').json() == datasets
        assert client.get(prefix).json() == run
        for filename, digest in result['exports_sha256'].items():
            response = client.get(prefix + '/exports/' + filename)
            assert response.status_code == 200
            assert sha256(response.content).hexdigest() == digest
        assert client.get(prefix + '/summary').json()['counts'] == result['counts']
    print(json.dumps({**result, 'restart': 'passed'}, ensure_ascii=False))
