---
title: kloc-agent frontend rewrite — full-stack delivery via feature-team
slug: kloc-agent-frontend-rewrite
created: 2026-05-18
project: /Users/michal/dev/ai/kloc/kloc-agent
tags: [session-summary]
---

# kloc-agent frontend rewrite — full-stack delivery via feature-team

## TL;DR

Rewrote `kloc-agent/frontend/` from scratch (Next.js 16 + React 19 + Tailwind 4 + `@ag-ui/client` direct, **no CopilotKit**) covering the full `docs/behavior.xml` scope (UC1–UC5). Run was orchestrated as a feature-team (`pm`, `architect`, `developer-1`, `developer-2`, `reviewer-1`, `qa` under `team-lead`) producing 17 commits on `master`. Final state: all UCs PASS in Chrome browser QA except for items naturally unexercisable in one session (artifact emission, retry path, mid-stream cut); the only outstanding NFR miss is `nfr.progressive-rendering` p95 < 1s — currently ~6.8s due to upstream LLM + cold runner spawn, not a FE bug.

## Goal of the session

User asked to rewrite the kloc-agent frontend from zero. Stack to be `@ag-ui/client` direct (drop CopilotKit which was in the old build), modern web design per `docs/specs/ui/kloc-analyst.html` (light theme) and `docs/specs/ui/img_1.png` (dark theme). Backend was to remain untouched in spirit but small scoped patches were approved later (CUSTOM AG-UI events for `ToolCallDenied` and `ArtifactAttached` + SSE `seq` stamping for replay).

Scope evolved twice during the session:
1. After QA's first Phase-6 report, three "blockers" were not FE bugs but environmental: backend OTel logs exporter defaulting to empty `otlp` (hanging runners), Tailwind 4 token-bracket bug, and stale runner Docker image — all fixed.
2. A user-reported "second message does nothing" symptom turned out to be the `RUNNER_WARM_IDLE_S=60s` evicting runners during typical read-then-reply cadence, forcing 20-25s cold spawn on turn 2. Bumped to 300s.

## What we did

