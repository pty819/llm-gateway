"""Add optional upstream Prometheus metrics URL.

Revision ID: 20260604_0007
Revises: 20260527_0006
Create Date: 2026-06-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260604_0007"
down_revision: str | None = "20260527_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("alter table upstream_targets add column if not exists metrics_url varchar")


def downgrade() -> None:
    op.execute("alter table upstream_targets drop column if exists metrics_url")
