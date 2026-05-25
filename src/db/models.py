"""ORM mappings + Pydantic Hydration schema.

ORM tables: sessions, messages, audit_log, artifact_metadata.
Pydantic models: HydrationPayload, McpStdioEndpoint, McpHttpEndpoint.

`AuditEventType` is the single-source-of-truth Literal of the 14 locked
audit event names.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


# Audit event vocabulary is locked: every value must be a documented event.
# Adding a new event requires a corresponding write site plus a downstream
# consumer (otherwise it is dead audit weight).

AuditEventType = Literal[
    "session_opened",
    "session_closed",
    "message_persisted",
    "stream_orphaned",
    "tool_call.started",
    "tool_call.completed",
    "tool_call.denied",
    "tool_call.crashed",
    "runner_spawned",
    "runner_warm_idle_evicted",
    "runner_heartbeat_lost",
    "artifact_registered",
    # Emitted when the JSONL ingress drops an inbound runner frame
    # because it exceeded the byte cap (oversized) or the backend
    # deterministically rejected it (e.g. invalid JSON). Payload shape:
    # {runner_id, run_id, cause: "frame_oversized" | "frame_rejected",
    #  upstream_status: int, byte_size: int}.
    "runner_channel_frame_rejected",
    # One row per AGENT.md file the runner could not load: malformed
    # frontmatter, duplicate `name`, or a `name` that collides with an
    # MCP tool advertised at runner startup. Payload shape:
    # {file: str, reason: "invalid_spec" | "duplicate_name"
    #  | "name_conflicts_with_mcp_tool", detail: str}.
    "runner_subagent_load_failed",
]


AUDIT_EVENT_TYPES: frozenset[str] = frozenset(get_args(AuditEventType))


MessageRole = Literal["user", "assistant", "system", "tool"]

_MESSAGE_ROLE_ENUM = SAEnum(
    "user",
    "assistant",
    "system",
    "tool",
    name="message_role",
    create_constraint=False,
)


# ---------------------------------------------------------------------------
# ORM tables
# ---------------------------------------------------------------------------


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    analyst_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'Untitled session'"),
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    runner_state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'idle'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index(
            "ix_sessions_analyst_id_created_at",
            "analyst_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_sessions_open",
            "analyst_id",
            text("updated_at DESC"),
            postgresql_where=text("closed_at IS NULL"),
        ),
        Index(
            "ix_sessions_metadata_gin",
            "metadata",
            postgresql_using="gin",
            postgresql_ops={"metadata": "jsonb_path_ops"},
        ),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[MessageRole] = mapped_column(_MESSAGE_ROLE_ENUM, nullable=False)
    content: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    content_parts: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    parent_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)

    session: Mapped[Session] = relationship(back_populates="messages")

    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_messages_session_seq"),
        Index("ix_messages_session_seq", "session_id", "seq"),
        Index("ix_messages_session_created", "session_id", "created_at"),
        Index(
            "ix_messages_streaming",
            "session_id",
            postgresql_where=text("finalized_at IS NULL"),
        ),
    )


ToolCallState = Literal["running", "done", "denied"]


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tool_call_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    args: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[ToolCallState] = mapped_column(Text, nullable=False)
    denied_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    denied_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('running', 'done', 'denied')",
            name="ck_tool_calls_state",
        ),
        UniqueConstraint(
            "session_id",
            "tool_call_id",
            name="uq_tool_calls_session_tool_id",
        ),
        Index("ix_tool_calls_parent_message", "parent_message_id"),
        Index("ix_tool_calls_session_seq", "session_id", "seq"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=True,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_audit_session_created", "session_id", "created_at"),
        Index("ix_audit_event_type_created", "event_type", "created_at"),
        Index(
            "ix_audit_payload_gin",
            "payload",
            postgresql_using="gin",
            postgresql_ops={"payload": "jsonb_path_ops"},
        ),
    )


class ArtifactMetadata(Base):
    __tablename__ = "artifact_metadata"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "object_key",
            name="uq_artifact_metadata_session_id",
        ),
        Index("ix_artifact_session_created", "session_id", "created_at"),
        Index(
            "ix_artifact_message",
            "message_id",
            postgresql_where=text("message_id IS NOT NULL"),
        ),
        Index("ix_artifact_sha256", "sha256"),
    )


# ---------------------------------------------------------------------------
# Pydantic models — Contract D (hydration payload schema)
# ---------------------------------------------------------------------------


class McpStdioEndpoint(BaseModel):
    """Stdio MCP child-process spec embedded in the hydration payload."""

    command: str
    args: list[str]
    env: dict[str, str] = Field(default_factory=dict)


class McpHttpEndpoint(BaseModel):
    """Streamable-HTTP MCP endpoint spec (MCP 2025-03-26).

    The runner POSTs JSON-RPC messages to `url` and consumes the
    streaming response. Used to reach kloc-intelligence's
    `mcp-server-http` subcommand at e.g.
    `http://host.docker.internal:8765/mcp` — the operator brings up
    kloc-intelligence (Neo4j + Qdrant + the HTTP MCP server) out of band.
    """

    url: str
    # Optional auth — for PoC unset; future: bearer token / mTLS.


class HydrationPayload(BaseModel):
    """JSON payload bind-mounted into the runner container.

    Path inside the container: `/run/kloc/hydration.json`.
    Backend writes to `/tmp/hydration-<rid>.json` on the host; aiodocker
    mounts read-only.

    See Contract D in docs/specs/kloc-agent-poc-plan.md.
    """

    session_id: str
    run_id: str
    runner_id: str
    runner_secret: str

    # Agent construction
    system_prompt: str
    model_id: str
    # Runner-side `model_factory.create_model` only wires gemini.
    llm_provider: Literal["gemini"]
    prior_messages: list[dict[str, Any]]
    state: dict[str, Any] = Field(default_factory=dict)

    # MCP — runner may receive stdio child-process specs or HTTP
    # (Streamable-HTTP) endpoint URLs. Mixed lists are allowed.
    mcp_endpoints: list[McpStdioEndpoint | McpHttpEndpoint]

    # Skills
    skills_dir: str = "/skills"

    # Subagents — `/agents` is the parallel runner-side surface to
    # `/skills`. The backend seeds the `kloc-agents` named volume from
    # the repo-root `./agents/` tree; the runner reads `<name>/AGENT.md`
    # via `runner/agents_loader.py` at agent build time.
    agents_dir: str = "/agents"

    # Project sources — `/projects` is the runner-side surface for the
    # `read_project_file` tool. Backend seeds the `kloc-projects` named
    # volume from the repo-root `./projects/` tree; the runner reads
    # `<project_name>/<path>` on demand. Override seam for the runner-
    # side tool resolver.
    projects_dir: str = "/projects"

    # Channel
    backend_url: str
    heartbeat_interval_s: int = 15

    # PGMQ inbox transport. `pg_dsn` is the asyncpg URL the runner uses
    # to LISTEN + pgmq.read; `inbox_queue` is the per-session queue name
    # (deterministic from session_id, see
    # `src/messaging/pgmq.py:inbox_queue_name`).
    pg_dsn: str
    inbox_queue: str