- **Spawned a feature-team** (`/feature-team-run kloc-agent-frontend --auto`) under team `kloc-agent-frontend`. Six members: pm, architect, developer-1, developer-2, reviewer-1, qa. Coordinated 17 commits across three streams and one PoC validation pass.
- **PM** wrote spec at `/Users/michal/dev/ai/kloc/kloc-agent/docs/specs/kloc-agent-frontend.md` (Gherkin AC for all 5 UCs, NFR table, visual token table, browser-QA requirement). Amended four times to lock CUSTOM event shapes, recovery-window definition, backend pre-reqs, and tool-call state normalization (running/done/denied).
- **Architect** wrote implementation plan at `docs/specs/kloc-agent-frontend-plan.md` with disjoint file ownership across Stream A (scaffold/theme/shell/sidebar/REST), Stream B (AG-UI run loop, reducer, conversation components, reconnect), Stream C (three small backend patches).
- **QA** wrote test plan at `.claude/qa-notes/kloc-agent-frontend_qa_ref_note.md` mandating Chrome browser automation against every Gherkin scenario.
- **Stream A** (dev-1): scaffold from zero, `lib/types.ts` FROZEN first, theme tokens, layout, landing page, conversation shell, sidebar, REST client, agent-proxy SSE forward routes. Commits `87459e59c` + `01c8aed0b` + `b91a9e8a7` + `4f2b40424`.
- **Stream B** (dev-2): AG-UI run loop (`lib/agui.ts` hand-rolled SSE consumer over `fetch().body.getReader()`), pure reducer for 12 AG-UI event variants + 2 CUSTOM events, conversation components (Conversation/Thread/AnalystBubble/AssistantBubble/ToolCallCard/ArtifactChip/CodeChip/BlinkingCaret/InputBar/ConnectionBanner), reconnect with `?last_event_id=<seq>`. Commits `678dadea6` + `d6e0d9a6a` + `ae0fc8a1e` + `bfb481d9e` + `e71e3b5b6` + `8aa05a4a2`.
- **Stream C** (dev-2): three runner-side patches in `runner/hooks/audit.py` for ToolCallDenied + ArtifactAttached CUSTOM events, plus `src/api/internal.py` + `src/streaming/sse.py` for seq stamping (dual-channel: SSE `id:` line + payload field). Tests in `tests/unit/test_audit_hooks_custom_events.py` + `tests/streaming/test_sse.py`. Commits `abd8dd1a4` + `d5b10cfdc`.
- **Reviewer-1** ran code-reviewer skill across all three streams. Verdict pattern: Stream A loop-2 APPROVE, Stream B loop-3 APPROVE, Stream C loop-1 APPROVE. Caught three real Importants on Stream A (SSR env-var split, missing `data-test` attrs) and three on Stream B (data-test selector mismatches with QA notes).
- **Project-wide Tailwind 4 fix** (`3287e65da`, dev-1): discovered during QA Phase 6 that Tailwind 4 stable does not unwrap bare `bg-[--color-X]` arbitrary-value brackets — the entire token-bound styling layer was emitting no CSS. Swept all `[--color-X]` → `[var(--color-X)]` and `[--font-X]` → `[var(--font-X)]` across both Stream A and Stream B files.
- **RunAgentInput body fix** (`8aa05a4a2`, dev-2): FE was sending `{sessionId, runId, run_id, messages:[{role,content}]}`. AG-UI requires `{thread_id, run_id, messages:[{id, role, content}]}`. Backend silently accepted the wrong shape but Strands could not drive the loop → runner died at 30s heartbeat. Fixed agui.ts + agent-proxy/route.ts body parse.
- **Backend OTel fix** (`59676f002`, lead): added `OTEL_LOGS_EXPORTER: ${OTEL_LOGS_EXPORTER:-console}` to backend compose env. The default `otlp` against an empty endpoint blocked all log emission, runners never sent heartbeats, audit log shows **system had never had a successful assistant turn in 7 days prior**. After fix: backend → runner → agent turns complete cleanly via `runner_warm_idle_evicted`.
- **Stale runner image** (lead, no commit): `kloc-agent-runner:dev` was built 2026-05-17 13:06, one day before Stream C patches landed. Pre-Stream-C runners had no `ToolCallDenied` CUSTOM emission code at all. Rebuilt the image with `docker build -f runner/Dockerfile -t kloc-agent-runner:dev .`, force-evicted lingering warm runners.
- **Warm-idle bump** (`60e652af7`, lead): `RUNNER_WARM_IDLE_S` 60 → 300 in docker-compose.yml. User reported "second message does nothing"; QA reproduction showed the second submit DOES work but takes 20-25s because the 60s warm-idle had evicted the runner during typical read cadence, forcing cold respawn.

## Key findings / numbers

