"""Add marketplace skills and mcps tables.

Revision ID: 20260630_0011
Revises: 20260629_0010
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260630_0011"
down_revision: str | None = "20260629_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    resource_state = postgresql.ENUM("ACTIVE", "DISABLED", name="resourcestate", create_type=False)
    mcp_transport = postgresql.ENUM("STDIO", "HTTP", "SSE", name="mcptransport", create_type=False)
    # `resourcestate` already exists from migration 0001; `mcptransport` is new
    # and must be created explicitly because create_type=False suppresses the
    # implicit DDL. checkfirst=True keeps this idempotent.
    mcp_transport.create(op.get_bind(), checkfirst=True)

    # --- skills ---
    op.create_table(
        "skills",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_subject_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("state", resource_state, nullable=False),
        sa.Column("latest_version", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["owner_subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_subject_id", "slug", name="uq_skill_owner_slug"),
    )
    op.create_index("ix_skills_owner_subject_id", "skills", ["owner_subject_id"])
    op.create_index("ix_skills_slug", "skills", ["slug"])
    op.create_index("ix_skills_state", "skills", ["state"])
    op.create_index("ix_skills_latest_version", "skills", ["latest_version"])

    # --- skill_versions ---
    op.create_table(
        "skill_versions",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("content_blob", sa.LargeBinary(), nullable=False),
        sa.Column("content_sha256", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("upload_subject_id", sa.Uuid(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"]),
        sa.ForeignKeyConstraint(["upload_subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_version_skill_version"),
    )
    op.create_index("ix_skill_versions_skill_id", "skill_versions", ["skill_id"])
    op.create_index("ix_skill_versions_version", "skill_versions", ["version"])
    op.create_index("ix_skill_versions_content_sha256", "skill_versions", ["content_sha256"])
    op.create_index("ix_skill_versions_state", "skill_versions", ["state"])

    # --- mcps ---
    op.create_table(
        "mcps",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_subject_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("state", resource_state, nullable=False),
        sa.Column("latest_version", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["owner_subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_subject_id", "slug", name="uq_mcp_owner_slug"),
    )
    op.create_index("ix_mcps_owner_subject_id", "mcps", ["owner_subject_id"])
    op.create_index("ix_mcps_slug", "mcps", ["slug"])
    op.create_index("ix_mcps_state", "mcps", ["state"])
    op.create_index("ix_mcps_latest_version", "mcps", ["latest_version"])

    # --- mcp_versions ---
    op.create_table(
        "mcp_versions",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mcp_id", sa.Uuid(), nullable=False),
        sa.Column("version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("transport", mcp_transport, nullable=False),
        sa.Column("command", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("args", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("env", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("headers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tools", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("upload_subject_id", sa.Uuid(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.ForeignKeyConstraint(["mcp_id"], ["mcps.id"]),
        sa.ForeignKeyConstraint(["upload_subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mcp_id", "version", name="uq_mcp_version_mcp_version"),
    )
    op.create_index("ix_mcp_versions_mcp_id", "mcp_versions", ["mcp_id"])
    op.create_index("ix_mcp_versions_version", "mcp_versions", ["version"])
    op.create_index("ix_mcp_versions_transport", "mcp_versions", ["transport"])
    op.create_index("ix_mcp_versions_state", "mcp_versions", ["state"])

    # --- skill_team_grants ---
    op.create_table(
        "skill_team_grants",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "team_id", name="uq_skill_team_grant_skill_team"),
    )
    op.create_index("ix_skill_team_grants_skill_id", "skill_team_grants", ["skill_id"])
    op.create_index("ix_skill_team_grants_team_id", "skill_team_grants", ["team_id"])
    op.create_index("ix_skill_team_grants_state", "skill_team_grants", ["state"])

    # --- mcp_team_grants ---
    op.create_table(
        "mcp_team_grants",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mcp_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.ForeignKeyConstraint(["mcp_id"], ["mcps.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mcp_id", "team_id", name="uq_mcp_team_grant_mcp_team"),
    )
    op.create_index("ix_mcp_team_grants_mcp_id", "mcp_team_grants", ["mcp_id"])
    op.create_index("ix_mcp_team_grants_team_id", "mcp_team_grants", ["team_id"])
    op.create_index("ix_mcp_team_grants_state", "mcp_team_grants", ["state"])


def downgrade() -> None:
    for table in (
        "mcp_team_grants",
        "skill_team_grants",
        "mcp_versions",
        "mcps",
        "skill_versions",
        "skills",
    ):
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_state")
        op.drop_table(table)
    # Drop the enum type created in this migration; resourcestate is left intact
    # (owned by migration 0001).
    op.execute("DROP TYPE IF EXISTS mcptransport")
