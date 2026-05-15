# Research 03 — Per-Session Agent Runner: Management & Isolation

> Scope: how the **backend** spawns, talks to, and tears down **per-session runner processes** that execute the Strands agent loop. Two concrete modes: **subprocess** (PoC) and **Docker per session** (self-hosted production). Both sit behind one interface so the choice is a config swap. AgentCore is explicitly out of scope. Persistence, AG-UI, and reference projects are covered elsewhere.

---

## 0. Mental model

The runner is **ephemeral and stateless**. All durable state — messages, audit, artifacts — lives in Postgres + MinIO on the backend. A runner is just "a process that runs `agent.stream_async()` against a hydrated context, emits events back to the backend, and dies." This shapes every decision below:

- We never auto-restart a runner. Crash = backend records it and lets the user click resume.
- We can evict a runner whenever we want; resuming just rehydrates from DB.
- A runner has no privileged data: if it dies mid-tool-call, nothing leaks.

The interface (§4) is small because the runner does so little.

---

## 1. Subprocess pattern (Python `asyncio.subprocess`)

### 1.1 The four things to get right

1. **Spawn with `create_subprocess_exec`** (not `_shell`). Pass an explicit argv list; no shell interpolation, no quoting bugs.
2. **Two concurrent readers, one writer.** stdout, stderr, stdin are *independent* `StreamReader`/`StreamWriter` objects. The classic deadlock is "fill the OS pipe buffer for stdout while you're waiting on stderr." Either spawn separate reader tasks **or** use `proc.communicate()` (the latter only works for one-shot, bounded output — not us).
3. **`await drain()` on every stdin write.** `write()` is sync and unbounded; `drain()` is the backpressure handle. Without it a slow runner will balloon the writer buffer.
4. **`terminate()` → wait-with-timeout → `kill()`.** SIGTERM first, give it a few seconds to flush, SIGKILL if it stalls. On Windows `terminate()` and `kill()` are the same call.

### 1.2 Parent-death detection (Linux)

If the **backend** crashes hard, runners must die too — otherwise we leak processes forever. There is no portable Python API for this; on Linux the answer is `prctl(PR_SET_PDEATHSIG, SIGTERM)` called from the child *after* fork *before* exec via `preexec_fn`:

```python
import ctypes, signal
_LIBC = ctypes.CDLL("libc.so.6", use_errno=True)
PR_SET_PDEATHSIG = 1

def _set_pdeathsig():
    # Runs in the forked child before exec. SIGTERM if parent dies.
    _LIBC.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
```

Caveats: `preexec_fn` is Unix-only and not safe inside a multi-threaded parent (FastAPI is fine — we spawn from the asyncio main loop, not a worker thread). On macOS there's no equivalent; for the PoC we accept that. Production isolation is Docker anyway, where the docker daemon owns cleanup.

### 1.3 Backpressure-safe stdout reader

`StreamReader.readline()` respects the `limit` constructor arg (default 64 KiB per line). If the runner writes a giant JSON blob on one line, raise the limit at spawn time. Otherwise the line is rejected and the reader stalls.

```python
proc = await asyncio.create_subprocess_exec(
    sys.executable, "-m", "kloc_agent.runner",
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    limit=1024 * 1024,           # 1 MiB lines; events are JSON
    preexec_fn=_set_pdeathsig,   # Linux: die with parent
)
```

### 1.4 Production-grade skeleton

