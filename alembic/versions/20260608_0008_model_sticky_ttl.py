"""Add model alias sticky routing TTL.

Revision ID: 20260608_0008
Revises: 20260604_0007
Create Date: 2026-06-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260608_0008"
down_revision: str | None = "20260604_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "alter table model_aliases "
        "add column if not exists sticky_ttl_seconds integer not null default 1200"
    )


def downgrade() -> None:
    op.execute("alter table model_aliases drop column if exists sticky_ttl_seconds")
