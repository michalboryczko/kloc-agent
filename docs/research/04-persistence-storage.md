# 04 — Persistence (Postgres) & Object Storage (MinIO)

> Scope: durable state for `kloc-agent`. Postgres is the relational source of truth
> (sessions, messages, audit log, artifact metadata). MinIO (S3-compatible, locked)
> stores artifact files. Backend is FastAPI + asyncio.

Versions: dates as of late 2025 / early 2026. Where a pin matters, the doc names
the package family ("SQLAlchemy 2.0", "Alembic 1.x", "aioboto3 13+") rather than
a specific patch.

---

## 1. Postgres schema (the meat)

Four tables. UUIDs for IDs (`gen_random_uuid()` from `pgcrypto`, which ships with
Postgres 13+; alternatively `uuidv7()` from a contrib or app-side `uuid.uuid7()`,
but `gen_random_uuid()` is the boring-correct choice for PoC). All timestamps are
`timestamptz`. `jsonb` is used where we read structured payloads selectively;
plain `text` is used where the payload is opaque to the DB.

### 1.1 Extensions & conventions

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS citext;       -- optional, for case-insensitive identifiers

-- Naming convention applied via SQLAlchemy MetaData so Alembic autogen produces
-- stable names. See section 4.
```

### 1.2 `sessions`

```sql
CREATE TABLE sessions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analyst_id    text        NOT NULL,                         -- PoC: bare string, no FK
    title         text        NOT NULL DEFAULT 'Untitled session',
    metadata      jsonb       NOT NULL DEFAULT '{}'::jsonb,     -- free-form: tags, model, hints
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    closed_at     timestamptz                                    -- NULL = open / resumable
);

CREATE INDEX ix_sessions_analyst_id_created_at
    ON sessions (analyst_id, created_at DESC);

CREATE INDEX ix_sessions_open
    ON sessions (analyst_id, updated_at DESC)
    WHERE closed_at IS NULL;          -- partial index for the common "my open sessions" listing

CREATE INDEX ix_sessions_metadata_gin
    ON sessions USING gin (metadata jsonb_path_ops);
```

**Rationale.**
- `analyst_id text`: PoC has a single hardcoded analyst, but typing it as `text`
  keeps the column ready for SSO/JWT subject values later. No FK to a `users`
  table — none exists yet.
- `title` is analyst-editable from the UI (rename a session post-hoc); keep
  it on the row so list endpoints don't join.
- `updated_at` is bumped on any write that meaningfully changes session state
  (new message persisted, title edited). Use an `UPDATE` from the message-
  persistence path, not a trigger — keeps behaviour explicit and migratable.
- `closed_at` lets us soft-archive sessions without deleting them. The partial
  index `ix_sessions_open` makes "list my active sessions" cheap as the corpus
  grows.
- `metadata jsonb` + GIN: holds things like preferred model, locale, the active
  skill set, copilot UI flags. `jsonb_path_ops` is the right opclass when you
  only do containment queries (`@>`), and it's smaller/faster than the default
  `jsonb_ops`.

### 1.3 `messages`

```sql
CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system', 'tool');

CREATE TABLE messages (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    uuid        NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role          message_role NOT NULL,
    content       text        NOT NULL DEFAULT '',              -- see "text not jsonb" note
    content_parts jsonb,                                         -- optional structured parts (tool_use, image refs)
    parent_message_id uuid    REFERENCES messages(id) ON DELETE SET NULL,
    model         text,                                          -- e.g. 'anthropic/claude-opus-4-7'
    token_count   integer,                                       -- final, after stream completes
    created_at    timestamptz NOT NULL DEFAULT now(),
    finalized_at  timestamptz,                                   -- NULL while streaming
    seq           bigint      NOT NULL                            -- monotonic per session, see below
);

CREATE INDEX ix_messages_session_seq
    ON messages (session_id, seq);

CREATE INDEX ix_messages_session_created
    ON messages (session_id, created_at);

CREATE INDEX ix_messages_streaming
    ON messages (session_id)
    WHERE finalized_at IS NULL;        -- partial index: "what's still streaming?"

-- Per-session monotonic ordering. Avoid relying on created_at when the runner
-- and backend clocks drift. seq is set in the app by SELECT max(seq)+1 inside
-- the same transaction as the INSERT.
CREATE UNIQUE INDEX uq_messages_session_seq
    ON messages (session_id, seq);
