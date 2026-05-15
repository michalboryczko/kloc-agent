# Coding Conventions

**Analysis Date:** 2026-05-15

## Naming Patterns

**Files:**
- Python modules: `snake_case.py` — e.g., `agent_factory.py`, `verify_hmac.py`, `event_bus.py`
- Python packages: `snake_case/` directories with `__init__.py`
- TypeScript/TSX files: `PascalCase.tsx` for components (`ChatWindow.tsx`, `ToolCallCard.tsx`), `camelCase.ts` for utilities/lib (`agui-http-agent.ts`, `api.ts`, `sseParser.ts`)
- Test files: `test_<module_name>.py` — mirrors `src/` module path exactly

**Classes:**
- Python: `PascalCase` — `RunnerRegistry`, `SessionRepo`, `WarmIdleManager`, `HeartbeatWatcher`
- Protocol classes: `PascalCase` with `Protocol` suffix — `Runner`, `RunnerHandle` (runtime_checkable)
- Pydantic models: `PascalCase` — `HydrationPayload`, `CreateSessionBody`, `PostMessageResponse`
- SQLAlchemy ORM: `PascalCase` — `Session`, `Message`, `AuditLog`, `ArtifactMetadata`
- Dataclasses: `PascalCase` — `RegistryEntry`

**Functions / Methods:**
- Python: `snake_case` — `get_settings()`, `create_engine_for_settings()`, `verify_hmac_signature()`
- Private helpers: `_snake_case` — `_diag()`, `_split_cors_allow_origins()`, `_validate_provider_key()`
- Module-level constants: `UPPER_SNAKE_CASE` — `REPLAY_WINDOW_MS`, `HARDCODED_ANALYST_ID`, `FLUSH_BYTES`
- TypeScript: `camelCase` for functions — `createSession()`, `listSessions()`, `jsonOrThrow()`

**Variables:**
- Python: `snake_case` — `session_id`, `runner_id`, `warm_idle_s`
- Logger name: always `log = logging.getLogger(__name__)` at module level (except `src/main.py` and named-subsystem loggers)
- TypeScript: `camelCase`

**Types:**
- Python `Literal` types used for constrained strings — `AuditEventType`, `LlmProvider`, `MessageRole`
- Python `TypeAlias` pattern: `AuditEmitFn = Callable[[str, dict], Awaitable[None]]` at module level

## Module Docstrings

Every Python module has a top-level docstring referencing the plan phase/AC numbers it implements. Pattern:

```python
"""HMAC-SHA256 signing + verification for the runner→backend webhook
(Phase 1.C-1.3, Contract C §C6).
...
"""
```

This is a firm convention: every `src/` file and test file has a docstring with plan traceability.

## Code Style

**Formatting:**
- Python: no explicit formatter config found (no `pyproject.toml [tool.ruff]` / `[tool.black]` sections); PEP 8 conventions observed throughout
- TypeScript: no Prettier config; ESLint via `eslint.config.mjs` using `next/core-web-vitals` + `next/typescript`
- TypeScript strict mode: `"strict": true` in `tsconfig.json`

**Linting:**
- Python: `# noqa: <code>` suppressions used sparingly (`F401` for intentional re-exports in `__init__.py`)
- Python: `# type: ignore[<code>]` used for aiodocker optional-import pattern and duck-typed attributes
- TypeScript: ESLint `next/core-web-vitals` + `next/typescript`

## Import Organization

**Python pattern (every file):**
1. `from __future__ import annotations` — always first, in every backend file
2. Standard library imports
3. Third-party imports (fastapi, sqlalchemy, pydantic, etc.)
4. Local `src.*` imports

```python
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.deps import get_session
from src.db.models import Session
```

**Optional/lazy imports** used for optional dependencies that may not be installed:

```python
try:
    import aiodocker  # type: ignore
except ImportError:
    aiodocker = None  # type: ignore
```

**TYPE_CHECKING guard** used to avoid circular imports at runtime:

```python
if TYPE_CHECKING:
    from .protocol import Runner, RunnerHandle
```

**TypeScript path aliases:**
- `@/*` maps to `./src/*` in `tsconfig.json`
- Components imported as `import { ChatWindow } from "@/components/ChatWindow"`

## Error Handling

**FastAPI endpoints:**
- Raise `HTTPException` directly with appropriate status codes
- Pattern: check precondition → raise if violated → proceed with happy path

```python
row = await sessions.get(session_id)
if row is None:
    raise HTTPException(status_code=404, detail="session not found")
if row.closed_at is not None:
    raise HTTPException(status_code=409, detail="session is closed")
```