```python
# kloc_agent/runner_mgmt/subprocess_runner.py
import asyncio, json, signal, sys
from dataclasses import dataclass
from typing import AsyncIterator

@dataclass
class SubprocessHandle:
    session_id: str
    proc: asyncio.subprocess.Process
    _stderr_task: asyncio.Task          # drains stderr to logs


class SubprocessRunner:
    async def spawn(self, session_id: str, hydration: dict) -> SubprocessHandle:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "kloc_agent.runner",
            "--session-id", session_id,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024,
            preexec_fn=_set_pdeathsig if sys.platform == "linux" else None,
        )
        # Push hydration as the very first stdin frame (§6).
        proc.stdin.write((json.dumps({"type": "hydrate", "payload": hydration}) + "\n").encode())
        await proc.stdin.drain()

        stderr_task = asyncio.create_task(_drain_stderr(session_id, proc.stderr))
        return SubprocessHandle(session_id, proc, stderr_task)

    async def send_user_message(self, h: SubprocessHandle, message: str) -> None:
        if h.proc.returncode is not None:
            raise RunnerGone(h.session_id)
        h.proc.stdin.write((json.dumps({"type": "user_message", "text": message}) + "\n").encode())
        await h.proc.stdin.drain()

    async def stream_events(self, h: SubprocessHandle) -> AsyncIterator[dict]:
        while True:
            line = await h.proc.stdout.readline()
            if not line:                              # EOF -> child exited
                return
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # Treat as opaque diagnostic, don't crash the consumer.
                yield {"type": "runner_warning", "raw": line.decode(errors="replace")}

    async def terminate(self, h: SubprocessHandle, *, graceful_timeout: float = 5.0) -> int:
        if h.proc.returncode is not None:
            return h.proc.returncode
        h.proc.terminate()                            # SIGTERM
        try:
            await asyncio.wait_for(h.proc.wait(), timeout=graceful_timeout)
        except asyncio.TimeoutError:
            h.proc.kill()                             # SIGKILL
            await h.proc.wait()
        h._stderr_task.cancel()
        return h.proc.returncode

    def is_alive(self, h: SubprocessHandle) -> bool:
        return h.proc.returncode is None


async def _drain_stderr(session_id: str, stderr: asyncio.StreamReader) -> None:
    while True:
        line = await stderr.readline()
        if not line:
            return
        # Hand to structured logger; do not block on it.
        log.warning("runner.stderr", session_id=session_id, line=line.decode(errors="replace").rstrip())
```

Things to notice:

- `stream_events` is a clean async generator. The backend's session endpoint can `async for ev in runner.stream_events(h): await broker.publish(...)` and call it a day.
- `stderr` always has a drainer task. If you forget, a chatty runner will eventually fill its 64KiB stderr pipe buffer and block on `print()`.
- The `RunnerGone` raise on `send_user_message` lets the backend decide: spawn a fresh runner and replay, or surface "session expired" to the user.

---

## 2. Docker pattern (per-session container)

### 2.1 Library choice — recommendation: **aiodocker**

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| `docker` (docker-py, sync) | Official, mature, exhaustive API | Sync — every call must go through `run_in_executor`/`asyncio.to_thread`. Streaming logs in a generator means a thread parked forever per session. Doesn't compose with FastAPI's async stack. | Avoid for our use case. |
| `aiodocker` (asyncio + aiohttp) | True async/await, native streaming, low overhead, active maintenance (v0.26.0 Feb 2026, Python 3.10+) | Smaller community than docker-py; some edge cases in attach/stream API have rougher docs | **Recommended.** |
| Shelling out to `docker` CLI | No Python deps; trivial to inspect | Fragile parsing, no structured errors, weird cleanup on crash, awkward stream multiplexing | No. |

We are an async backend that holds long-lived per-session streams. We need to read from N container stdouts concurrently without burning N threads. That's aiodocker's exact niche. Snyk's "inactive" label is stale — releases have continued through 2025/2026.

If aiodocker gets in our way, **docker-py wrapped in `asyncio.to_thread`** is a viable plan B, accepting one extra thread per active session (we typically expect < 100 active sessions, so this is fine — just less elegant).

### 2.2 Container lifecycle

```
pull image (once at backend startup; not per-session)
  └─ create container (config + HostConfig)
        └─ attach to stdin/stdout/stderr stream  (BEFORE start, to not miss output)
              └─ start container
                    └─ async for frame in stream: emit events
                          └─ when stream ends: container.wait() to learn exit code
                                └─ container.delete(force=True)
```

