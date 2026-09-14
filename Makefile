# Convenience wrappers. Every target maps to commands that are also spelled out
# in RUN.md, so a reviewer who ignores make entirely is never blocked.

SHELL := /bin/bash
COMPOSE := docker compose
API := api
VENV := $(API)/.venv
PY := $(VENV)/bin/python

export KIVI_DATABASE_URL ?= postgresql+psycopg://kivi:kivi@localhost:5432/kivi

.PHONY: help up down migrate reset psql test native-setup native-db lint

help:
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --- primary review path: containers ---------------------------------------

up: ## Start Postgres+pgvector and apply migrations
	$(COMPOSE) up -d db
	$(COMPOSE) run --rm migrate

down: ## Stop containers, keep data
	$(COMPOSE) down

reset: ## Destroy all data and rebuild an empty schema
	$(COMPOSE) down -v
	$(COMPOSE) up -d db
	$(COMPOSE) run --rm migrate

# --- alternative path: native Postgres, no Docker --------------------------

native-setup: ## Create the venv and install the backend
	cd $(API) && uv venv --python 3.11 .venv && \
	  uv pip install --python .venv/bin/python -e ".[dev]"

native-db: ## Create role+database on a locally running Postgres 16
	-su postgres -c "psql -c \"CREATE ROLE kivi LOGIN PASSWORD 'kivi' SUPERUSER;\""
	-su postgres -c "createdb -O kivi kivi"

migrate: ## Apply migrations using the local venv
	cd $(API) && .venv/bin/alembic upgrade head

# --- inspection and checks -------------------------------------------------

psql: ## Open a shell on the memory state
	PGPASSWORD=kivi psql -h localhost -U kivi -d kivi

test: ## Run the schema verification suite against the live database
	cd $(API) && .venv/bin/python -m pytest tests -q

lint:
	cd $(API) && .venv/bin/ruff check kivi tests
