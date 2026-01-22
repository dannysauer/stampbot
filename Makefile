.PHONY: help venv install install-dev test lint format clean build docker-build docker-push helm-lint helm-template helm-validate helm-unittest helm-test helm-package run dev pre-commit pre-commit-install secrets-baseline act-lint act-test act-helm act-ci

# Variables
IMAGE_NAME ?= stampbot
IMAGE_TAG ?= latest
REGISTRY ?= docker.io
CHART_VERSION ?= 0.1.0
VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

# Docker buildx settings for caching
DOCKER_BUILDKIT := 1
BUILDX_CACHE_TYPE := registry

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

venv: $(VENV)/bin/activate ## Create virtual environment

install: venv ## Install production dependencies
	$(PIP) install -r requirements.txt

install-dev: venv ## Install development dependencies
	$(PIP) install -r requirements.txt
	$(PIP) install -e ".[dev]"

test: venv ## Run tests
	$(PYTHON) -m pytest tests/ -v

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
	$(PIP) install pre-commit
	$(VENV)/bin/pre-commit install

# Secret detection
secrets-baseline: venv ## Update .secrets.baseline file for false positive management
	$(PIP) install -q detect-secrets==1.5.0
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
