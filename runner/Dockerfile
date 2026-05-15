FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /usr/local/bin/uv

# git is required so `uv sync` can clone `strands_agentskills @ git+...`.
# python:3.12-slim does not ship git.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dev-1 owns the root pyproject.toml + uv.lock; this image installs the
# same dependency set because the agent SDK and ag_ui_strands need to be
# available in the runner. The build context is the repo root.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Runner package. Sources baked in for prod; mount-over for dev.
COPY runner/ /app/runner/
COPY src/ /app/src/

# Skills are bind-mounted at runtime: -v ./skills:/skills:ro
ENV KLOC_SKILLS_DIR=/skills \
    KLOC_HYDRATION_PATH=/run/kloc/hydration.json \
    OTEL_SERVICE_NAME=kloc-agent-runner \
    OTEL_RESOURCE_ATTRIBUTES=service.namespace=kloc,component=runner

# B-INFRA-4: put the uv-managed venv bin on PATH so the ENTRYPOINT below
# can resolve `opentelemetry-instrument` and `python`. Mirrors backend
# Dockerfile:26.
ENV PATH="/app/.venv/bin:${PATH}"

# Plan H2 (dev-3): OTel auto-instrumentation wraps the entrypoint so
# runner spans / metrics reach the same collector as the backend. The
# `opentelemetry-instrument` binary ships with `opentelemetry-distro`
# in the root pyproject.toml. The runner-side OTEL_EXPORTER_OTLP_ENDPOINT
# is forwarded from the backend process env at spawn time (see
# `src/runner_mgmt/docker_runner.py`).
ENTRYPOINT ["opentelemetry-instrument", "python", "-m", "runner"]
