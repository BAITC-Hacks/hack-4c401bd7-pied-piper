# syntax=docker/dockerfile:1
FROM python:3.12.12-slim-bookworm AS builder
WORKDIR /build
COPY requirements.lock ./
RUN python -m venv /opt/venv
# Keep existing pins, excluding pytest and its exclusive dependencies.
RUN python -c "from pathlib import Path; lines=Path('requirements.lock').read_text().splitlines(); Path('runtime.txt').write_text('\n'.join(s for s in lines if '==' in s and s.split('==')[0] not in {'pytest','pluggy','iniconfig','pygments'}))"
RUN --mount=type=cache,target=/root/.cache/pip /opt/venv/bin/pip install --no-compile -r runtime.txt
# Preserve numpy._core.tests: numpy.testing imports _natype at runtime via SciPy.
RUN find /opt/venv -type d \( -name tests -o -name __pycache__ \) ! -path '*/numpy/_core/tests' -prune -exec rm -rf '{}' +

FROM python:3.12.12-slim-bookworm AS app
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY frontend/ ./frontend/
COPY backend/ ./backend/
COPY scripts/ensure_results.py ./scripts/ensure_results.py
COPY .streamlit/config.toml ./.streamlit/config.toml
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "frontend/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--", "--outputs", "/results"]

FROM app AS tests
COPY requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-compile -r requirements.lock
COPY scripts/audit_evidence.py ./scripts/audit_evidence.py
COPY scripts/smoke_backend.py ./scripts/smoke_backend.py
COPY tests/ ./tests/
CMD ["python", "-m", "pytest", "-q"]

FROM app AS runtime
