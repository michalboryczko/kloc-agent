"""tool_calls — durable per-tool-call rows linked to assistant messages

Revision ID: 20260525_0001
Revises: 20260514_0001
Create Date: 2026-05-25

Adds the `tool_calls` table that backs the chat-history restoration of
tool cards. Each row is keyed by `(session_id, tool_call_id)` so a
re-emitted START is absorbed by `INSERT ... ON CONFLICT DO UPDATE`, and
`parent_message_id` (NOT NULL FK to `messages.id`) makes the FE's per-
message grouping a direct lookup rather than an event-stream replay.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260525_0001"
down_revision: str | Sequence[str] | None = "20260514_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_calls",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tool_call_id", sa.Text(), nullable=False),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column(
            "args",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("denied_reason", sa.Text(), nullable=True),
        sa.Column("denied_hint", sa.Text(), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finalized_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('running', 'done', 'denied')",
            name="ck_tool_calls_state",
        ),
        sa.UniqueConstraint(
            "session_id",
            "tool_call_id",
            name="uq_tool_calls_session_tool_id",
        ),
    )
    op.execute(
        "CREATE INDEX ix_tool_calls_parent_message "
        "ON tool_calls (parent_message_id)"
    )
    op.execute(
        "CREATE INDEX ix_tool_calls_session_seq "
        "ON tool_calls (session_id, seq)"
    )


def downgrade() -> None:
    op.drop_table("tool_calls")
