# kloc-agent

Self-hosted research-agent service over the `kloc-intelligence` MCP.
Analysts ask natural-language questions about a PHP codebase from a web
chat and receive streamed, sourced answers.

PoC; see `docs/specs/kloc-agent-poc.md` for acceptance criteria and
`docs/specs/kloc-agent-poc-plan.md` for the implementation plan.

## Quick start

```
cp .env.example .env
# Fill in ANTHROPIC_API_KEY at minimum.
docker compose up -d
curl http://localhost:8000/healthz   # expect {"status":"ok"}
```

## Local development

```
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

bind-mounts `src/` for hot reload.

## Local URLs

| Service       | URL                       | Notes                                |
| ------------- | ------------------------- | ------------------------------------ |
| backend       | http://localhost:8000     | FastAPI                              |
| postgres      | localhost:5432            | `kloc / changeme` (override in .env) |
| MinIO API     | http://localhost:9000     | S3 endpoint                          |
| MinIO console | http://localhost:9001     | `minioadmin / minioadmin`            |

## Tests

```
uv run pytest -m "not e2e"   # unit + integration
uv run pytest -m e2e         # full compose + real Anthropic + Docker runner
```

## Layout

```
src/        backend (FastAPI + SQLAlchemy + aioboto3)
runner/     in-container Strands Agent runtime (dev-2)
skills/     bind-mounted into runners (dev-2)
frontend/   Next.js + CopilotKit + AG-UI (dev-3)
migrations/ Alembic async
tests/      unit / integration / e2e
docs/       specs, architecture, research
```

## Caveats

- skills/ is bind-mounted **read-only**; mutating it requires dropping
  in-flight sessions (research/04 risk R6d).
- Per-session Docker runners are spawned by the backend via aiodocker.
  They are NOT declared in docker-compose; the backend talks to the
  Docker socket on the host.
- AG-UI versions are pair-pinned: `ag-ui-protocol==0.1.18` (Python) ↔
  `@ag-ui/client==0.0.42` (frontend). Drift is a release-blocker (AC26).

## References

- `docs/specs/kloc-agent-poc.md` — 26 acceptance criteria, 14 QA scenarios.
- `docs/specs/kloc-agent-poc-plan.md` — File manifest, ownership table,
  4 interface contracts, phased tasks.
- `docs/architecture.md` — Three ASCII diagrams (system, backend, runner).
- `docs/research/04-persistence-storage.md` — Postgres schema + MinIO setup.
