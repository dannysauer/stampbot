.PHONY: help venv install install-dev test lint format clean build docker-build docker-push helm-lint helm-template helm-validate helm-unittest helm-test helm-package run dev pre-commit pre-commit-install secrets-baseline act-lint act-test act-helm act-ci mutate mutate-report mutate-html mutate-survival mutate-clean

# Variables
IMAGE_NAME ?= stampbot
IMAGE_TAG ?= latest
REGISTRY ?= docker.io
CHART_VERSION ?= 0.1.0
VENV := .venv
PYTHON := $(VENV)/bin/python
POETRY := poetry

# Poetry configuration - create venv in project directory
export POETRY_VIRTUALENVS_IN_PROJECT := true

# Docker buildx settings for caching
DOCKER_BUILDKIT := 1
BUILDX_CACHE_TYPE := registry

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

$(VENV)/bin/activate: pyproject.toml poetry.lock
	$(POETRY) install
	@touch $(VENV)/bin/activate

venv: $(VENV)/bin/activate ## Create virtual environment with dependencies

install: ## Install production dependencies only
	$(POETRY) install --only main

install-dev: $(VENV)/bin/activate ## Install development dependencies
	@# venv target already runs poetry install

test: venv ## Run tests
	$(PYTHON) -m pytest tests/ -v

# Mutation testing with Cosmic Ray
COSMIC_RAY := $(VENV)/bin/cosmic-ray
MUTATION_DB := .mutation.sqlite

mutate: venv ## Run mutation testing (full suite)
	$(COSMIC_RAY) init cosmic-ray.toml $(MUTATION_DB)
	$(COSMIC_RAY) --verbosity INFO exec cosmic-ray.toml $(MUTATION_DB)
	$(COSMIC_RAY) dump $(MUTATION_DB) | $(PYTHON) -c "\
import json, sys; \
data = []; \
[data.append({**json.loads(line)[0], **(json.loads(line)[1] or {})}) for line in sys.stdin if line.strip()]; \
print(json.dumps(data, indent=2))" > mutation-report.json
	@echo ""
	@echo "Mutation testing complete. Run 'make mutate-report' for summary."

mutate-report: venv ## Show mutation testing summary
	@if [ ! -f $(MUTATION_DB) ]; then echo "No mutation database found. Run 'make mutate' first."; exit 1; fi
	$(COSMIC_RAY) dump $(MUTATION_DB) | $(PYTHON) -c "\
import json, sys; \
data = [{**json.loads(line)[0], **(json.loads(line)[1] or {})} for line in sys.stdin if line.strip()]; \
total = len(data); \
killed = sum(1 for m in data if m.get('test_outcome') == 'killed'); \
survived = sum(1 for m in data if m.get('test_outcome') == 'survived'); \
timeout = sum(1 for m in data if m.get('test_outcome') == 'timeout'); \
incompetent = sum(1 for m in data if m.get('test_outcome') == 'incompetent'); \
score = (killed / (killed + survived) * 100) if (killed + survived) > 0 else 0; \
print(f'Total mutants: {total}'); \
print(f'Killed: {killed}'); \
print(f'Survived: {survived}'); \
print(f'Timeout: {timeout}'); \
print(f'Incompetent: {incompetent}'); \
print(f'Mutation score: {score:.1f}%')"

mutate-html: venv ## Generate HTML mutation report
	@if [ ! -f $(MUTATION_DB) ]; then echo "No mutation database found. Run 'make mutate' first."; exit 1; fi
	$(VENV)/bin/cr-html $(MUTATION_DB) > mutation-report.html
	@echo "HTML report generated: mutation-report.html"

mutate-survival: venv ## List surviving mutants (tests may need improvement)
	@if [ ! -f $(MUTATION_DB) ]; then echo "No mutation database found. Run 'make mutate' first."; exit 1; fi
	$(COSMIC_RAY) dump $(MUTATION_DB) | $(PYTHON) -c "\
import json, sys; \
data = [{**json.loads(line)[0], **(json.loads(line)[1] or {})} for line in sys.stdin if line.strip()]; \
survived = [m for m in data if m.get('test_outcome') == 'survived']; \
print(f'Surviving mutants ({len(survived)}):'); \
print('-' * 60); \
for m in survived: \
    mut = m.get('mutations', [{}])[0]; \
    print(f\"{mut.get('module_path', '?')}:{mut.get('start_pos', [0])[0]} - {mut.get('operator_name', 'unknown')}\")"

mutate-clean: ## Remove mutation testing artifacts
	rm -f $(MUTATION_DB) mutation-report.json mutation-report.html

lint: venv ## Run linters
	$(VENV)/bin/ruff check stampbot/ tests/
	$(VENV)/bin/mypy stampbot/

format: venv ## Format code
	$(VENV)/bin/ruff format stampbot/ tests/
	$(VENV)/bin/ruff check --fix stampbot/ tests/

clean: ## Clean build artifacts
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .pytest_cache
	rm -rf .mypy_cache
	rm -rf .ruff_cache
	rm -rf htmlcov/
	rm -rf $(VENV)
	rm -f .mutation.sqlite mutation-report.json mutation-report.html
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete

build: venv ## Build Python package
	$(PYTHON) -m build

docker-build: ## Build Docker image with caching
	docker buildx build \
		--platform linux/amd64,linux/arm64 \
		--cache-from type=$(BUILDX_CACHE_TYPE),ref=$(REGISTRY)/$(IMAGE_NAME):buildcache \
		--cache-to type=$(BUILDX_CACHE_TYPE),ref=$(REGISTRY)/$(IMAGE_NAME):buildcache,mode=max \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		-t $(REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG) \
		-t $(REGISTRY)/$(IMAGE_NAME):latest \
		--load \
		.

docker-build-pr: ## Build Docker image for PR with PR number tag
	@if [ -z "$(PR_NUMBER)" ]; then \
		echo "Error: PR_NUMBER is required"; \
		exit 1; \
	fi
	docker buildx build \
		--platform linux/amd64 \
		--cache-from type=$(BUILDX_CACHE_TYPE),ref=$(REGISTRY)/$(IMAGE_NAME):buildcache \
		--cache-to type=$(BUILDX_CACHE_TYPE),ref=$(REGISTRY)/$(IMAGE_NAME):buildcache,mode=max \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		-t $(REGISTRY)/$(IMAGE_NAME):pr-$(PR_NUMBER) \
		--load \
		.

docker-push: ## Push Docker image
	docker push $(REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)
	docker push $(REGISTRY)/$(IMAGE_NAME):latest

docker-push-pr: ## Push Docker image for PR
	@if [ -z "$(PR_NUMBER)" ]; then \
		echo "Error: PR_NUMBER is required"; \
		exit 1; \
	fi
	docker push $(REGISTRY)/$(IMAGE_NAME):pr-$(PR_NUMBER)

helm-lint: ## Lint Helm chart
	helm lint charts/stampbot

helm-template: ## Render Helm templates
	helm template stampbot charts/stampbot \
		--set github.appId=123456 \
		--set github.privateKey=test \
		--set github.webhookSecret=test

helm-validate: ## Validate Helm templates with kubeconform
	@command -v kubeconform >/dev/null 2>&1 || { echo "kubeconform not found. Install: go install github.com/yannh/kubeconform/cmd/kubeconform@latest"; exit 1; }
	helm template stampbot charts/stampbot \
		--set github.appId=123456 \
		--set github.privateKey=test \
		--set github.webhookSecret=test \
		| kubeconform -strict -ignore-missing-schemas -summary

helm-unittest: ## Run Helm unit tests (via Docker)
	docker run --rm -v $(CURDIR)/charts:/apps helmunittest/helm-unittest:latest stampbot

helm-test: helm-lint helm-validate helm-unittest ## Run all Helm tests

helm-package: ## Package Helm chart
	helm package charts/stampbot --version $(CHART_VERSION)

helm-install: ## Install Helm chart locally
	helm upgrade --install stampbot charts/stampbot \
		--set image.repository=$(REGISTRY)/$(IMAGE_NAME) \
		--set image.tag=$(IMAGE_TAG) \
		--create-namespace \
		--namespace stampbot

helm-uninstall: ## Uninstall Helm chart
	helm uninstall stampbot --namespace stampbot

run: venv ## Run the application locally
	$(PYTHON) -m stampbot

dev: venv ## Run the application in development mode with auto-reload
	$(VENV)/bin/uvicorn stampbot.main:app --reload --host 0.0.0.0 --port 8000

# CI/CD targets
ci-test: install-dev lint test ## Run CI tests

ci-build: docker-build ## Build for CI

ci-release: docker-build docker-push helm-package ## Build and release

# Pre-commit hooks
pre-commit: venv ## Run pre-commit checks on all files
	$(VENV)/bin/pre-commit run --all-files

pre-commit-install: venv ## Install pre-commit hooks
	$(VENV)/bin/pre-commit install

# Secret detection
secrets-baseline: ## Update .secrets.baseline file for false positive management
	$(POETRY) install --with secrets
	@if [ -f .secrets.baseline ]; then \
		$(VENV)/bin/detect-secrets scan --baseline .secrets.baseline; \
	else \
		$(VENV)/bin/detect-secrets scan > .secrets.baseline; \
	fi
	@echo ""
	@echo "Baseline changes:"
	@git diff .secrets.baseline || true
	@echo ""
	@echo "Review false positives with: $(VENV)/bin/detect-secrets audit .secrets.baseline"

# GitHub Actions local testing with act (requires: docker, act)
# Install act: brew install act (macOS) or see https://github.com/nektos/act
act-lint: ## Run lint job locally with act
	act -j lint --eventpath .github/act/push.json

act-test: ## Run test job locally with act
	act -j test --eventpath .github/act/push.json

act-helm: ## Run helm-lint job locally with act
	act -j helm-lint --eventpath .github/act/push.json

act-ci: ## Run CI workflow locally (lint + test + helm-lint)
	act -j lint -j test -j helm-lint --eventpath .github/act/push.json
