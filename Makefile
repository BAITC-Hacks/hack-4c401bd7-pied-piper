.DEFAULT_GOAL := help

.PHONY: backend backend-test
backend:
	"$(PYTHON)" -m backend

backend-test:
	"$(PYTHON)" -m pytest tests/test_backend.py -q

ifeq ($(OS),Windows_NT)
PYTHON := .venv/Scripts/python.exe
else
PYTHON := .venv/bin/python
endif
DATA ?= data
OUTPUTS ?= outputs
PORT ?= 3000

.PHONY: help setup pipeline validate ui stop run test demo docker-build docker-up docker-down docker-recompute docker-test

help:
	@echo "make backend  Start local API and worker on port 8000"
	@echo "make backend-test Run backend tests"
	@echo "make docker-build Build the runtime image once or after code updates"
	@echo "make docker-up    Start built containers without rebuilding"
	@echo "make docker-down  Stop containers and keep results"
	@echo "make docker-recompute Recalculate results using the built image"
	@echo "make docker-test  Build the separate test image and run tests"
	@echo "make stop     Stop this project UI on port $(PORT)"
	@echo "make setup    Install Python 3.12 environment and dependencies (uv required)"
	@echo "make run      Calculate real data, validate, then start UI on port $(PORT)"
	@echo "make ui       Open existing real results without recalculation"
	@echo "make pipeline Calculate real data"
	@echo "make validate Validate real results"
	@echo "make test     Run tests"
	@echo "make demo     Generate, validate and open synthetic demo"

$(PYTHON):
	uv venv --python 3.12 .venv

setup: $(PYTHON)
	uv pip sync --python "$(PYTHON)" requirements.lock

pipeline:
	"$(PYTHON)" -m backend.pipeline --data "$(DATA)" --out "$(OUTPUTS)"

validate:
	"$(PYTHON)" -m backend.validate --data "$(DATA)" --out "$(OUTPUTS)"

ui:
	"$(PYTHON)" -m streamlit run frontend/app.py --server.address 127.0.0.1 --server.port $(PORT) -- --outputs "$(OUTPUTS)"

stop:
	"$(PYTHON)" scripts/stop_ui.py --port $(PORT)

run:
	$(MAKE) pipeline
	$(MAKE) validate
	$(MAKE) ui

test:
	"$(PYTHON)" -m pytest -q

demo:
	"$(PYTHON)" tests/fixtures/ui/make_fixture.py --out demo_outputs --data demo_data
	"$(PYTHON)" -m backend.validate --data demo_data --out demo_outputs --expected-nodes 24
	$(MAKE) ui OUTPUTS=demo_outputs

docker-build:
	docker compose build ui

docker-up:
	docker compose up -d --no-build --pull never

docker-down:
	docker compose down

docker-recompute:
	docker compose run --rm --no-deps pipeline

docker-test:
	docker compose build tests
	docker compose run --rm tests
