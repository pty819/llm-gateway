"""Add readme column to mcps.

Revision ID: 20260701_0013
Revises: 20260630_0012
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes

from alembic import op

revision: str = "20260701_0013"
down_revision: str | None = "20260630_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("mcps", sa.Column("readme", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    op.drop_column("mcps", "readme")
