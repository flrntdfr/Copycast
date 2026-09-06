.DEFAULT_GOAL := help
SHELL := /bin/sh

UV      ?= uv
PNPM    ?= pnpm --dir web
COMPOSE ?= docker compose
IMAGE   ?= copycast:local
VERSION ?=

# End-to-end stack: the CI overlay adds the fixtures server and pins the locally built image.
# The containers run as the caller's uid/gid so the bind-mounted ./data is writable without sudo.
E2E_IMAGE    ?= copycast:ci
E2E_BASE_URL ?= http://localhost:8080
E2E_COMPOSE  := -f docker-compose.yml -f docker-compose.ci.yml --profile direct
E2E_ENV      := COPYCAST_IMAGE=$(E2E_IMAGE) COPYCAST_UID=$$(id -u) COPYCAST_GID=$$(id -g)

.PHONY: help setup db-up db-down migrate api worker web-dev dev lint fmt typecheck \
        test test-unit test-integration test-e2e web-test web-lint web-build web-e2e \
        openapi openapi-check golden-regen coverage-domain build image image-run up down \
        engine-version bump clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## Install Python and web dependencies from the lockfiles
	$(UV) sync --locked --all-groups
	$(PNPM) install --frozen-lockfile

db-up: ## Start the development Postgres (deploy/compose/docker-compose.dev.yml)
	$(COMPOSE) -f deploy/compose/docker-compose.dev.yml up -d --wait

db-down: ## Stop the development Postgres
	$(COMPOSE) -f deploy/compose/docker-compose.dev.yml down

migrate: ## Apply database migrations
	$(UV) run copycast migrate

api: ## Run the API process with auto-reload
	$(UV) run copycast api --reload

worker: ## Run the worker process
	$(UV) run copycast worker

web-dev: ## Run the Vite dev server (proxies /api, /feeds, /mcp, /healthz to :8080)
	$(PNPM) run dev

dev: ## Run api, worker and the Vite dev server together
	@trap 'kill 0' INT TERM; \
	$(UV) run copycast api --reload & \
	$(UV) run copycast worker & \
	$(PNPM) run dev & \
	wait

lint: ## Ruff check, format check and import-linter
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run lint-imports

fmt: ## Auto-fix lint findings and format
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

typecheck: ## Pyright (strict) over src
	$(UV) run pyright src

test: ## Unit + integration tests (not e2e, not network)
	$(UV) run pytest -m "not e2e and not network" --cov --cov-report=term-missing

test-unit: ## Unit tests only
	$(UV) run pytest tests/unit -q

test-integration: ## Integration tests (Postgres via COPYCAST_TEST_DATABASE_URL or testcontainers)
	$(UV) run pytest tests/integration -q -m "not network"

test-e2e: ## Build copycast:ci, start the compose stack (direct profile + fixtures), httpx smoke + Playwright, tear down
	$(MAKE) image IMAGE=$(E2E_IMAGE)
	mkdir -p data config
	$(E2E_ENV) $(COMPOSE) $(E2E_COMPOSE) up -d --wait
	@status=0; \
	{ COPYCAST_E2E_BASE_URL=$(E2E_BASE_URL) $(UV) run pytest -m e2e tests/e2e -q \
	  && PLAYWRIGHT_BASE_URL=$(E2E_BASE_URL) $(PNPM) run e2e; } || status=$$?; \
	if [ $$status -ne 0 ]; then \
	  $(E2E_ENV) $(COMPOSE) $(E2E_COMPOSE) logs --no-color --tail 200; \
	fi; \
	$(E2E_ENV) $(COMPOSE) $(E2E_COMPOSE) down -v; \
	exit $$status

web-test: ## Frontend unit tests (vitest)
	$(PNPM) run test -- --run

web-lint: ## Frontend lint + typecheck
	$(PNPM) run lint
	$(PNPM) run typecheck

web-build: ## Build the frontend bundle
	$(PNPM) run build

web-e2e: ## Playwright specs against a running stack
	$(PNPM) run e2e

openapi: ## Regenerate web/openapi.json and the TypeScript schema
	$(UV) run copycast openapi > web/openapi.json
	$(PNPM) run gen

openapi-check: openapi ## Fail when web/openapi.json is out of date
	git diff --exit-code web/openapi.json

golden-regen: ## Regenerate golden Mirror Feed fixtures
	COPYCAST_GOLDEN_REGEN=1 $(UV) run pytest tests/unit/feeds -q

coverage-domain: ## Coverage report restricted to the domain package
	$(UV) run pytest tests/unit/domain -q --cov=copycast.domain --cov-report=term-missing --cov-fail-under=95

build: ## Build the wheel and sdist
	$(UV) build

image: ## Build the container image
	docker build -t $(IMAGE) \
		--build-arg APP_VERSION=$$($(UV) version --short) \
		--build-arg ENGINE_VERSION=$$($(UV) run python scripts/engine_version.py) .

image-run: ## Run the compose stack with the locally built image (direct profile)
	COPYCAST_IMAGE=$(IMAGE) COMPOSE_PROFILES=direct $(COMPOSE) up -d --wait

up: ## Start the compose stack
	$(COMPOSE) up -d --wait

down: ## Stop the compose stack
	$(COMPOSE) down

engine-version: ## Print the locked yt-dlp version
	$(UV) run python scripts/engine_version.py

bump: ## Set the application version: make bump VERSION=1.2.3
	@test -n "$(VERSION)" || { echo "usage: make bump VERSION=X.Y.Z"; exit 2; }
	$(UV) version $(VERSION)
	sed -i.bak 's/^APP_VERSION = ".*"$$/APP_VERSION = "$(VERSION)"/' src/copycast/version.py && rm -f src/copycast/version.py.bak

clean: ## Remove build, cache and coverage artifacts
	rm -rf build dist .pytest_cache .ruff_cache .coverage coverage.xml htmlcov web/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
