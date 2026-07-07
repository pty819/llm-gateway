"""Add ON DELETE behavior to marketplace foreign keys.

Revision ID: 20260707_0015
Revises: 20260701_0013
Create Date: 2026-07-07

Migration 0009 added ON DELETE to the core tables but predated the
marketplace tables (added in 0011/0012). Their FKs were still NO ACTION, so
deleting a user left orphaned skills/versions/grants/likes, and deleting a
skill left orphaned versions. This migration mirrors 0009's approach:

* Owner FKs (skills/mcps -> subjects) and grant/like FKs -> CASCADE: deleting
  a user or team removes the marketplace artifacts and their grants/likes.
* Version tables -> CASCADE on both FKs. upload_subject_id is NOT NULL, so
  SET NULL is impossible (it would raise NotNullViolation); CASCADE removes
  the version row when its uploader is deleted. Note this is a weaker
  history guarantee than core-table SET NULL in 0009, but those tables
  (request_facts/audit_events) had nullable FK columns; the marketplace
  version tables do not.

These tables are small config/child tables, so inline validation is fine
(no NOT VALID/VALIDATE dance needed, unlike the big append-only tables in
0009). Constraint names are PG's defaults (<table>_<column>_fkey), confirmed
against migrations 0011/0012.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260707_0015"
down_revision: str | None = "20260701_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, column, constraint_name, referenced_table)
_CASCADE = [
    ("skills", "owner_subject_id", "skills_owner_subject_id_fkey", "subjects"),
    ("mcps", "owner_subject_id", "mcps_owner_subject_id_fkey", "subjects"),
    ("skill_versions", "skill_id", "skill_versions_skill_id_fkey", "skills"),
    (
        "skill_versions",
        "upload_subject_id",
        "skill_versions_upload_subject_id_fkey",
        "subjects",
    ),
    ("mcp_versions", "mcp_id", "mcp_versions_mcp_id_fkey", "mcps"),
    (
        "mcp_versions",
        "upload_subject_id",
        "mcp_versions_upload_subject_id_fkey",
        "subjects",
    ),
    ("skill_team_grants", "skill_id", "skill_team_grants_skill_id_fkey", "skills"),
    ("skill_team_grants", "team_id", "skill_team_grants_team_id_fkey", "teams"),
    ("mcp_team_grants", "mcp_id", "mcp_team_grants_mcp_id_fkey", "mcps"),
    ("mcp_team_grants", "team_id", "mcp_team_grants_team_id_fkey", "teams"),
    ("skill_likes", "subject_id", "skill_likes_subject_id_fkey", "subjects"),
    ("skill_likes", "skill_id", "skill_likes_skill_id_fkey", "skills"),
    ("mcp_likes", "subject_id", "mcp_likes_subject_id_fkey", "subjects"),
    ("mcp_likes", "mcp_id", "mcp_likes_mcp_id_fkey", "mcps"),
]


def upgrade() -> None:
    for table, column, name, ref in _CASCADE:
        op.drop_constraint(name, table)
        op.create_foreign_key(name, table, ref, [column], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    # Restore NO ACTION (the implicit default) on every FK.
    for table, column, name, ref in _CASCADE:
        op.drop_constraint(name, table)
        op.create_foreign_key(name, table, ref, [column], ["id"])
