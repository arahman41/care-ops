.PHONY: help install dev-install lint test cov up down db-init load-test cluster-up cluster-down eval-structuring eval-structuring-primock eval-structuring-replay coding-benchmark coding-benchmark-pilot coding-benchmark-replay

help:
	@echo "Targets: install dev-install lint test cov up down db-init load-test cluster-up cluster-down eval-structuring eval-structuring-primock eval-structuring-replay coding-benchmark coding-benchmark-pilot coding-benchmark-replay"

# Use the project venv, not whatever `python` resolves to on PATH. On Windows
# a bare `python` hits the system install, which has none of the pinned deps:
# `make coding-benchmark-replay` failed on a missing psycopg for exactly this
# reason. Falls back to `python` when no venv is present (CI installs deps
# into the ambient environment). Override with: make test PY=python3.12
PY ?= $(firstword $(wildcard .venv/Scripts/python.exe .venv/bin/python) python)

# Match the defaults in docker-compose.yml. These were previously undefined,
# so `make db-init` expanded to `psql -U -d -f -` and failed with
# `role "-d" does not exist`: make does not read .env. Override on the command
# line or from the environment if your compose file differs.
POSTGRES_USER ?= care_ops
POSTGRES_DB ?= care_ops

install:
	$(PY) -m pip install -r requirements.txt

dev-install:
	$(PY) -m pip install -r requirements-dev.txt

lint:
	$(PY) -m ruff check .

test:
	$(PY) -m pytest -q

cov:
	$(PY) -m pytest --cov=shared --cov=services --cov=governance --cov-report=term-missing

up:
	docker compose up --build

down:
	docker compose down -v

db-init:
	docker compose exec -T db psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) -f - < db/schema.sql

load-test:
	$(PY) scripts/run_load_test.py

cluster-up:
	kind create cluster --name care-ops || true
	kubectl apply -f k8s/

cluster-down:
	kind delete cluster --name care-ops

# The newest verdicts-only STRUCTURING artifact. Matched by prefix, not a bare
# *.json: since P2-4 the same directory also holds coding_*.json artifacts,
# which have a different shape, and an unscoped glob would hand the newest one
# to the structuring replay. The .full.json siblings carry clinical text and
# are gitignored, so they are never what gets replayed.
ARTIFACT ?= $(shell ls -t governance/eval_artifacts/structuring_*.json 2>/dev/null | grep -v '\.full\.json' | head -1)

# The headline structuring metric. Costs real money on a cold cache; every LLM
# call is content-addressed, so a re-run replays the same outputs for free.
eval-structuring:
	$(PY) scripts/run_structuring_eval.py --dataset aci

# The Phase 1 exit gate: PriMock57 held-out consultations from audio.
eval-structuring-primock:
	$(PY) scripts/run_structuring_eval.py --dataset primock

# Recompute the headline from the committed verdicts. Zero API calls.
eval-structuring-replay:
	$(PY) scripts/run_structuring_eval.py --replay $(ARTIFACT)

# The newest coding-benchmark committed artifact (never the .full.json roster).
CODING_ARTIFACT ?= $(shell ls -t governance/eval_artifacts/coding_*.json 2>/dev/null | grep -v '\.full\.json' | head -1)

# P2-4 coding routing benchmark. The pilot is cheap; the full run spends real
# money (240 calls at xhigh and high) and is human-gated. See the runbook in
# docs/superpowers/plans/2026-07-22-p2-4-coding-routing-benchmark.md.
coding-benchmark-pilot:
	$(PY) scripts/run_coding_benchmark.py --pilot

coding-benchmark:
	$(PY) scripts/run_coding_benchmark.py

coding-benchmark-replay:
	$(PY) scripts/run_coding_benchmark.py --replay $(CODING_ARTIFACT)