**Lifespan / background tasks:**
- Catch broad exceptions with `except Exception as e:` + `logger.exception(...)` for defensive paths
- Use specific exception types for expected operational errors (e.g. `OperationalError`, `InterfaceError`)
- Non-critical boot failures use `logger.info()` with skip reason; critical failures re-raise

```python
except (OperationalError, InterfaceError, DBAPIError) as e:
    logger.exception("boot orphan-message scan: DB unreachable (%s)", e)
```

**Security functions:**
- Return `bool` never raise on bad input — `verify_hmac_signature` catches all exceptions and returns `False`

**Python exception chaining:**
- Used with `from exc` where cause matters: `raise SSEParseError(...) from exc`

## Logging

**Framework:** Python `logging` module

**Logger naming conventions:**
- Module-level: `log = logging.getLogger(__name__)` (most modules)
- Named subsystems: `log = logging.getLogger("kloc_agent.webhooks")` and `log = logging.getLogger("kloc_agent.internal")` for webhook/internal API modules
- Root app: `logger = logging.getLogger("kloc_agent")` in `src/main.py`

**Log level discipline:**
- `log.info()` — operational events (boot steps, runner spawn, eviction)
- `log.exception()` — caught exceptions that should surface (always includes traceback via `exc_info=True` implicitly)
- Avoid `log.debug()` in hot paths (not observed in codebase)

**Diagnostic bypass pattern:**
When uvicorn log filtering suppresses records, write directly to stderr:

```python
def _diag(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
```

This pattern appears in `src/api/webhooks.py` and `src/api/internal.py` — documented as B-DIAG-* observability workaround.

## Comments

**When to Comment:**
- Every non-trivial decision has an inline comment referencing plan sections, AC numbers, or reviewer IDs
- Guard clauses explaining WHY a check exists (not just what it does)

```python
# Force the `kloc_agent` logger tree to INFO so B-DIAG-* observability
# lines from `src/api/internal.py` + `src/api/webhooks.py` actually
# reach uvicorn's stdout — uvicorn keeps the root logger at WARNING by
# default and our custom logger inherits that level otherwise.
```

- Concurrency invariants documented where locking occurs (see `src/runner_mgmt/registry.py` module docstring explaining `_lock` discipline)

**Plan traceability comments:**
- AC numbers: `(AC15)`, `(AC24)` etc. appear in comments throughout
- Phase references: `Phase 1.A7`, `dev-2 CR`, `Track H` etc.
- Reviewer comments: `# Reviewer-2 C1 follow-up:` in test files

## Function Design

**Size:** Functions are compact; complex flows broken into private helpers with `_` prefix

**Parameters:** Use keyword-only arguments for optional config; positional for required primary args

**Return Values:**
- Pydantic models returned from API handlers (typed by `response_model=`)
- `None` returned explicitly from `204 No Content` endpoints
- `bool` from verification/check functions
- `AsyncIterator` from SSE/streaming generators

## Module Design

**Exports:**
- `__init__.py` files re-export public API symbols: `from src.runner_mgmt.registry import RunnerRegistry  # noqa: F401`
- `Final` constants in `tests/fixtures/audit_events.py` serve as the canonical audit event vocabulary

**Barrel Files:**
- Minimal: `src/runner_mgmt/__init__.py` exports `RunnerRegistry` and `sweeper`
- `src/api/__init__.py` is empty (routes imported directly in `src/main.py`)

## Python-Specific Conventions

**Pydantic v2:**
- `BaseSettings` from `pydantic_settings` for settings
- `Field(default_factory=...)` for mutable defaults
- `model_validator(mode="after")` for cross-field validation
- `field_validator(..., mode="before")` for coercion

**SQLAlchemy 2.0 async:**
- `Mapped[T]` / `mapped_column(...)` typed ORM style throughout `src/db/models.py`
- `async with engine.begin() as conn:` for connection-level operations
- `await session.flush()` (not `commit()`) in repos; commit happens at the API layer

**FastAPI:**
- Dependency injection via `Depends(get_session)` for DB sessions
- Router tags used: `tags=["sessions"]`, `tags=["webhooks"]`
- `app.state.*` for lifespan-owned singletons (engine, S3, RunnerRegistry)

## TypeScript / Next.js Conventions

**"use client" directive** at top of all interactive components (`ChatWindow.tsx`, `Composer.tsx`, `AgentBody.tsx`)

**Component props:** Typed inline with destructuring defaults:

```typescript
export function ChatWindow({
  title = "kloc analyst",
  initial = "Ask anything...",
}: {
  title?: string;
  initial?: string;
}) { ... }
```

**Fetch pattern:**
- `jsonOrThrow<T>(res)` helper used throughout `src/lib/api.ts` for uniform error handling
- `BROWSER_BACKEND_URL` from `process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000"`

---

*Convention analysis: 2026-05-15*