Two non-obvious points:

- **Attach before start.** If you `start()` first then `attach()`, you can miss the first frames. aiodocker's `attach()` returns a `Stream` that, combined with `OpenStdin=True`, gives you bidi.
- **`logs()` vs `attach()`**: `logs(follow=True)` is simpler (read-only). `attach()` is needed because we also need to send user messages on stdin.

```python
config = {
    "Image": "kloc-agent-runner:latest",
    "Cmd": ["python", "-m", "kloc_agent.runner", "--session-id", session_id],
    "AttachStdin": True, "AttachStdout": True, "AttachStderr": True,
    "OpenStdin": True, "StdinOnce": False,
    "Tty": False,                              # framed multiplexed bytes; we want JSON lines
    "Env": [f"BACKEND_URL={BACKEND_URL}", f"MCP_URL={MCP_URL}", f"SESSION_ID={session_id}"],
    "HostConfig": {
        "Memory":        1 * 1024 * 1024 * 1024,   # 1 GiB
        "MemorySwap":    1 * 1024 * 1024 * 1024,   # no swap allowance
        "NanoCpus":      2_000_000_000,             # 2 vCPU
        "PidsLimit":     256,
        "RestartPolicy": {"Name": "no"},            # never auto-restart
        "NetworkMode":   "kloc-agent_default",      # see §2.3
        "AutoRemove":    False,                     # we delete explicitly to grab exit code
    },
    "Labels": {
        "kloc.session_id": session_id,
        "kloc.role":       "runner",
    },
}
```

### 2.3 Networking — recommendation: **shared docker-compose bridge network**

Both backend and runner live in our docker-compose project. Compose creates one default bridge network per project; every service gets DNS = service name. The runner POSTs to `http://backend:8000/internal/...` and dials the MCP server at its known service hostname. No `network_mode: host`, no socket bind-mounts.

Alternatives we considered:

| Option | Verdict |
|---|---|
| Bridge network with service hostname | **Pick this.** Default behavior, DNS works, isolation is fine. |
| `network_mode: host` | No isolation, port collisions, breaks on macOS docker desktop. No. |
| Unix socket bind-mount | Tight coupling backend ↔ runner; doesn't help us across machines later. No. |
| `network_mode: container:<backend>` | Shares the backend's net namespace — couples lifetimes weirdly. No. |
| Per-session network | Overkill; default bridge already prevents cross-runner traffic at the L7 (they don't know each other's hostnames). |

The runner is spawned by the backend via the Docker API and is *not* a compose service. It still needs to join the compose project's network — pass `NetworkMode: "<project>_default"` in `HostConfig`. Discover the name with `docker network ls` or hard-code it via `COMPOSE_PROJECT_NAME`.

The backend therefore needs the docker socket. Two options:

1. **Bind-mount `/var/run/docker.sock`** into the backend container (and keep the backend privileged-ish). Standard, works, but the backend can now do anything to the host docker. Mitigate by trusting the image and not exposing this to user input.
2. **Docker-in-Docker**: heavier, slower, mostly overkill for self-hosted production.

Recommendation: bind-mount, document the trust boundary, label every runner with `kloc.role=runner` so a sweep is trivial.

### 2.4 MCP subprocess — recommendation: **sibling container, not nested**

The intelligence MCP server is *already* a deployable service we host. The runner should talk to it the same way the backend does — over the compose network. The runner does **not** spawn its own MCP via stdio. That keeps:

- One MCP process serving all sessions (vs N).
- Restart/upgrade of MCP decoupled from runner lifecycle.
- Cleaner observability — MCP traces aren't trapped inside ephemeral containers.

If we eventually want a per-session, project-specific MCP (e.g. each session indexes a different repo), the answer is *a second container per session*, not nesting inside the runner. The runner gets `MCP_URL` env var and is otherwise agnostic.

