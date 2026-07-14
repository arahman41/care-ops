.PHONY: help install dev-install lint test cov up down db-init load-test cluster-up cluster-down eval-structuring eval-structuring-primock eval-structuring-replay

help:
	@echo "Targets: install dev-install lint test cov up down db-init load-test cluster-up cluster-down eval-structuring eval-structuring-primock eval-structuring-replay"

install:
	pip install -r requirements.txt

dev-install:
	pip install -r requirements-dev.txt

lint:
	ruff check .

test:
	pytest -q

cov:
	pytest --cov=shared --cov=services --cov=governance --cov-report=term-missing

up:
	docker compose up --build

down:
	docker compose down -v

db-init:
	docker compose exec -T db psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) -f - < db/schema.sql

load-test:
	locust -f scripts/load_test.py --headless -u 20 -r 5 -t 1m --host http://localhost:8000

cluster-up:
	kind create cluster --name care-ops || true
	kubectl apply -f k8s/

cluster-down:
	kind delete cluster --name care-ops

# The newest verdicts-only artifact. The .full.json siblings carry clinical
# text and are gitignored, so they are never what gets replayed.
ARTIFACT ?= $(shell ls -t governance/eval_artifacts/*.json 2>/dev/null | grep -v '\.full\.json' | head -1)

# The headline structuring metric. Costs real money on a cold cache; every LLM
# call is content-addressed, so a re-run replays the same outputs for free.
eval-structuring:
	python scripts/run_structuring_eval.py --dataset aci

# The Phase 1 exit gate: PriMock57 held-out consultations from audio.
eval-structuring-primock:
	python scripts/run_structuring_eval.py --dataset primock

# Recompute the headline from the committed verdicts. Zero API calls.
eval-structuring-replay:
	python scripts/run_structuring_eval.py --replay $(ARTIFACT)
