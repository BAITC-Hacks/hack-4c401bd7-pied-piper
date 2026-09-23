"""Start local API and worker together: python -m backend."""
import argparse
import logging
import subprocess
import sys

import uvicorn

from backend.config import Settings
from backend.datasets import bootstrap
from backend.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    settings = Settings.from_env()
    logging.basicConfig(level=logging.INFO)
    bootstrap(settings, Store(settings.state))
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    worker = subprocess.Popen([sys.executable, '-m', 'backend.worker'], creationflags=flags)
    try:
        uvicorn.run('backend.api:create_app', factory=True, host='127.0.0.1', port=args.port)
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()


if __name__ == '__main__':
    main()
