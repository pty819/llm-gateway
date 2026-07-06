"""Add marketplace browse columns (download/like counts, readme) and like tables.

Revision ID: 20260630_0012
Revises: 20260630_0011
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes

from alembic import op

revision: str = "20260630_0012"
down_revision: str | None = "20260630_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- add count + readme columns to skills ---
    op.add_column(
        "skills",
        sa.Column("readme", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        "skills",
        sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "skills",
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_skills_download_count", "skills", ["download_count"])
    op.create_index("ix_skills_like_count", "skills", ["like_count"])

    # --- add count columns to mcps ---
    op.add_column(
        "mcps",
        sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "mcps",
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_mcps_download_count", "mcps", ["download_count"])
    op.create_index("ix_mcps_like_count", "mcps", ["like_count"])

    # --- skill_likes ---
    op.create_table(
        "skill_likes",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_id", "skill_id", name="uq_skill_likes_subject_skill"),
    )
    op.create_index("ix_skill_likes_subject_id", "skill_likes", ["subject_id"])
    op.create_index("ix_skill_likes_skill_id", "skill_likes", ["skill_id"])

    # --- mcp_likes ---
    op.create_table(
        "mcp_likes",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("mcp_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.ForeignKeyConstraint(["mcp_id"], ["mcps.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_id", "mcp_id", name="uq_mcp_likes_subject_mcp"),
    )
    op.create_index("ix_mcp_likes_subject_id", "mcp_likes", ["subject_id"])
    op.create_index("ix_mcp_likes_mcp_id", "mcp_likes", ["mcp_id"])


def downgrade() -> None:
    op.drop_table("mcp_likes")
    op.drop_table("skill_likes")
    op.execute("DROP INDEX IF EXISTS ix_mcps_like_count")
    op.execute("DROP INDEX IF EXISTS ix_mcps_download_count")
    op.drop_column("mcps", "like_count")
    op.drop_column("mcps", "download_count")
    op.execute("DROP INDEX IF EXISTS ix_skills_like_count")
    op.execute("DROP INDEX IF EXISTS ix_skills_download_count")
    op.drop_column("skills", "like_count")
    op.drop_column("skills", "download_count")
    op.drop_column("skills", "readme")