```

**Rationale.**
- `content text` *not* `jsonb`: the body of an assistant message is, in practice,
  a long string built up by appending tokens. `text` appends/UPDATEs cheaper than
  `jsonb` and avoids parse cost on every read. Structured side-channels (tool
  invocations, image attachments) go into `content_parts jsonb` so we don't
  flatten them into the text blob.
- `role enum`: small, indexable, prevents typos. If we ever need a fifth value
  we run a single migration (`ALTER TYPE ... ADD VALUE`).
- `parent_message_id`: kept as a nullable self-FK because it's cheap insurance.
  We do **not** wire branching into the UI in PoC — the column is there so a
  later branching feature is a code change, not a migration. `ON DELETE SET NULL`
  rather than `CASCADE` so deleting a parent doesn't silently delete children.
- `finalized_at`: the streaming-completion sentinel. Read paths can render
  "still typing" indicators by checking `finalized_at IS NULL`. The partial
  index makes recovery on backend restart ("find orphaned streams") O(active).
- `seq`: per-session monotonic counter. Wall-clock ordering on `created_at`
  breaks down when multiple writes land in the same millisecond. `seq` is the
  canonical order for replay.

### 1.4 `audit_log`

```sql
CREATE TABLE audit_log (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    uuid        REFERENCES sessions(id) ON DELETE CASCADE,
    message_id    uuid        REFERENCES messages(id) ON DELETE SET NULL,
    event_type    text        NOT NULL,                          -- 'tool_call_start' | 'tool_call_result' | 'hook_fired' | 'runner_spawned' | 'runner_evicted' | ...
    actor         text        NOT NULL,                          -- runner_id or 'system' or 'backend'
    payload       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_audit_session_created
    ON audit_log (session_id, created_at);

CREATE INDEX ix_audit_event_type_created
    ON audit_log (event_type, created_at);

CREATE INDEX ix_audit_payload_gin
    ON audit_log USING gin (payload jsonb_path_ops);
```

**Rationale.**
- `event_type text` not enum: audit event taxonomy grows constantly (new hooks,
  new runner states). An enum forces a migration per new event. Text + a Python
  `Literal[...]` type at the app layer is the right trade-off here, even though
  it loses DB-side validation. We get back validation by *also* enforcing the
  set in app code where events are produced — and the events are produced in
  exactly one module.
- `message_id` is nullable: many audit rows (`runner_spawned`, scheduled
  evictions, policy decisions) have no message yet.
- The two non-GIN indexes mirror the two read patterns: "show me the audit
  trail for this session" and "show me all `tool_call_result` events globally".
- GIN on payload: lets us answer "find audit rows where `payload.tool == 'mcp_search'`"
  without a full scan once the table is large. Cheap to add now; expensive to
  add to a 100M-row table later.

### 1.5 `artifact_metadata`

```sql
CREATE TABLE artifact_metadata (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    uuid        NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    message_id    uuid        REFERENCES messages(id) ON DELETE SET NULL,
    filename      text        NOT NULL,                          -- as the agent named it
    content_type  text        NOT NULL,                          -- e.g. 'application/pdf'
    size_bytes    bigint      NOT NULL,
    bucket        text        NOT NULL,                          -- e.g. 'kloc-agent-artifacts'
    object_key    text        NOT NULL,                          -- 'sessions/{session_id}/artifacts/{artifact_id}/{filename}'
    sha256        bytea       NOT NULL,                          -- 32 bytes; bytea, not text, to save space + indexable
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_artifact_session_created
    ON artifact_metadata (session_id, created_at);

CREATE INDEX ix_artifact_message
    ON artifact_metadata (message_id) WHERE message_id IS NOT NULL;

-- Same object should never be registered twice for the same session.
CREATE UNIQUE INDEX uq_artifact_session_object
    ON artifact_metadata (session_id, object_key);

-- For dedup / orphan reconciliation: same sha256 across sessions is fine, but
-- within a session we don't want duplicates.
CREATE INDEX ix_artifact_sha256
    ON artifact_metadata (sha256);
```

**Rationale.**
- `sha256 bytea`: 32 bytes vs 64 hex chars + a varchar header. Same comparability.
- `bucket` is stored even though we use a single bucket today — costs nothing
  and lets us migrate to per-tenant buckets without altering the schema.
- The `uq_artifact_session_object` constraint is the contract for the runner's
  upload-then-register flow: if the runner retries the webhook after a flaky
  connection, the unique constraint absorbs the duplicate without erroring.

---

## 2. Incremental message persistence

The runner emits a stream of token deltas. We want every delta on the wire
reflected in Postgres without one UPDATE per token.

### 2.1 Options considered

| Option | Pros | Cons |
|---|---|---|
| UPDATE per token | trivially correct, every reader sees current state | UPDATE-thrash; one HOT chain per row → bloat; vacuum pressure; 10–50 UPDATEs/sec/message |
| Batched UPDATE every N tokens / M ms (debounce) | bounded write rate; one row per message; cheap reads | small window of unpersisted text on crash; needs in-process buffer |
| Append-only `message_chunks` table | high write throughput; no UPDATE bloat; replayable | read path joins+concats; more code; PoC overkill |
| Buffer until done, single INSERT | minimum write cost | loses everything on crash; no incremental UI from DB |

### 2.2 Recommendation: **batched UPDATE with debounce**

Concretely, per active assistant message hold an in-memory buffer:
- Flush when the buffer exceeds **256 chars** *or* **250 ms** have elapsed since
  the last flush, whichever comes first.
- Always flush on `finalized_at`-setting event (stream end / error / cancel).
- The UPDATE is `UPDATE messages SET content = content || $1, updated_at = now() WHERE id = $2`
  — server-side concatenation, so the backend never re-sends the full body.

Why this and not append-only chunks?
- The frontend reads via SSE / AG-UI streaming, **not** by polling Postgres. The
  DB is the durable record, not the live transport. So the read path doesn't
  need per-token granularity — it needs "what's the state of the message if I
  reconnect now?".
- 4 flushes/sec/message is well inside Postgres comfort. With HOT updates on
  the row (no indexed column changes during streaming), bloat is bounded; a
  routine autovacuum pass keeps it healthy.
- We get exactly the durability we need: a 250 ms crash window is acceptable
  for a PoC chat surface, and finer durability would add complexity nothing in
  the product needs yet.

A reasonable upgrade path if we ever exceed this: keep the `messages` row as
the **finalised** record and stream chunks into a `message_chunks` table during
streaming; on finalisation, fold chunks into `messages.content` and delete them.
That migration is non-breaking — current readers continue to see the right
content because the rollup happens before `finalized_at` is set.

### 2.3 Edge cases

- **Backend restart mid-stream.** On boot, query
  `SELECT id, session_id FROM messages WHERE finalized_at IS NULL`. Either
  reattach to a still-running runner (preferred) or mark the message
  `finalized_at = now()` with an `audit_log` entry of `event_type =
  'stream_orphaned'`.
- **Runner died mid-stream.** Backend sees the runner-event stream close
  unexpectedly; flushes the buffer; sets `finalized_at`; writes an audit row.
- **Token counts.** Updated only at finalisation. They're meaningful only for
  the completed body and we don't want token-counter writes contending with
  content writes.

---

## 3. ORM / DB layer recommendation

### 3.1 Candidates

**SQLAlchemy 2.0 (async).** The 2.0 line is async-first with `AsyncSession`,
`async_sessionmaker`, `create_async_engine`. Mature, documented, integrates
with Alembic out of the box. Declarative `Mapped[...]` syntax is type-friendly.
Largest ecosystem, deepest Postgres feature support (window functions, CTEs,
JSONB operators, partial indexes — all first-class).

**Tortoise ORM.** Django-style, async-native, simpler API. Migration tool
(`aerich`) is younger and less battle-tested than Alembic. Less expressive
querying. Smaller ecosystem.

**SQLModel.** Pydantic-on-SQLAlchemy. Nicer for routes-that-mirror-tables
projects, but our message/audit shapes don't 1:1 mirror response shapes, and
SQLModel still defers to SQLAlchemy 2.0 underneath. We pay a thin abstraction
layer for limited benefit.

**asyncpg raw.** Fastest. Zero ORM ceremony. But: hand-rolled migrations,
hand-rolled query mapping, no relationship loading, no constraints discovery
for Alembic autogen. Not the right primary tool for a four-table schema with
real foreign keys.

**pgsync.** A change-data-capture sync tool, not an ORM. Wrong category for
this question — not a real candidate.

### 3.2 Pick: **SQLAlchemy 2.0 async**

Reasons:
1. **Alembic integration is the default path.** Autogenerate inspects the
   declarative `MetaData`. No "and then we wrote a migration adapter" step.
2. **Async support is first-class as of 2.0.** Not a bolt-on.
3. **Postgres-specific features.** Partial indexes, JSONB operators (`@>`,
   `?`, `?&`), `gen_random_uuid()`, `ON CONFLICT DO UPDATE` — all clean.
4. **Future-proofing.** When the team grows and patterns get richer (event
   sourcing, change tables, projections), SQLAlchemy doesn't get in the way.
   Tortoise and bare asyncpg both would.

Driver: `postgresql+asyncpg://...`. `asyncpg` is the fastest Postgres driver
in Python and the supported async dialect.

Performance footnote: for the hot path (the streaming UPDATE in §2.2) we keep
the operation simple enough that the ORM cost is rounding error. If profiling
ever shows otherwise, we drop to `session.execute(text("UPDATE ..."), {...})`
for that one query without rewriting the whole layer.

### 3.3 Minimal app layout

```python
# app/db/engine.py
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.config import settings

engine = create_async_engine(
    settings.database_url,            # 'postgresql+asyncpg://...'
    pool_size=10, max_overflow=20,
    pool_pre_ping=True,
    echo=settings.sql_echo,
)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
```

```python
# app/db/base.py
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata_obj = MetaData(naming_convention=NAMING_CONVENTION)

class Base(AsyncAttrs, DeclarativeBase):
    metadata = metadata_obj
```

```python
# app/api/deps.py
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.engine import async_session_factory

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
```

Routes inject `session: AsyncSession = Depends(get_session)`. A single
`AsyncSession` per request, never shared across concurrent tasks.

---

## 4. Migrations: Alembic async

### 4.1 File layout

```
backend/
├── alembic.ini
├── app/
│   ├── db/
│   │   ├── base.py             # Base, MetaData with naming convention
│   │   ├── engine.py
│   │   └── models.py           # All ORM models import Base
│   └── ...
└── migrations/
    ├── env.py
    ├── script.py.mako
    └── versions/
        └── 2026_01_01_0001_init.py
```

`alembic.ini` minimal:

```ini
[alembic]
script_location = migrations
file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d_%%(slug)s
sqlalchemy.url = ${DATABASE_URL}              ; read from env at runtime in env.py
```

### 4.2 Async `env.py` (the critical bit)

```python
# migrations/env.py
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.db.base import Base
from app.db import models  # noqa: F401  — register models on Base.metadata

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
fileConfig(config.config_file_name)

target_metadata = Base.metadata


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,                  # detect column type changes
        compare_server_default=True,        # detect DEFAULT changes
        render_as_batch=False,              # postgres doesn't need batch mode
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


run_migrations_online()
```

### 4.3 Autogenerate

```bash
alembic revision --autogenerate -m "init"
```

The `target_metadata = Base.metadata` is what Alembic compares against the live
DB. With `compare_type=True` and `compare_server_default=True`, the diff catches
type and default drift, not just column add/remove.

### 4.4 How migrations run at deploy

**Three options, ordered by recommendation:**

1. **One-shot service in compose / job in k8s (RECOMMENDED).** A separate
   container that runs `alembic upgrade head` and exits. The backend container
   has `depends_on: { backend-migrate: { condition: service_completed_successfully } }`.
   - Pros: clean separation, no race on multi-replica backend, easy to skip in
     CI, idempotent.
   - Cons: one extra service in compose.

2. **Entrypoint script in the backend image.** `entrypoint.sh` runs
   `alembic upgrade head && exec uvicorn ...`.
   - Pros: zero compose changes.
   - Cons: every backend replica races to run migrations; need an advisory lock
     (`pg_advisory_lock(42)` around the upgrade) to be safe. Adds boot time on
     every container.

3. **FastAPI startup hook.** Run migrations from `@app.on_event("startup")`.
   - Pros: simplest.
   - Cons: same race as (2), *plus* `alembic` was not designed to run from
     inside an already-imported app process; you'll have import-order pain and
     Alembic's `env.py` doing `asyncio.run(...)` from inside an already-running
     loop is a foot-gun.

**Pick option 1.** Compose snippet for the migrator is in §10.

### 4.5 Common pitfalls

- **Autogen drift.** Autogen does not detect: `CHECK` constraints (sometimes),
  enum value additions, partial indexes (partially), custom GIN opclasses
  (`jsonb_path_ops`). Always read the generated migration; never apply blind.
- **Naming convention.** Set it on `MetaData` *before* the first migration,
  not after. Otherwise existing constraints get renamed on the next autogen
  and produce a noisy diff.
- **Enum values.** `CREATE TYPE ... AS ENUM (...)` is autogen-friendly;
  `ALTER TYPE ... ADD VALUE` is not transactional in older Postgres and not
  always autogenned correctly. Hand-edit those.
- **Server defaults using functions.** `DEFAULT now()` vs `DEFAULT current_timestamp`
  vs `DEFAULT (now() AT TIME ZONE 'utc')` — pick one and stick to it, otherwise
  every autogen drifts.

---

## 5. MinIO setup

### 5.1 Compose service (excerpted; full chunk in §10)

```yaml
minio:
  image: quay.io/minio/minio:latest      # pin to a RELEASE.YYYY-MM-DDTHH-MM-SSZ tag for prod
  command: server /data --console-address ":9001"
  environment:
    MINIO_ROOT_USER: ${MINIO_ROOT_USER}
    MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
  ports:
    - "9000:9000"   # S3 API
    - "9001:9001"   # web console UI (local debugging — http://localhost:9001)
  volumes:
    - minio-data:/data
  healthcheck:
    test: ["CMD", "curl", "-fsS", "http://localhost:9000/minio/health/live"]
    interval: 10s
    timeout: 3s
    retries: 10
    start_period: 10s
```

Notes:
- `quay.io/minio/minio` is the official current home of the image
  (Docker Hub mirrors are no longer recommended by MinIO upstream as of 2025).
- The healthcheck hits `/minio/health/live` (the unauthenticated liveness
  endpoint MinIO exposes specifically for orchestrator probes).
- For prod, pin the image tag to a dated `RELEASE.*` tag. `latest` is fine for
  local dev but unsafe for production reproducibility.

### 5.2 Bucket bootstrap — three options

1. **Sidecar `mc-init` one-shot service in compose.** A container running
   `quay.io/minio/mc` that waits on the minio service, runs `mc alias set` +
   `mc mb`, and exits. Idempotent (`mc mb --ignore-existing`).
2. **Entrypoint script in the backend.** Backend boot script calls `mc` or
   issues an S3 `CreateBucket` before launching uvicorn.
3. **FastAPI startup hook.** `head_bucket → create_bucket if 404`.

**Recommendation: sidecar `mc-init`.** Reasons:
- It's the pattern MinIO docs themselves recommend.
- It runs once at compose-up; backend replicas don't race.
- It's transparent — anyone reading the compose file sees the bucket exist.
- It uses the canonical tool (`mc`), so behaviour matches what an operator
  would do interactively.
- Backend code stays out of bootstrapping concerns.

A startup hook in FastAPI is fine as a *defensive* fallback (idempotent
`head_bucket` → log + create if missing), but the primary mechanism is the
sidecar.

### 5.3 Console UI

`http://localhost:9001` with `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`.
Lets devs browse buckets, inspect objects, check sizes, set policies. The dev
team should add `localhost:9001` and `localhost:9000` to the project README's
"local URLs" cheat sheet.

---

## 6. Python S3 client

### 6.1 Candidates

| Library | Async | Notes |
|---|---|---|
| `boto3` | sync | Blocks the event loop; needs `run_in_threadpool` per call. Bad fit. |
| `aiobotocore` | yes (lower-level) | Async port of `botocore`. Client-style API only. |
| `aioboto3` | yes (higher-level) | Wraps `aiobotocore`; gives back the boto3-resource API. |
| `minio` (official) | **no** | Sync only. Each call would need `asyncio.to_thread`. |

Reading the matrix: `minio` is the official MinIO client but is sync, which
forces a thread-pool wrap on every call from FastAPI — pointless when a native
async option exists. `aiobotocore` is correct but slightly lower-level than
needed. `aioboto3` gives us the boto3 ergonomics most Python devs already know.

### 6.2 Pick: **`aioboto3`**

Reasons:
1. **Native async.** Works inside FastAPI's loop without thread offloading.
2. **Same API as boto3.** Easy onboarding for anyone with AWS experience.
3. **MinIO speaks the S3 API**, and `aioboto3` is the leading async S3 client.
   MinIO compatibility is just a matter of `endpoint_url=`.
4. **Presigned URLs work**: `generate_presigned_url` is on `aioboto3` too.

Caveats:
- `aioboto3` v8+ made clients/resources async context managers. For a long-
  running FastAPI server we manage the client lifecycle with `AsyncExitStack`
  inside the FastAPI lifespan handler (see §6.4).

### 6.3 Minimal upload + presigned URL

```python
# app/storage/s3.py
import aioboto3
from botocore.config import Config

from app.config import settings


def _session() -> aioboto3.Session:
    return aioboto3.Session(
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        region_name="us-east-1",                  # MinIO doesn't care; boto3 wants one
    )


_S3_CLIENT_KWARGS = dict(
    endpoint_url=settings.minio_endpoint_url,     # 'http://minio:9000' inside compose
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    use_ssl=settings.minio_use_ssl,
)


async def upload_bytes(bucket: str, key: str, body: bytes, content_type: str) -> None:
    async with _session().client("s3", **_S3_CLIENT_KWARGS) as s3:
        await s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )


async def presigned_get(bucket: str, key: str, expires_in: int = 900) -> str:
    async with _session().client("s3", **_S3_CLIENT_KWARGS) as s3:
        return await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
```

`signature_version="s3v4"` + `addressing_style="path"` is the MinIO-friendly
combination. Virtual-hosted-style addressing would require DNS wildcards we
don't have in compose.

### 6.4 Lifespan-managed client (production pattern)

Recreating a client per call is fine for upload/download but wastes a TCP
connection setup on each request. For a busy backend, manage one client per
process via the FastAPI lifespan:

```python
# app/main.py
from contextlib import asynccontextmanager, AsyncExitStack
from fastapi import FastAPI
import aioboto3
from botocore.config import Config
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    stack = AsyncExitStack()
    session = aioboto3.Session(
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        region_name="us-east-1",
    )
    s3 = await stack.enter_async_context(
        session.client(
            "s3",
            endpoint_url=settings.minio_endpoint_url,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            use_ssl=settings.minio_use_ssl,
        )
    )
    app.state.s3 = s3
    try:
        yield
    finally:
        await stack.aclose()


app = FastAPI(lifespan=lifespan)
```

Then route handlers and helpers read `request.app.state.s3` instead of
opening a fresh client.

---

## 7. Bucket layout

### 7.1 Bucket count

**Pick: single bucket per environment, named `kloc-agent-artifacts-{env}`** —
e.g. `kloc-agent-artifacts-dev`, `kloc-agent-artifacts-prod`.

Why not bucket-per-tenant: we have no tenants in PoC, and even when we add
them, S3-style ACL/policy isolation works at the prefix level just fine for
our threat model (analyst can only see their own session's artifacts because
the backend is the only thing that issues presigned URLs).

Why not single global bucket: separating environments at the bucket level is
the cheapest way to keep prod data from showing up in dev console listings
during debugging. Same MinIO instance can host multiple buckets, costs
nothing.

### 7.2 Object key scheme

```
sessions/{session_id}/artifacts/{artifact_id}/{filename}
```

Concretely:
```
sessions/d3a52f96-1d6d-4f9c-a36f-3f4f1c4a3e98/artifacts/c5cf...e2/report.pdf
sessions/d3a52f96-.../artifacts/c5cf.../report.pdf.partial         # uploads-in-progress, never registered
```

Justification:
- **Prefix-by-session** makes "delete a session's artifacts" a single
  `mc rm --recursive` (or, programmatically, `list-objects + delete-objects`).
- **`artifact_id` inside the prefix** means filename collisions are impossible
  (two reports both named `report.pdf` get different prefixes), and we never
  have to rename uploads to deduplicate.
- **Keeping `filename` at the leaf** preserves the user-meaningful name for
  presigned-URL `response-content-disposition` headers and for browser
  downloads — the browser sees `report.pdf`, not a UUID.
- Lexicographic prefix groupings are also cheap on listing endpoints in MinIO.

---

## 8. Artifact lifecycle

### 8.1 Flow

```
┌──────────┐  1. write file to tempdir       ┌──────────┐
│  Runner  │ ─────────────────────────────► │  Runner  │
│ (agent)  │                                 │ (agent)  │
└──────────┘                                 └────┬─────┘
                                                  │ 2. PUT via aioboto3 / mc
                                                  │    (creds set at spawn time)
                                                  ▼
                                            ┌──────────┐
                                            │  MinIO   │
                                            │ (S3 API) │
                                            └────▲─────┘
       3. POST /webhook/artifact_uploaded        │
       { session_id, message_id, artifact_id,    │
         object_key, sha256, size, content_type }│
┌──────────┐                                     │
│ Backend  │ ◄───────────────────────────────────┘
│ (FastAPI)│  4. INSERT INTO artifact_metadata
└────┬─────┘     (UNIQUE on session_id + object_key
     │            absorbs retries)
     │ 5. emit AG-UI event to frontend
     │    'artifact_ready' { artifact_id }
     ▼
┌──────────┐
│ Frontend │  6. GET /artifacts/{id}
│  (Next)  │ ─────────────────────────────► Backend returns presigned URL
└──────────┘  7. GET presigned URL ─────► MinIO (direct)
```

### 8.2 Why upload-direct-then-webhook

- The backend never proxies bytes. Bandwidth stays on the runner↔MinIO path.
- The webhook is the single point where the row gets created; everything
  authoritative lives in Postgres.
- Frontend never gets MinIO credentials; presigned URLs are short-lived
  (15 minutes default).

### 8.3 Failure modes & recovery

**Orphan uploads.** The runner uploads to MinIO but the webhook never reaches
the backend (network blip, runner crashes, etc.). Object exists in MinIO with
no row in `artifact_metadata`.

*Mitigation: scheduled sweep.* A nightly job:
1. Lists objects under `sessions/{session_id}/artifacts/*` (paginated).
2. For each object, checks `artifact_metadata` for a row matching
   `(bucket, object_key)`.
3. Objects with no row and older than a threshold (24h) are deleted.

Run as a separate FastAPI CLI subcommand (`python -m app.cli sweep-orphans`)
on a scheduled basis. Threshold matters: don't delete the artifact a runner
is currently mid-upload on.

**Webhook duplication.** Runner retries the webhook because it didn't see the
ack. The `UNIQUE (session_id, object_key)` constraint on `artifact_metadata`
turns the duplicate into a no-op. Webhook handler should catch
`IntegrityError` and return 200 with `created=false`.

**Stale presigned URL.** URLs are short-lived, so a user opening a tab from
yesterday will get an expired URL. Frontend re-requests
`GET /artifacts/{id}` to get a fresh one. Backend can also issue longer-lived
URLs for confirmed-completed artifacts if the UI demands it; 15 minutes is
the default for "I'm about to click the link".

**Runner has wrong creds.** Runner is spawned with a service-account
key/secret scoped to the artifact bucket only (set as env vars at spawn).
If creds are wrong, upload fails fast and the runner can surface the error
as a tool error to the agent loop.

---

## 9. TTL / retention

For PoC: **artifacts live forever.** Postgres rows in `artifact_metadata` and
matching MinIO objects are kept until manual cleanup or session deletion (the
`ON DELETE CASCADE` on `session_id` removes the metadata row; a hook in the
session-delete service deletes the corresponding MinIO objects via a prefix
listing).

For later, MinIO supports S3-style lifecycle rules. To auto-expire artifacts
older than N days:

```bash
mc ilm rule add \
  --expire-days 90 \
  --prefix sessions/ \
  myminio/kloc-agent-artifacts-prod
```

Or via the S3 API (`put_bucket_lifecycle_configuration`). No need to wire
this for PoC, but it's a few-line change when we do.

---

## 10. Docker compose snippet

Self-contained chunk; drop into `docker-compose.yml`. Assumes `.env` provides
the secrets. Uses health-condition `depends_on` so app boot order is
deterministic.

```yaml
services:

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "5432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 5s

  minio:
    image: quay.io/minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    ports:
      - "9000:9000"   # S3 API
      - "9001:9001"   # console UI — http://localhost:9001
    volumes:
      - minio-data:/data
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 3s
      retries: 10
      start_period: 10s

  mc-init:
    image: quay.io/minio/mc:latest
    depends_on:
      minio:
        condition: service_healthy
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
      ARTIFACT_BUCKET: ${ARTIFACT_BUCKET}        # e.g. kloc-agent-artifacts-dev
    entrypoint: >
      /bin/sh -c "
      set -e;
      /usr/bin/mc alias set local http://minio:9000 \"$$MINIO_ROOT_USER\" \"$$MINIO_ROOT_PASSWORD\";
      /usr/bin/mc mb --ignore-existing local/$$ARTIFACT_BUCKET;
      /usr/bin/mc anonymous set none local/$$ARTIFACT_BUCKET;
      echo 'mc-init done';
      "
    restart: on-failure

  backend-migrate:
    build:
      context: ./backend
    command: ["alembic", "upgrade", "head"]
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"

  backend:
    build:
      context: ./backend
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      MINIO_ENDPOINT_URL: http://minio:9000
      MINIO_ACCESS_KEY: ${MINIO_ROOT_USER}
      MINIO_SECRET_KEY: ${MINIO_ROOT_PASSWORD}
      MINIO_USE_SSL: "false"
      ARTIFACT_BUCKET: ${ARTIFACT_BUCKET}
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      minio:
        condition: service_healthy
      backend-migrate:
        condition: service_completed_successfully
      mc-init:
        condition: service_completed_successfully

volumes:
  postgres-data:
  minio-data:
```

Companion `.env.example`:

```
POSTGRES_USER=kloc
POSTGRES_PASSWORD=changeme
POSTGRES_DB=kloc_agent

MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin

ARTIFACT_BUCKET=kloc-agent-artifacts-dev
```

For prod, swap `minio:latest` to a pinned `RELEASE.YYYY-MM-DDTHH-MM-SSZ` tag,
rotate `MINIO_ROOT_*` to a real secret, and either pin `postgres:16-alpine`
to a patch tag or accept the minor-version churn.

---

## Decisions summary

| Decision | Pick |
|---|---|
| ORM | SQLAlchemy 2.0 async + `asyncpg` driver |
| Migrations | Alembic async, run via one-shot compose service (`backend-migrate`) |
| Streaming persistence | Batched UPDATE with 256-char / 250-ms debounce, server-side concat |
| S3 client | `aioboto3`, lifespan-managed client on `app.state.s3` |
| Bucket layout | Single bucket per env: `kloc-agent-artifacts-{env}` |
| Object key | `sessions/{session_id}/artifacts/{artifact_id}/{filename}` |
| Bucket bootstrap | `mc-init` sidecar in compose, idempotent `mb --ignore-existing` |
| Retention | Unlimited for PoC; lifecycle rules as a future flip |

## Open questions (defer)

- Do we want soft delete on sessions or hard delete? Schema supports either.
- Do we want a `tool_invocations` table separate from `audit_log` for fast
  tool-replay UI, or is `audit_log` enough? Defer — `audit_log` is enough until
  it isn't.
- Multi-tenant key separation: when we add tenants, do we put `tenant_id` in
  the object key prefix or in the bucket name? Probably prefix, since MinIO
  bucket count caps are not friendly at scale.

---

## References

- SQLAlchemy 2.0 asyncio — https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- Alembic cookbook — https://alembic.sqlalchemy.org/en/latest/cookbook.html
- MinIO container docs — https://docs.min.io/enterprise/aistor-object-store/installation/container/
- MinIO `mc` reference — https://docs.min.io/enterprise/aistor-object-store/reference/cli/
- aioboto3 — https://aioboto3.readthedocs.io/ and https://github.com/terricain/aioboto3
- MinIO Python (sync, official) — https://github.com/minio/minio-py
- FastAPI best practices — https://github.com/zhanymkanov/fastapi-best-practices
- Compose bootstrap example — https://banach.net.pl/posts/2025/creating-bucket-automatically-on-local-minio-with-docker-compose/
- Async SQLAlchemy + Alembic walkthrough — https://berkkaraal.com/blog/2024/09/19/setup-fastapi-project-with-async-sqlalchemy-2-alembic-postgresql-and-docker/
