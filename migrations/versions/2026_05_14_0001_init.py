"""init — sessions, messages, audit_log, artifact_metadata

Revision ID: 20260514_0001
Revises:
Create Date: 2026-05-14

Hand-authored (research/04 §4.5: partial indexes, GIN opclasses, and
enum values are not reliably autogen-detected). Mirrors §1.2-1.5 in
research/04 and the ORM mapping in src/db/models.py.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260514_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ---------------- sessions ----------------
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("analyst_id", sa.Text(), nullable=False),
        sa.Column(
            "title",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'Untitled session'"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "runner_state",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'idle'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute(
        "CREATE INDEX ix_sessions_analyst_id_created_at "
        "ON sessions (analyst_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_sessions_open "
        "ON sessions (analyst_id, updated_at DESC) "
        "WHERE closed_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_sessions_metadata_gin "
        "ON sessions USING gin (metadata jsonb_path_ops)"
    )

    # ---------------- messages ----------------
    op.execute(
        "CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system', 'tool')"
    )

    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            postgresql.ENUM(name="message_role", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "content_parts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "parent_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finalized_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("session_id", "seq", name="uq_messages_session_seq"),
    )
    op.execute(
        "CREATE INDEX ix_messages_session_seq ON messages (session_id, seq)"
    )
    op.execute(
        "CREATE INDEX ix_messages_session_created ON messages (session_id, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_messages_streaming "
        "ON messages (session_id) "
        "WHERE finalized_at IS NULL"
    )

    # ---------------- audit_log ----------------
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute(
        "CREATE INDEX ix_audit_session_created ON audit_log (session_id, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_audit_event_type_created "
        "ON audit_log (event_type, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_audit_payload_gin "
        "ON audit_log USING gin (payload jsonb_path_ops)"
    )

    # ---------------- artifact_metadata ----------------
    op.create_table(
        "artifact_metadata",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "session_id",
            "object_key",
            name="uq_artifact_metadata_session_id",
        ),
    )
    op.execute(
        "CREATE INDEX ix_artifact_session_created "
        "ON artifact_metadata (session_id, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_artifact_message "
        "ON artifact_metadata (message_id) "
        "WHERE message_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_artifact_sha256 ON artifact_metadata (sha256)"
    )


def downgrade() -> None:
    op.drop_table("artifact_metadata")
    op.drop_table("audit_log")
    op.drop_table("messages")
    op.execute("DROP TYPE IF EXISTS message_role")
    op.drop_table("sessions")
