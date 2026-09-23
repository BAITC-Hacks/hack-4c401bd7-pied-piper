#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Сначала установите окружение: uv venv .venv && uv pip sync --python .venv/bin/python requirements.lock' >&2
  exit 1
fi
exec .venv/bin/python -m streamlit run app.py -- "$@"