| Metric | Value | Source |
|---|---|---|
| Total feature commits on `master` | 17 | `git log --oneline c4f63ce40..HEAD` |
| Stream C new tests passing | 20 / 20 | `pytest tests/unit/test_audit_hooks_custom_events.py tests/streaming/test_sse.py -q` |
| Full kloc-agent unit + integration suite | 180 / 180 | `pytest tests/unit tests/integration -q` (per dev-2's report) |
| Frontend bundle gzipped (landing) | ~67 KB (largest chunk) / ~132 KB total | dev-1's `npm run build` output |
| Bundle budget | < 200 KB compressed | spec §NFR engineering constraints |
| Backend stack | `docker compose up -d backend postgres minio` | `docker-compose.yml` |
| Backend host port | **8002** (not 8000 — port 8000 conflicts with another local Symfony container) | `frontend/.env.local` |
| TTFT measured (single-shot, cold runner) | ~6.8s | QA Phase 6 final report |
| TTFT NFR threshold | < 1000ms p95 | spec table from `docs/behavior.xml` `nfr.progressive-rendering` |
| `RUNNER_WARM_IDLE_S` | 300s (was 60s default) | `docker-compose.yml:101` |
| `RUNNER_HEARTBEAT_TIMEOUT_S` | 30s (unchanged) | `docker-compose.yml:102` |
| `ExecutionRegistry` replay window | 5 min after `RUN_FINISHED` | `src/streaming/execution_registry.py` |
| `KLOC_DENY_TOOLS` for denial QA | `file_read` (NOT `read_file` — canonical kloc-intelligence MCP tool name) | spec §Backend Pre-requisites for Browser QA |

## Files touched

**Created (FE):**
- `/Users/michal/dev/ai/kloc/kloc-agent/frontend/` — entire new directory; Next.js 16 standalone app
- `/Users/michal/dev/ai/kloc/kloc-agent/frontend/package.json`, `package-lock.json`, `tsconfig.json`, `next.config.ts`, `eslint.config.mjs`, `postcss.config.mjs`, `Dockerfile`, `.env.local.example`, `.gitignore`, `.dockerignore`
- `frontend/src/app/layout.tsx`, `globals.css`, `page.tsx` (landing), `s/[sessionId]/page.tsx` (conversation), `s/[sessionId]/ConversationClient.tsx`
- `frontend/src/app/api/agent-proxy/route.ts` (POST), `frontend/src/app/api/agent-proxy/resume/route.ts` (GET)
- `frontend/src/lib/types.ts` (frozen interface contract — 12 AG-UI event variants + 2 CUSTOM payloads + 6 ConnectionStates + ReducerAction union + view models)
- `frontend/src/lib/api.ts` (REST client w/ SSR-vs-browser URL split), `cn.ts`, `theme.ts`, `time.ts`, `agui.ts` (hand-rolled SSE parser), `reducer.ts` (pure reducer), `runLoop.ts` (useRunLoop hook)
- `frontend/src/styles/fonts.ts` (self-hosted Geist + JetBrains Mono via `next/font`)
- `frontend/src/components/`: Shell, Sidebar, SessionListItem, NewSessionButton, ThemeToggle, RailFooter, EmptyState, ErrorBanner, ConversationHeader, Conversation, Thread, AnalystBubble, AssistantBubble, ToolCallCard, ArtifactChip, CodeChip, BlinkingCaret, InputBar, ConnectionBanner, icons (inline SVG set)

**Created (backend tests):**
- `kloc-agent/tests/unit/test_audit_hooks_custom_events.py` — 11 cases for Stream C CUSTOM events
- `kloc-agent/tests/streaming/test_sse.py`, `tests/streaming/__init__.py` — 7+ cases for seq stamping

**Created (planning):**
- `kloc-agent/docs/specs/kloc-agent-frontend.md` (PM spec)
- `kloc-agent/docs/specs/kloc-agent-frontend-plan.md` (architect plan)
- `kloc-agent/.claude/qa-notes/kloc-agent-frontend_qa_ref_note.md` (QA test plan)

**Modified (backend, Stream C and lead infra):**
- `kloc-agent/runner/hooks/audit.py` — emit `CUSTOM ToolCallDenied` in three deny branches; add `register_artifact()` emitting `CUSTOM ArtifactAttached`
- `kloc-agent/src/api/internal.py` — moved ExecutionRegistry `seq` allocation here (the JSONL ingress boundary), stamps seq on wire frame before `bus.publish`
- `kloc-agent/src/api/stream.py` — removed duplicate `execution.append(wire)` in `_persist_events` (would have double-stamped seq). Also retains the prior hydration-vs-persist reorder fix.
- `kloc-agent/src/streaming/sse.py` — prepends `id: <seq>\n` to each encoded SSE frame
- `kloc-agent/tests/integration/test_stream_reconnect.py` — adjusted to publish via `_dispatch_frame` instead of `event_bus.publish` directly (same invariant, new boundary)
- `kloc-agent/docker-compose.yml` — added `OTEL_LOGS_EXPORTER: ${OTEL_LOGS_EXPORTER:-console}` (commit `59676f002`); bumped `RUNNER_WARM_IDLE_S` 60 → 300 (commit `60e652af7`)

**Read / referenced heavily:**
- `kloc-agent/docs/behavior.xml` — 5 use-cases, EARS rules, invariants, NFRs (authoritative spec)
- `kloc-agent/docs/specs/ui/kloc-analyst.html` — light-theme mockup (authoritative visual)
- `kloc-agent/docs/specs/ui/img_1.png` — dark-theme reference
- `kloc-agent/CLAUDE.md` — stack pins, comment policy (ISS-13), naming conventions
- `kloc-agent/runner/__main__.py` — runner agent loop structure (informs reducer expectations)
- `kloc-agent/src/api/sessions.py`, `stream.py`, `artifacts.py` — backend REST shapes

## Current state / where we stopped

- Master branch: `master` on kloc-agent sub-repo, 17 feature commits ahead of the pre-rewrite baseline (`c4f63ce40 chore: remove REQUIREMENTS.md for v1.0 milestone`).
- Latest commit: `60e652af7 config(runner): extend warm-idle from 60s to 300s`.
- Backend container: `kloc-agent-backend-1` healthy on host port **8002**. Env: `LLM_PROVIDER=gemini`, `GEMINI_API_KEY` set (operator-provided), `KLOC_CORS_ALLOW_ORIGINS=http://localhost:3000`, `KLOC_DENY_TOOLS=file_read`, `OTEL_LOGS_EXPORTER=console`, `RUNNER_WARM_IDLE_S=300`.
- Frontend dev server: running on `:3000`, picked up via HMR from latest commits. Log at `/tmp/kloc-fe-dev.log` (per QA).
- `kloc-agent-runner:dev` Docker image: rebuilt today after Stream C patches landed. Confirmed via `docker run --rm --entrypoint cat kloc-agent-runner:dev /app/runner/hooks/audit.py | grep ToolCallDenied`.
- `frontend/.env.local`: `NEXT_PUBLIC_BACKEND_URL=http://localhost:8002`, `BACKEND_URL=http://localhost:8002`, `NEXT_TELEMETRY_DISABLED=1`. Note: `.env.local.example` still says `:8000` — see open questions.
- Working tree (kloc-agent): hundreds of unrelated pre-existing deletions (`.agents/`, `.claude/agents/`, old CopilotKit-era frontend index files) that we left UNSTAGED. Lead-side cleanup task that was deliberately deferred.
- Reference-project Docker container (`kloc-reference-project-php-php-1`) was stopped during this session to free host port 8000; not started back up.
- Feature-team is still spawned (pm, architect, developer-1, developer-2, reviewer-1, qa all alive). Team file at `~/.claude/teams/kloc-agent-frontend/config.json`.

What we know is NOT working today:
- `nfr.progressive-rendering` p95 < 1s — measured ~6.8s single-shot; cold-runner cost. Not a FE bug.
- UC4.4 artifact attachment — code paths exist (FE reducer handles `CustomEvent.name="ArtifactAttached"`; runner emits it via `_emit_custom_event`) but no agent naturally produced an artifact during QA. Untested at runtime.
- UC4.6 retry path — code paths exist (`retry()` in runLoop.ts rolls back optimistic + resubmits, `data-test="retry-message"` button) but couldn't naturally provoke a RUN_ERROR with healthy pipeline. Untested at runtime.
- UC5.1 reconnect within window — cursor-replay code path exists and is correctly shaped per architect's plan, but Gemini streams faster than QA could cut mid-stream via DevTools throttling. Inconclusive at runtime; UC5.2 (completed-while-disconnected) and UC5.3 (window-exceeded fallback) both verified.

## Open questions / unresolved

- **TTFT NFR threshold.** Spec says < 1000ms p95. Actual ~6.8s. Either the threshold is wrong for tool-using Gemini turns or we need a warm-runner pool / faster model. Suggest revising spec.
- **`frontend/.env.local.example`** still says `BACKEND_URL=http://localhost:8000`. If the operator wants the backend on conventional port 8000 (it's now free after we stopped the reference-project), change `docker-compose.yml` to publish `8000:8000` on the backend service and revert `.env.local` to `:8000`. Currently both files diverge — example uses 8000, live `.env.local` uses 8002.
- **Reference-project container** was stopped to free port 8000. If the operator needs it running for other work, `docker start kloc-reference-project-php-php-1` brings it back (but will reoccupy port 8000).
- **Three deferred Stream B reviewer Suggestions** (S-1, S-2, S-4, S-5) on `ToolCallCard.tsx`, `Conversation.tsx`, etc. None blocking, all noted in reviewer-1's loop-1 verdict.
- **Working-tree noise** on kloc-agent: hundreds of pre-existing deletions (`.agents/skills/vercel-react-best-practices/*`, old CopilotKit-era frontend index files) unstaged. Decide whether to commit as a sweep commit or `git restore` if any were unintentional.
- **Feature-team still alive.** Decide whether to `SendMessage(type: shutdown_request)` to each member and then `TeamDelete`, or keep them alive for follow-up work.

## Recommended next steps

1. **Tear down or keep the team.** If the rewrite is shipped: send `shutdown_request` to pm, architect, developer-1, developer-2, reviewer-1, qa (in any order), wait for shutdown_response, then `TeamDelete`. Effort: 2 minutes. Why first: keeps the agents list tidy and frees their Claude budgets.
2. **Reconcile port config.** Either rebind backend to host port 8000 in compose and revert `.env.local` to match `.env.local.example`, OR update `.env.local.example` to use 8002 to match reality. Don't leave the two files inconsistent. Effort: 2 min.
3. **Decide on TTFT NFR.** Either lower the threshold to a realistic cold-spawn-aware value (e.g., < 1s for warm, ≤ 10s for cold) or invest in warm-runner-pool / model swap. Update `docs/behavior.xml` `nfr.progressive-rendering` accordingly. Effort: 30 min (spec edit) to days (warm-pool implementation).
4. **Investigate the working-tree pre-existing deletions.** Mostly old CopilotKit-era files we expected to be gone, but also `.agents/skills/vercel-react-best-practices/` which may or may not be intentional. `git restore` if needed; otherwise wrap into a `chore: remove dead frontend tree` commit. Effort: 5 min.
5. **Manually exercise UC4.4, UC4.6, UC5.1** under controlled conditions. Artifact attachment needs an agent prompt that triggers a tool which uploads (kloc-intelligence MCP may or may not have one — check). RUN_ERROR can be forced by stopping the backend mid-stream. UC5.1 cursor replay needs a slow tool path or DevTools network throttling at the right millisecond. Effort: 1-2 hours QA.
6. **Address dev-2's three deferred Stream B Suggestions** if/when polishing. Effort: 30 min total.

## User preferences observed

- **Wants autonomous multi-agent runs.** "Handle all in one run" — said multiple times directly and indirectly. Operator dislikes mid-pipeline confirmations.
- **Wants AG-UI direct, no CopilotKit.** When asked, said "go without it only ag-ui". CLAUDE.md frontend-stack section will need updating to drop the CopilotKit line.
- **Wants concise communication.** Terse end-of-turn summaries, no narrative. Pushes back on multi-paragraph explanations.
- **Wants both light AND dark themes.** When asked "darkmode - yes".
- **Wants full behavior.xml scope (no MVP cut).** "full scope".
- **Does NOT want auth UI** (single-operator PoC). "no auth".
- **Approves scoped backend patches when needed for FE correctness.** Approved the Stream C runner patches (CUSTOM events + seq stamping) when architect surfaced the gap. Also approved `OTEL_LOGS_EXPORTER` + warm-idle config changes without pushback.
- **Distrusts agents that drift.** Multiple times pushed back on stale-queue replay, reviewer pre-claiming review tasks before code existed, and reviewer sending preemptive checklists to devs.

## Key context a future session needs

- **The kloc-agent FastAPI backend is on host port 8002**, NOT 8000. Compose publishes `0.0.0.0:8002->8000/tcp`. Port 8000 was previously held by an unrelated Symfony container (`kloc-reference-project-php-php-1`). That container is currently stopped.
- **The `kloc-agent-runner:dev` Docker image is the runtime artifact for agent runs**, not the kloc-agent source tree directly. Backend spawns runner containers from this image via aiodocker. ALWAYS rebuild the image after touching `runner/` source: `docker build -f runner/Dockerfile -t kloc-agent-runner:dev .` (from `kloc-agent/`). The old image had no Stream C code at all; the runner Dockerfile copies source at build time.
- **AG-UI protocol 0.1.18 has no `TOOL_CALL_DENIED` or `ARTIFACT_ATTACHED` reserved event types.** We use AG-UI `CUSTOM` events with names `ToolCallDenied` and `ArtifactAttached` instead — value shapes locked in `frontend/src/lib/types.ts` (`ToolCallDeniedCustom`, `ArtifactAttachedCustom`) and matching runner emissions in `runner/hooks/audit.py`. The FE reducer discriminates by `(type === "CUSTOM" && name === "...")`.
- **Reconnect cursor is `?last_event_id=<seq>` query param, NOT the `Last-Event-ID` HTTP header.** Native `EventSource` can't be used for resume. Stream B uses `fetch().body.getReader()` + manual SSE parsing. The `seq` is stamped onto each frame in two places: SSE `id:` line (for any EventSource consumer) AND as a top-level field on the event payload (for `@ag-ui/client` consumers). Both wire paths agree.
- **The frozen interface contract is `frontend/src/lib/types.ts`.** Dev-1 wrote it Day 1 and signaled "types frozen" before dev-2 started Stream B. Don't duplicate types in Stream B; import from there. Note `CustomAGUIEvent` (not `CustomEvent`) to avoid DOM `CustomEvent` global shadowing.
- **Tailwind 4 stable does NOT unwrap bare CSS-custom-property identifiers in arbitrary-value brackets.** `bg-[--color-X]` emits no CSS. Always use `bg-[var(--color-X)]`. This was a project-wide latent bug; QA's first PASS on token resolution checked `:root` CSS variables, not actual painted elements. Lesson: visual regression must check computed styles on real surfaces, not just the root variables.
- **Why `@ag-ui/client` direct over CopilotKit:** CopilotKit was a UI shell on top of AG-UI. The mockup defines its own bespoke chat UI primitives, so CopilotKit's components weren't being used anyway. Dropping CopilotKit saved ~300 KB of bundle and removed the agent-proxy envelope translation layer; the agent-proxy now just byte-pipes SSE.
- **The runner audit flow is dual-channel.** Denials and artifacts go BOTH via the HMAC webhook + audit log (durable, backend-side) AND via the JSONL channel as AG-UI CUSTOM events (real-time, FE-side). The FE doesn't poll audit; it consumes CUSTOM events on the live SSE.
- **Reviewer-1 had to be hard-stopped twice** for preemptive pre-claiming of review tasks before code existed. The team-lead.md protocol is explicit: review tasks are blocked-by implementation tasks and stay `pending` until the implementing dev pings ready. Don't let reviewers send "pre-review checklists" to devs.
- **Why the bigger-than-expected Stream A fix commit `3287e65da` touched dev-2's files:** the Tailwind 4 var() bug was project-wide. Dev-1 swept the entire codebase in one commit rather than asking dev-2 to do half. Cross-stream ownership was approved by lead at the time; dev-2 acknowledged later via a peer DM.

## How to resume

> Read `.claude/sessions/kloc-agent-frontend-rewrite.md` plus `docs/specs/kloc-agent-frontend.md` (PM spec) and `docs/specs/kloc-agent-frontend-plan.md` (architect plan).
>
> Current state: feature is shipped to `master` (17 commits, latest `60e652af7`). Backend container `kloc-agent-backend-1` healthy on host port 8002. Frontend dev server still running on :3000.
>
> Tasks the user might pick from, in priority order:
> 1. Shut down the still-alive feature-team (`pm`, `architect`, `developer-1`, `developer-2`, `reviewer-1`, `qa`) via `SendMessage` with `shutdown_request`, then `TeamDelete`. Team file at `~/.claude/teams/kloc-agent-frontend/config.json`.
> 2. Reconcile the `.env.local` (8002) vs `.env.local.example` (8000) port divergence. Decide which one wins.
> 3. Either revise the `nfr.progressive-rendering` p95 threshold in `docs/behavior.xml` or add a warm-runner pool — current TTFT is ~6.8s.
> 4. Manually verify UC4.4 (artifact attachment), UC4.6 (RUN_ERROR retry), UC5.1 (cursor replay) which were not naturally exercisable during one-shot QA.
>
> Do NOT: reopen the AG-UI vs CopilotKit decision (closed), reopen the Tailwind 4 `var()` wrapping decision (closed), revert the OTel `console` exporter (it's load-bearing for runner heartbeats), or rebuild the `kloc-agent-runner:dev` image without verifying the source has the latest Stream C code first.
