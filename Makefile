.PHONY: help install dev-install lint test cov up down db-init load-test cluster-up cluster-down

help:
	@echo "Targets: install dev-install lint test cov up down db-init load-test cluster-up cluster-down"

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
	psql "postgresql://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@$(POSTGRES_HOST):$(POSTGRES_PORT)/$(POSTGRES_DB)" -f db/schema.sql

load-test:
	locust -f scripts/load_test.py --headless -u 20 -r 5 -t 1m --host http://localhost:8000

cluster-up:
	kind create cluster --name care-ops || true
	kubectl apply -f k8s/

cluster-down:
	kind delete cluster --name care-ops
