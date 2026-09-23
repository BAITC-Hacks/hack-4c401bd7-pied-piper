"""Reuse a validated publication; calculate only when the volume is empty."""
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    exists = Path('/results/current.json').exists()
    entry = 'validate.py' if exists else 'pipeline.py'
    print('Checking existing results.' if exists else 'Creating first publication.', flush=True)
    return subprocess.call([
        sys.executable, str(root / entry), '--data', '/data', '--out', '/results',
    ])


if __name__ == '__main__':
    raise SystemExit(main())
