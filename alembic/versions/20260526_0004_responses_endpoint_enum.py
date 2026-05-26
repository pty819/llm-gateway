"""Ensure Responses endpoint family enum value exists.

Revision ID: 20260526_0004
Revises: 20260526_0003
Create Date: 2026-05-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260526_0004"
down_revision: str | None = "20260526_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE endpointfamily ADD VALUE IF NOT EXISTS 'OPENAI_RESPONSES'")


def downgrade() -> None:
    pass
