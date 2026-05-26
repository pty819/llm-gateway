"""Add account auth and team-based model grants.

Revision ID: 20260525_0002
Revises: 20260524_0001
Create Date: 2026-05-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
import sqlmodel.sql.sqltypes
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260525_0002"
down_revision: str | None = "20260524_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subjects",
        sa.Column("login_username", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        "subjects",
        sa.Column("password_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        "subjects",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_subjects_login_username", "subjects", ["login_username"], unique=True
    )
    op.create_index("ix_subjects_is_admin", "subjects", ["is_admin"])
    op.alter_column("subjects", "is_admin", server_default=None)

    resource_state = postgresql.ENUM(
        "ACTIVE", "DISABLED", name="resourcestate", create_type=False
    )

    op.create_table(
        "teams",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column(
            "is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_teams_name", "teams", ["name"])
    op.create_index("ix_teams_state", "teams", ["state"])
    op.create_index("ix_teams_is_builtin", "teams", ["is_builtin"])
    op.alter_column("teams", "is_builtin", server_default=None)

    op.create_table(
        "team_memberships",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("role", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "team_id", "subject_id", name="uq_team_membership_team_subject"
        ),
    )
    op.create_index("ix_team_memberships_team_id", "team_memberships", ["team_id"])
    op.create_index(
        "ix_team_memberships_subject_id", "team_memberships", ["subject_id"]
    )
    op.create_index("ix_team_memberships_state", "team_memberships", ["state"])

    op.create_table(
        "model_team_grants",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_alias_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.ForeignKeyConstraint(["model_alias_id"], ["model_aliases.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "model_alias_id", "team_id", name="uq_model_team_grant_model_team"
        ),
    )
    op.create_index(
        "ix_model_team_grants_model_alias_id", "model_team_grants", ["model_alias_id"]
    )
    op.create_index("ix_model_team_grants_team_id", "model_team_grants", ["team_id"])
    op.create_index("ix_model_team_grants_state", "model_team_grants", ["state"])

    op.create_table(
        "user_sessions",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("token_prefix", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_sessions_subject_id", "user_sessions", ["subject_id"])
    op.create_index("ix_user_sessions_token_prefix", "user_sessions", ["token_prefix"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    op.create_index("ix_user_sessions_state", "user_sessions", ["state"])


def downgrade() -> None:
    op.drop_index("ix_user_sessions_state", table_name="user_sessions")
    op.drop_index("ix_user_sessions_expires_at", table_name="user_sessions")
    op.drop_index("ix_user_sessions_token_prefix", table_name="user_sessions")
    op.drop_index("ix_user_sessions_subject_id", table_name="user_sessions")
    op.drop_table("user_sessions")

    op.drop_index("ix_model_team_grants_state", table_name="model_team_grants")
    op.drop_index("ix_model_team_grants_team_id", table_name="model_team_grants")
    op.drop_index("ix_model_team_grants_model_alias_id", table_name="model_team_grants")
    op.drop_table("model_team_grants")

    op.drop_index("ix_team_memberships_state", table_name="team_memberships")
    op.drop_index("ix_team_memberships_subject_id", table_name="team_memberships")
    op.drop_index("ix_team_memberships_team_id", table_name="team_memberships")
    op.drop_table("team_memberships")

    op.drop_index("ix_teams_is_builtin", table_name="teams")
    op.drop_index("ix_teams_state", table_name="teams")
    op.drop_index("ix_teams_name", table_name="teams")
    op.drop_table("teams")

    op.drop_index("ix_subjects_is_admin", table_name="subjects")
    op.drop_index("ix_subjects_login_username", table_name="subjects")
    op.drop_column("subjects", "is_admin")
    op.drop_column("subjects", "password_hash")
    op.drop_column("subjects", "login_username")
