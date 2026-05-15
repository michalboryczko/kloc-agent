# syntax=docker/dockerfile:1.7
# Backend image (Phase 1.A6). CMD wrapped with `opentelemetry-instrument`
# per dev-3 Track H change-request.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# uv installs into /usr/local; we copy the binary explicitly.
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /usr/local/bin/uv

# git is required so `uv sync` can clone `strands_agentskills @ git+...`.
# python:3.12-slim does not ship git.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
# uv.lock will exist after `uv lock` runs locally; copy if present for repro.
COPY uv.lock* ./
RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

# Put the uv-managed venv bin on PATH so dev-3's CR-verbatim CMD can call
# `opentelemetry-instrument` and `uvicorn` directly without a `uv run` prefix.
ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000

CMD ["opentelemetry-instrument", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
