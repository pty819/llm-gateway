"""Add capacity analytics request facts.

Revision ID: 20260526_0003
Revises: 20260525_0002
Create Date: 2026-05-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260526_0003"
down_revision: str | None = "20260525_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE endpointfamily ADD VALUE IF NOT EXISTS 'OPENAI_RESPONSES'")
    op.add_column("request_facts", sa.Column("cached_tokens", sa.Integer()))
    op.add_column("request_facts", sa.Column("latency_ms", sa.Integer()))
    op.add_column("request_facts", sa.Column("time_to_first_token_ms", sa.Integer()))
    op.add_column("request_facts", sa.Column("stream_duration_ms", sa.Integer()))
    op.add_column(
        "request_facts",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "request_facts",
        sa.Column("fallback_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("request_facts", sa.Column("fallback_tokens", sa.Integer()))
    op.add_column("request_facts", sa.Column("queue_ms", sa.Integer()))
    op.add_column("request_facts", sa.Column("prefill_ms", sa.Integer()))
    op.add_column("request_facts", sa.Column("decode_ms", sa.Integer()))
    op.add_column("request_facts", sa.Column("kv_cache_usage", sa.Float()))
    op.add_column(
        "request_facts",
        sa.Column(
            "performance_detail",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("request_facts", "retry_count", server_default=None)
    op.alter_column("request_facts", "fallback_count", server_default=None)
    op.alter_column("request_facts", "performance_detail", server_default=None)


def downgrade() -> None:
    op.drop_column("request_facts", "performance_detail")
    op.drop_column("request_facts", "kv_cache_usage")
    op.drop_column("request_facts", "decode_ms")
    op.drop_column("request_facts", "prefill_ms")
    op.drop_column("request_facts", "queue_ms")
    op.drop_column("request_facts", "fallback_tokens")
    op.drop_column("request_facts", "fallback_count")
    op.drop_column("request_facts", "retry_count")
    op.drop_column("request_facts", "stream_duration_ms")
    op.drop_column("request_facts", "time_to_first_token_ms")
    op.drop_column("request_facts", "latency_ms")
    op.drop_column("request_facts", "cached_tokens")
