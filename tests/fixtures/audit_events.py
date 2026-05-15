"""Audit event vocabulary — verbatim mirror of plan §"Audit event vocabulary".

Plan reference: `docs/specs/kloc-agent-poc-plan.md` lines 560–580 (LOCKED).
QA scenario assertions match these names by string equality. The single
source of truth in the implementation is
`src/db/models.py:AuditEventType = Literal[...]` (dev-1, Phase 1.B).

QA imports these constants so scenario stubs avoid stringly-typed
assertions; tightening C3 (the audit-name pinning concern from the
Phase 1+2 QA notes).
"""
from __future__ import annotations

from typing import Final

# Session lifecycle
SESSION_OPENED: Final = "session_opened"
SESSION_CLOSED: Final = "session_closed"

# Streaming + persistence
MESSAGE_PERSISTED: Final = "message_persisted"
STREAM_ORPHANED: Final = "stream_orphaned"

# Tool-call lifecycle (BeforeToolCall / AfterToolCall + deny / crash)
TOOL_CALL_STARTED: Final = "tool_call.started"
TOOL_CALL_COMPLETED: Final = "tool_call.completed"
TOOL_CALL_DENIED: Final = "tool_call.denied"
TOOL_CALL_CRASHED: Final = "tool_call.crashed"

# Runner lifecycle
RUNNER_SPAWNED: Final = "runner_spawned"
RUNNER_WARM_IDLE_EVICTED: Final = "runner_warm_idle_evicted"
RUNNER_HEARTBEAT_LOST: Final = "runner_heartbeat_lost"

# Artifacts
ARTIFACT_REGISTERED: Final = "artifact_registered"


ALL_EVENTS: Final[frozenset[str]] = frozenset(
    {
        SESSION_OPENED,
        SESSION_CLOSED,
        MESSAGE_PERSISTED,
        STREAM_ORPHANED,
        TOOL_CALL_STARTED,
        TOOL_CALL_COMPLETED,
        TOOL_CALL_DENIED,
        TOOL_CALL_CRASHED,
        RUNNER_SPAWNED,
        RUNNER_WARM_IDLE_EVICTED,
        RUNNER_HEARTBEAT_LOST,
        ARTIFACT_REGISTERED,
    }
)
"""All 12 locked audit event names. Use to detect spec drift."""