### 2.5 Resource limits

| Limit | Default for PoC | Rationale |
|---|---|---|
| `Memory` | 1 GiB | LLM workloads + skills + a few in-flight tool results. |
| `NanoCpus` | 2 vCPU | Mostly waiting on the model; CPU is not the bottleneck. |
| `PidsLimit` | 256 | Catches fork bombs / runaway tools. |
| `RestartPolicy` | `no` | **Never** auto-restart. Backend records the death and surfaces "resume" to the user. |
| `AutoRemove` | `false` | We want to inspect exit code & last lines after exit. Backend removes explicitly. |

Override these via env (`RUNNER_MEMORY_MB`, `RUNNER_CPU`) so a deployer can tune.

### 2.6 Cleanup

On normal `terminate(handle)`:
1. `container.stop(t=5)` — SIGTERM with 5s grace, then SIGKILL by docker.
2. `container.wait()` — pick up exit code.
3. `container.delete(force=True)`.

On *backend* crash: orphaned containers survive. On boot the backend runs a **sweeper**: `docker ps --filter label=kloc.role=runner`, look up each `session_id` label in Postgres, and either reattach (rare; we'd need the original stream state, which we don't keep — see §3) or kill & delete. Simplest policy: **kill all surviving runners on backend boot**; their sessions are already idle from the user's perspective and a fresh runner will be spawned on resume. This is sound because runners are stateless.

### 2.7 Dockerfile shape

```dockerfile
FROM python:3.12-slim

# uv for fast installs in the build, but plain pip would also work
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# App code is mounted read-only in dev, baked in for prod.
COPY src/ /app/src/

# Skills live outside the image so deployers update without rebuilds.
# Path is overridable via env; default matches docker-compose mount.
ENV SKILLS_DIR=/skills
VOLUME ["/skills"]

# Runner reads JSON frames on stdin, emits on stdout.
ENTRYPOINT ["python", "-m", "kloc_agent.runner"]
```

Skills mount: `-v ./skills:/skills:ro`. Read-only is non-negotiable — a skill must never modify itself.

---

## 3. IPC: runner → backend event stream

Three things flow runner ↔ backend:

- **Events** (runner → backend, high-volume, streaming): Strands `stream_async` events — text deltas, tool calls, tool results, lifecycle, errors.
- **User messages** (backend → runner, low-volume): one JSON frame per user turn.
- **Hook webhooks** (runner → backend, low-volume, separate transport): tool-call hooks per the poc.md "hooks are the policy layer" rule. These are **already** specified as HTTP webhooks — they stay HTTP. Not in scope here.

### 3.1 Options for the event stream

| Option | Subprocess fit | Docker fit | Notes |
|---|---|---|---|
| **stdio (JSONL)** | ★★★ native | ★★ works via attach websocket but is awkward to plumb | Zero infra dependency, trivial to debug (just print) |
| HTTP POST per event | ★★ wasteful | ★★ wasteful | TCP handshake per token is silly |
| **HTTP streaming POST (chunked, JSONL body)** | ★★ same fd as stdio, no win | ★★★ one persistent connection, clean across the bridge | Backend exposes `POST /internal/sessions/{id}/events` that reads chunked JSON lines |
| Redis pub/sub | ★ adds infra for PoC | ★ adds infra | Loses messages if backend reconnects later; fine for fanout but we already do fanout via SSE to the frontend, on the *backend*. Pushing it earlier in the chain buys nothing. |
| Redis Streams | ★ adds infra | ★ adds infra | Durable replay, but DB already has durable replay (the audit log). Redundant. |
| gRPC bidi | ★ heavy | ★ heavy | Schema benefits don't outweigh the dependency mass for PoC |
| Unix domain socket | ★★ nice for subprocess | ✗ not portable to docker without bind-mount | Tempting for subprocess but not uniform. |

