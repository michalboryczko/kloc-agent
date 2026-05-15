.PHONY: help compose-up compose-down test test-unit lint build-runner migrate

help:
	@echo "Targets:"
	@echo "  compose-up    bring up postgres + minio + backend"
	@echo "  compose-down  tear down compose stack"
	@echo "  migrate       run alembic upgrade head against compose Postgres"
	@echo "  build-runner  build kloc-agent-runner:dev image (must run before backend spawn)"
	@echo "  test          run all non-e2e tests"
	@echo "  test-unit     run unit tests only"

compose-up:
	docker compose up -d

compose-down:
	docker compose down

migrate:
	uv run alembic upgrade head

build-runner:
	docker build -t kloc-agent-runner:dev -f runner/Dockerfile .

test:
	uv run pytest -m "not e2e"

test-unit:
	uv run pytest -m unit
