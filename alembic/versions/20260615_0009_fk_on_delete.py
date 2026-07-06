"""Add ON DELETE behavior to foreign keys.

Revision ID: 20260615_0009
Revises: 20260608_0008
Create Date: 2026-06-15

Historical/append-only tables (request_facts, audit_events) get ON DELETE SET
NULL so deleting a subject/project/upstream can never destroy usage facts or
the audit trail; the column is nullable, so the facts row is preserved with the
reference cleared. Config/child tables get ON DELETE CASCADE so deleting a
parent cleans up its memberships/keys/sessions/entitlements/upstreams/grants
without the application having to enumerate every referencing table.

These SET NULL constraints are added NOT VALID then VALIDATEd so the scan of
the (potentially large) request_facts / audit_events tables does not hold an
ACCESS EXCLUSIVE lock for the duration; VALIDATE runs with a weaker lock.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260615_0009"
down_revision: str | None = "20260608_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, column, constraint_name, referenced_table)
_SET_NULL = [
    ("request_facts", "subject_id", "request_facts_subject_id_fkey", "subjects"),
    ("request_facts", "project_id", "request_facts_project_id_fkey", "projects"),
    (
        "request_facts",
        "upstream_target_id",
        "request_facts_upstream_target_id_fkey",
        "upstream_targets",
    ),
    (
        "audit_events",
        "actor_subject_id",
        "audit_events_actor_subject_id_fkey",
        "subjects",
    ),
]

# (table, column, constraint_name, referenced_table)
_CASCADE = [
    ("projects", "owner_subject_id", "projects_owner_subject_id_fkey", "subjects"),
    (
        "project_memberships",
        "project_id",
        "project_memberships_project_id_fkey",
        "projects",
    ),
    (
        "project_memberships",
        "subject_id",
        "project_memberships_subject_id_fkey",
        "subjects",
    ),
    ("gateway_keys", "project_id", "gateway_keys_project_id_fkey", "projects"),
    ("gateway_keys", "subject_id", "gateway_keys_subject_id_fkey", "subjects"),
    (
        "upstream_targets",
        "model_alias_id",
        "upstream_targets_model_alias_id_fkey",
        "model_aliases",
    ),
    (
        "model_team_grants",
        "model_alias_id",
        "model_team_grants_model_alias_id_fkey",
        "model_aliases",
    ),
    ("model_team_grants", "team_id", "model_team_grants_team_id_fkey", "teams"),
    (
        "model_entitlements",
        "gateway_key_id",
        "model_entitlements_gateway_key_id_fkey",
        "gateway_keys",
    ),
    (
        "model_entitlements",
        "model_alias_id",
        "model_entitlements_model_alias_id_fkey",
        "model_aliases",
    ),
    (
        "model_entitlements",
        "project_id",
        "model_entitlements_project_id_fkey",
        "projects",
    ),
    (
        "model_entitlements",
        "subject_id",
        "model_entitlements_subject_id_fkey",
        "subjects",
    ),
    (
        "router_command_configs",
        "model_alias_id",
        "router_command_configs_model_alias_id_fkey",
        "model_aliases",
    ),
    ("team_memberships", "subject_id", "team_memberships_subject_id_fkey", "subjects"),
    ("team_memberships", "team_id", "team_memberships_team_id_fkey", "teams"),
    ("user_sessions", "subject_id", "user_sessions_subject_id_fkey", "subjects"),
]


def upgrade() -> None:
    # Big / append-only tables: SET NULL, added NOT VALID then validated to avoid
    # a long ACCESS EXCLUSIVE lock while existing rows are checked.
    for table, column, name, ref in _SET_NULL:
        op.drop_constraint(name, table)
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({column}) REFERENCES {ref}(id) "
            f"ON DELETE SET NULL NOT VALID"
        )
        op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")

    # Config / child tables: CASCADE. Small tables, inline validation is fine.
    for table, column, name, ref in _CASCADE:
        op.drop_constraint(name, table)
        op.create_foreign_key(name, table, ref, [column], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    # Restore NO ACTION (the implicit default) on every FK.
    for table, column, name, ref in _CASCADE:
        op.drop_constraint(name, table)
        op.create_foreign_key(name, table, ref, [column], ["id"])
    for table, column, name, ref in _SET_NULL:
        op.drop_constraint(name, table)
        op.create_foreign_key(name, table, ref, [column], ["id"])
