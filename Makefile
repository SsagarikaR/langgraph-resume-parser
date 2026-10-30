.PHONY: help install dev ui test lint format typecheck eval backup restore deploy wipe secrets wipe-yes up down logs eval-pdfs monitoring-up monitoring-down monitoring-logs audit

PYTHON := .venv/bin/python3
# Only needed if your user isn't in the `docker` group: make up SUDO=sudo
SUDO ?=
COMPOSE := $(SUDO) docker compose

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Development ────────────────────────────────────────────────────────────────

install: ## Create venv and install all dependencies
	python3 -m venv .venv
	$(PYTHON) -m pip install -e ".[dev]"
	$(PYTHON) -m pre_commit install

dev: ## Start the API server locally (hot-reload)
	@set -a; [ -f .env ] && . ./.env; set +a; $(PYTHON) -m uvicorn app.main:app --reload

ui: ## Start the Streamlit UI locally (reads .env.ui.local if it exists)
	@set -a; [ -f .env.ui.local ] && . ./.env.ui.local; set +a; $(PYTHON) -m streamlit run ui/streamlit_app.py

# ── Quality gates ──────────────────────────────────────────────────────────────

test: ## Run the full test suite
	$(PYTHON) -m pytest

lint: ## Check code style with ruff
	$(PYTHON) -m ruff check .

format: ## Auto-fix style issues with ruff
	$(PYTHON) -m ruff format .

typecheck: ## Run mypy type checks
	$(PYTHON) -m mypy app

audit: ## Check locked dependencies for known vulnerabilities (same check as CI)
	$(PYTHON) -m pip install -q pip-audit==2.9.0
	@today=$$(date +%F); \
	ignores=$$(awk -v today="$$today" '/^(CVE-|GHSA-)/ { split($$2, e, ":"); if (e[2] >= today) printf "--ignore-vuln %s ", $$1 }' .trivyignore); \
	for f in api ui dev; do \
		$(PYTHON) scripts/filter_requirements_for_audit.py requirements/$$f.txt /tmp/audit-$$f.txt; \
		$(PYTHON) -m pip_audit --require-hashes --disable-pip $$ignores -r /tmp/audit-$$f.txt; \
	done

# ── Evaluations ────────────────────────────────────────────────────────────────

eval: ## Run the LLM evaluation suite (requires GEMINI_API_KEY)
	$(PYTHON) -m evals.run_eval

eval-pdfs: ## Re-generate evaluation PDFs from evals/fixtures/ text files
	$(PYTHON) evals/text_to_pdf.py

# ── Data management ────────────────────────────────────────────────────────────

wipe: ## Erase all local personal data (uploads, reports, checkpoint DB)
	@bash scripts/wipe_local_data.sh

wipe-yes: ## Erase local personal data without confirmation prompt
	@bash scripts/wipe_local_data.sh --yes

# ── Docker ops ─────────────────────────────────────────────────────────────────

up: ## Start the full stack with Docker Compose (always rebuilds, so local code changes apply)
	$(COMPOSE) up -d --wait --build

down: ## Stop and remove containers
	$(COMPOSE) down

logs: ## Follow API logs
	$(COMPOSE) logs -f api

monitoring-up: ## Start Prometheus + Grafana alongside the main stack (needs GRAFANA_ADMIN_PASSWORD in .env)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.monitoring.yml up -d --wait

monitoring-down: ## Stop Prometheus + Grafana (leaves the main stack running)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.monitoring.yml stop prometheus grafana

monitoring-logs: ## Follow Prometheus + Grafana logs
	$(COMPOSE) -f docker-compose.yml -f docker-compose.monitoring.yml logs -f prometheus grafana

# ── Secrets ────────────────────────────────────────────────────────────────────

secrets: ## Fetch secrets from cloud (SECRETS_BACKEND=aws|gcp|vault required)
	@bash scripts/fetch_secrets.sh $(ARGS)

# ── Backup / restore ───────────────────────────────────────────────────────────

backup: ## Back up the checkpoint database (BACKUP_AGE_RECIPIENT=age1... required)
	@bash scripts/backup.sh $(DEST)

restore: ## Restore from a backup: make restore BACKUP=backups/checkpoints-....db.age
	@bash scripts/restore.sh $(BACKUP)

# ── Deployment ─────────────────────────────────────────────────────────────────

deploy: ## Deploy signed images: make deploy API=ghcr.io/.../api@sha256:... UI=ghcr.io/.../ui@sha256:...
	@GITHUB_REPOSITORY=$(GITHUB_REPOSITORY) bash scripts/deploy.sh $(API) $(UI)
