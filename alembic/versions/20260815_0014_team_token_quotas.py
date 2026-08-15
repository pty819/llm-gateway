"""Add per-team time-windowed token quotas.

Revision ID: 20260815_0014
Revises: 20260701_0013
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0014"
down_revision: str | None = "20260701_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    resource_state = postgresql.ENUM(
        "ACTIVE", "DISABLED", name="resourcestate", create_type=False
    )

    op.create_table(
        "team_token_quotas",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("morning_tokens", sa.Integer(), nullable=True),
        sa.Column("afternoon_tokens", sa.Integer(), nullable=True),
        sa.Column("evening_tokens", sa.Integer(), nullable=True),
        sa.Column("state", resource_state, nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", name="uq_team_token_quota_team"),
    )
    op.create_index("ix_team_token_quotas_team_id", "team_token_quotas", ["team_id"])
    op.create_index("ix_team_token_quotas_state", "team_token_quotas", ["state"])


def downgrade() -> None:
    op.drop_index("ix_team_token_quotas_state", table_name="team_token_quotas")
    op.drop_index("ix_team_token_quotas_team_id", table_name="team_token_quotas")
    op.drop_table("team_token_quotas")
