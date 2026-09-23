"""Reuse a validated publication; calculate only when the volume is empty."""
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    exists = Path('/results/current.json').exists()
    entry = 'backend.validate' if exists else 'backend.pipeline'
    print('Checking existing results.' if exists else 'Creating first publication.', flush=True)
    return subprocess.call([
        sys.executable, '-m', entry, '--data', '/data', '--out', '/results',
    ], cwd=root)


if __name__ == '__main__':
    raise SystemExit(main())
