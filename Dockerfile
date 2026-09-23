FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.lock ./
RUN python -m pip install -r requirements.lock

COPY src/ ./src/
COPY tests/ ./tests/
COPY app.py pipeline.py validate.py ./
COPY .streamlit/ ./.streamlit/

EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--", "--outputs", "/results"]