### 3.2 Recommendation: **JSONL over stdio for subprocess, JSONL over chunked HTTP for Docker — uniform JSONL wire format**

Make the **wire format** uniform across both modes:

- Newline-delimited JSON.
- Every line is one event object: `{"type": "...", "session_id": "...", "ts": ..., ...}`.

Make the **transport** differ:

- **Subprocess mode**: events on stdout, user messages on stdin. Cheapest possible — no sockets.
- **Docker mode**: runner POSTs `POST /internal/sessions/{id}/events` with `Transfer-Encoding: chunked` and a body that streams JSONL frames. The same endpoint accepts a long-lived connection that lasts the runner's life. Inbound user messages go the other way: runner does `GET /internal/sessions/{id}/inbox` (long-poll or SSE) — or simpler, the backend `POST`s into the runner via the runner's tiny built-in HTTP server (one endpoint, localhost-only on the container's network).

The runner implementation does *both* by abstracting an `EventChannel` with two methods (`emit(event)`, `iter_inbound() -> AsyncIterator[Message]`). Two concrete impls: `StdioChannel`, `HttpChannel`. Identical event payloads, identical backend handling logic. The subprocess version is what you run locally; the docker version is what you ship.

> Why not stdio in Docker too? It works (the docker daemon multiplexes the attach stream), but you then need to keep an aiodocker websocket open for *every* runner from the FastAPI process and demultiplex frames. HTTP is what every Python developer already knows; the docker network is right there. Cost of one extra connection per session is negligible.

---

## 4. Single `Runner` interface

Goal: a `Protocol` so backend code never branches on "subprocess vs docker." Spawning, sending, streaming, terminating, liveness — all five verbs are the same regardless of mode.

```python
# kloc_agent/runner_mgmt/protocol.py
from __future__ import annotations
from typing import Protocol, AsyncIterator, runtime_checkable
from dataclasses import dataclass

@dataclass
class HydrationPayload:
    session_id: str
    system_prompt: str
    prior_messages: list[dict]              # [{role, content, ts}]
    mcp_endpoints: list[str]                # ["http://intel-mcp:8080"]
    project_context: dict                   # repo refs, scope, etc.
    skills_dir: str = "/skills"             # path in runner's filesystem
    model_id: str = "anthropic/claude-opus-4-7"

class RunnerHandle(Protocol):
    """Opaque to callers — internal to each backend impl."""
    session_id: str

@runtime_checkable
class Runner(Protocol):
    async def spawn(self, payload: HydrationPayload) -> RunnerHandle: ...
    async def send_user_message(self, handle: RunnerHandle, message: str) -> None: ...
    def stream_events(self, handle: RunnerHandle) -> AsyncIterator[dict]: ...
    async def terminate(self, handle: RunnerHandle, *, graceful_timeout: float = 5.0) -> int: ...
    def is_alive(self, handle: RunnerHandle) -> bool: ...


# kloc_agent/runner_mgmt/docker_runner.py
import aiodocker, asyncio, json
from typing import AsyncIterator
from .protocol import Runner, HydrationPayload

class DockerRunnerHandle:
    def __init__(self, session_id: str, container, inbound_queue: asyncio.Queue):
        self.session_id = session_id
        self.container = container
        self.inbound_queue = inbound_queue   # backend pushes user msgs here; runner pulls via HTTP

class DockerRunner:
    def __init__(self, image: str, network: str, backend_url: str, mcp_url: str):
        self._docker = aiodocker.Docker()
        self._image = image
        self._network = network
        self._backend_url = backend_url
        self._mcp_url = mcp_url

    async def spawn(self, payload: HydrationPayload) -> DockerRunnerHandle:
        # Persist hydration to a temp file the runner mounts read-only — simpler than streaming
        # it on stdin, and one less moving part inside the docker entrypoint. See §6.
        hydration_path = await _write_hydration_blob(payload)
        config = _make_container_config(
            image=self._image, network=self._network,
            backend_url=self._backend_url, mcp_url=self._mcp_url,
            session_id=payload.session_id, hydration_path=hydration_path,
        )
        container = await self._docker.containers.create(config=config)
        await container.start()
        return DockerRunnerHandle(payload.session_id, container, asyncio.Queue())

    async def send_user_message(self, h, message: str) -> None:
        # Backend-internal queue; runner picks it up by long-polling /internal/sessions/{id}/inbox
        await h.inbound_queue.put({"type": "user_message", "text": message})

    async def stream_events(self, h) -> AsyncIterator[dict]:
        # The runner POSTs JSONL events to the backend; FastAPI persists & republishes.
        # *This* iterator reads from the in-process pub channel keyed on session_id.
        async for ev in event_bus.subscribe(h.session_id):
            yield ev

    async def terminate(self, h, *, graceful_timeout: float = 5.0) -> int:
        try:
            await h.container.stop(t=int(graceful_timeout))
        except aiodocker.exceptions.DockerError:
            pass
        result = await h.container.wait()
        await h.container.delete(force=True)
        return result.get("StatusCode", -1)

    def is_alive(self, h) -> bool:
        # Cached status; refresh lazily. Avoid hammering the docker socket per call.
        return getattr(h, "_dead", False) is False
```

Subprocess impl is in §1.4. Both satisfy `Runner` and differ only in transport.

The backend keeps a per-session lookup: `dict[session_id, RunnerHandle]`. The session endpoint resolves to one of two code paths via DI, never by `isinstance`.

---

## 5. Eviction policy

### 5.1 Rule

A runner is evicted when **no events have been emitted for N minutes** (default 15, env `RUNNER_IDLE_TIMEOUT_S=900`). "Event" includes the runner's own heartbeat (§8), so a stuck-but-alive runner stays alive; a runner that has gone silent (model finished, user walked away) gets killed.

Because state lives in the DB, eviction is trivial: call `runner.terminate(handle)` and forget. On user reconnect we spawn fresh and rehydrate from DB.

### 5.2 Implementation: backend asyncio sweeper

A single backend coroutine started at FastAPI lifespan startup:

```python
async def eviction_sweeper(runner_registry: dict, idle_timeout_s: int = 900):
    while True:
        await asyncio.sleep(30)               # tick every 30s
        now = time.monotonic()
        victims = [
            sid for sid, (h, last_event_ts) in runner_registry.items()
            if now - last_event_ts > idle_timeout_s
        ]
        for sid in victims:
            handle, _ = runner_registry.pop(sid)
            log.info("evicting.idle_runner", session_id=sid)
            try:
                await asyncio.wait_for(runner.terminate(handle, graceful_timeout=5), timeout=10)
            except Exception:
                log.exception("eviction.failed", session_id=sid)
            await db.mark_session_evicted(sid, reason="idle_timeout")
```

`last_event_ts` is bumped by the stream-events pipeline on every emit. A more correct version uses an `asyncio.Event` per session and `wait_for(event.wait(), timeout=idle_timeout_s)` so we don't poll, but the sweeper above is dead simple, runs every 30s, and is enough for the PoC. Move to per-session timers only if we observe sweeper lag.

**Do not** introduce Celery for this. It's one async task; Celery would bring a broker, workers, and a deployment story for what should be 20 lines of code.

### 5.3 Forced eviction

`POST /internal/sessions/{id}/evict` for ops & for the "delete session" user action. Same code path.

---

## 6. Hydration on resume

### 6.1 Payload (minimum viable)

```python
HydrationPayload(
    session_id="...",
    system_prompt="You are a code-intelligence research agent ...",
    prior_messages=[
        {"role": "user",      "content": "...", "ts": "..."},
        {"role": "assistant", "content": "...", "ts": "..."},
        # ... full conversation; we will start with no summarization in PoC.
    ],
    mcp_endpoints=["http://intel-mcp:8080/sse"],
    project_context={"repo": "kloc/intelligence", "ref": "main"},
    skills_dir="/skills",
    model_id="anthropic/claude-opus-4-7",
)
```

The runner reconstructs a fresh `Agent` with this prompt + history and is ready for the next user message.

### 6.2 Transport options

| Option | Pros | Cons |
|---|---|---|
| **CLI args** | Easy | Will not fit (`prior_messages` is unbounded). No. |
| **Env vars** | Easy | Same problem. Tiny things (session_id, urls) fit — large structures don't. Use for *configuration*, not *state*. |
| **First stdin frame** (subprocess only) | Uniform with the rest of the wire | Doesn't translate to Docker without complicating entrypoint |
| **Mounted JSON file** (`/run/kloc/hydration.json`) | Works for both modes uniformly; runner just reads a file | One extra fs op; a temp file to clean up |
| HTTP fetch from backend at startup | Cleanest mental model | Adds a startup HTTP dance and a chicken-and-egg with the event channel |

**Recommendation: mounted JSON file for Docker, first-stdin-frame for subprocess.** Both impls of `Runner.spawn` write the same `HydrationPayload` dict to where the runner expects it. The runner has one bootstrap path:

```python
# kloc_agent/runner/__main__.py (sketch)
def _read_hydration() -> dict:
    p = os.environ.get("KLOC_HYDRATION_PATH")
    if p:                                          # docker mode
        with open(p) as f: return json.load(f)
    # subprocess mode: first line on stdin is the hydration frame
    first = sys.stdin.readline()
    return json.loads(first)["payload"]
```

This is the only place modes diverge inside the runner. Everything after is identical.

### 6.3 Token-limit guardrail

`prior_messages` can outgrow the model's context. The backend should compute a summary or sliding-window before passing the payload — but that belongs to the persistence/conversation-mgmt research stream, not here. The runner trusts the payload it gets.

---

## 7. Crash handling

### 7.1 Detection signals (in order of latency)

1. **`returncode is not None`** on the subprocess handle (subprocess mode) — checked every event read & every send.
2. **Container `State` != `running`** from aiodocker (docker mode) — checked on send & on periodic sweep.
3. **No heartbeat for >30s** (§8) — overlay across both modes; covers "stuck inside model" or "stuck inside MCP call."
4. **Backend boot scan** — kill+delete any container labelled `kloc.role=runner` whose session isn't in our `runner_registry`.

### 7.2 User-facing UX

```
state         | UI
--------------+----------------------------------------------------
running       | normal streaming
evicted-idle  | "Session paused. Click to resume." (resume = respawn)
crashed       | "Session interrupted. Resume?" + Sentry/audit link
              | (resume = respawn + replay last user message)
terminated    | grey "Session ended"
```

State lives in Postgres on the session row. The frontend reads it via the same SSE channel; the backend emits a `session.state.changed` event when transitions happen.

### 7.3 Audit log entry shape

```json
{
  "session_id": "sess_abc",
  "ts": "2026-05-14T12:34:56Z",
  "kind": "runner.crashed",
  "runner_mode": "docker",
  "exit_code": 137,
  "signal": "SIGKILL",
  "container_id": "abc123...",
  "last_event_kind": "tool_call.in_flight",
  "last_event_tool": "intel.semantic_search",
  "diagnostic": "OOMKilled by docker (Memory limit 1GiB)",
  "tail_stderr": "... last 4KiB ..."
}
```

The `last_event_kind` is what lets us implement §7.4.

### 7.4 Idempotency on resume

A tool call mid-flight at crash time:

- We **do not replay** the tool call automatically. The MCP server may not be idempotent. Replays are dangerous unless we know the tool's semantics.
- We **mark the tool call `crashed`** in the audit log and the session message stream.
- The new runner sees the prior conversation up to and including the user's last turn, but not the partial tool call. It will probably re-issue an equivalent call — that's the model's decision, not ours. If the tool is non-idempotent, this is the same risk as a human user clicking "retry" — acceptable.
- Exception: if/when we add a notion of "safe to retry" on a per-tool basis (in hook policy), the backend can auto-replay. Out of scope for PoC.

---

## 8. Heartbeat / liveness

### 8.1 Recommendation: **runner emits `heartbeat` every 15s**, even when idle

Cheap, makes the eviction sweeper trivial, and gives the frontend a clean signal that "yes the agent is thinking" vs "the runner stalled." It also gives docker mode a liveness signal independent of the HTTP keep-alive (a dead runner can still hold a TCP socket open until the kernel notices).

```jsonl
{"type":"heartbeat","session_id":"sess_abc","ts":"2026-05-14T12:34:56Z","busy":true,"active_tool":"intel.semantic_search"}
{"type":"heartbeat","session_id":"sess_abc","ts":"2026-05-14T12:35:11Z","busy":false}
```

- `busy=true` keeps the UI's "Agent is working..." indicator on.
- `busy=false` is what the eviction sweeper uses to consider the session idle (in combination with no real activity).

### 8.2 Backend treatment

The sweeper considers a runner **dead** if no heartbeat in 30s. That's separate from **idle eviction** (no real event in 15min). Two thresholds, two reasons to terminate; same `terminate(handle)` outcome.

### 8.3 Why not "just trust the stream is open"

Because TCP keep-alive defaults are *minutes*. A docker mode runner that segfaults inside model.invoke() will hold its event stream open until the OS notices the dead peer. A heartbeat closes that gap. For subprocess mode, EOF on stdout is immediate, so the heartbeat is mostly informational. Same code on the producer side either way.

---

## 9. Quick decisions summary

| Question | Decision |
|---|---|
| Library for Docker control | `aiodocker` |
| MCP runs where | Sibling service, not inside the runner |
| Networking | Default compose bridge; runner joins `<project>_default` |
| Wire format | JSONL, uniform across modes |
| Transport | stdio (subprocess) / chunked HTTP POST (docker) |
| Hydration | mounted JSON file (docker) / first stdin frame (subprocess) |
| Eviction | Idle 15 min, asyncio sweeper on backend |
| Auto-restart | Never — backend owns lifecycle |
| Heartbeat | Yes, 15s interval, runner-emitted |
| Tool-call replay on crash | No — surface "interrupted" to user |
| Docker socket access | Backend bind-mounts `/var/run/docker.sock`, labels every runner |
| Resource limits | 1 GiB / 2 vCPU / 256 pids (env-tunable) |
| Cleanup on backend boot | Kill+delete all `kloc.role=runner` containers |

---

## 10. References

- Python asyncio subprocess: <https://docs.python.org/3/library/asyncio-subprocess.html>
- aiodocker docs: <https://aiodocker.readthedocs.io/en/latest/>
- aiodocker repo & examples: <https://github.com/aio-libs/aiodocker>
- docker-py SDK (sync) reference for comparison: <https://docker-py.readthedocs.io/en/stable/>
- Streaming subprocess stdin/stdout pattern: <https://kevinmccarthy.org/2016/07/25/streaming-subprocess-stdin-and-stdout-with-asyncio-in-python/>
- `PR_SET_PDEATHSIG` from Python: <https://blog.raylu.net/2021/04/01/set_pdeathsig.html>
- Docker Compose networking & service hostnames: <https://docs.docker.com/compose/how-tos/networking/>
- FastAPI sync-in-threadpool: <https://fastapi.tiangolo.com/async/>
- Strands `stream_async` async iterators: <https://strandsagents.com/latest/user-guide/concepts/streaming/async-iterators/>
- Redis pub/sub vs streams (considered & rejected for our case): <https://redis.io/glossary/pub-sub/>
