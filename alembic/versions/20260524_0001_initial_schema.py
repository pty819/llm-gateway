"""Initial gateway schema.

Revision ID: 20260524_0001
Revises:
Create Date: 2026-05-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
import sqlmodel.sql.sqltypes
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260524_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    subject_type = postgresql.ENUM(
        "USER", "SERVICE", name="subjecttype", create_type=False
    )
    resource_state = postgresql.ENUM(
        "ACTIVE", "DISABLED", name="resourcestate", create_type=False
    )
    ip_policy_mode = postgresql.ENUM(
        "ALL_PASS", "ALLOWLIST", name="ippolicymode", create_type=False
    )
    router_policy = postgresql.ENUM(
        "CONSISTENT_HASH", "CACHE_AWARE", name="routerpolicy", create_type=False
    )
    endpoint_family = postgresql.ENUM(
        "OPENAI_CHAT", "ANTHROPIC_MESSAGES", name="endpointfamily", create_type=False
    )
    request_outcome = postgresql.ENUM(
        "SUCCESS",
        "AUTH_FAILURE",
        "POLICY_DENIAL",
        "RATE_LIMITED",
        "ADAPTER_FAILURE",
        "UPSTREAM_FAILURE",
        "CLIENT_CANCELLED",
        name="requestoutcome",
        create_type=False,
    )
    usage_source = postgresql.ENUM(
        "LITELLM", "MISSING", name="usagesource", create_type=False
    )
    for enum in [
        subject_type,
        resource_state,
        ip_policy_mode,
        router_policy,
        endpoint_family,
        request_outcome,
        usage_source,
    ]:
        enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "subjects",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("type", subject_type, nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subjects_name", "subjects", ["name"])
    op.create_index("ix_subjects_state", "subjects", ["state"])
    op.create_index("ix_subjects_type", "subjects", ["type"])

    op.create_table(
        "projects",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.Column("owner_subject_id", sa.Uuid(), nullable=True),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["owner_subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_projects_name", "projects", ["name"])
    op.create_index("ix_projects_owner_subject_id", "projects", ["owner_subject_id"])
    op.create_index("ix_projects_state", "projects", ["state"])

    op.create_table(
        "model_aliases",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("alias", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "upstream_model_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column("litellm_model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.Column("supports_streaming", sa.Boolean(), nullable=False),
        sa.Column("supports_tools", sa.Boolean(), nullable=False),
        sa.Column("supports_reasoning", sa.Boolean(), nullable=False),
        sa.Column("ip_policy_mode", ip_policy_mode, nullable=False),
        sa.Column(
            "ip_allowlist_cidrs", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias"),
    )
    op.create_index("ix_model_aliases_alias", "model_aliases", ["alias"])
    op.create_index("ix_model_aliases_state", "model_aliases", ["state"])

    op.create_table(
        "project_memberships",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("role", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_project_memberships_project_id", "project_memberships", ["project_id"]
    )
    op.create_index(
        "ix_project_memberships_subject_id", "project_memberships", ["subject_id"]
    )

    op.create_table(
        "gateway_keys",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("key_prefix", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("key_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gateway_keys_key_prefix", "gateway_keys", ["key_prefix"])
    op.create_index("ix_gateway_keys_project_id", "gateway_keys", ["project_id"])
    op.create_index("ix_gateway_keys_state", "gateway_keys", ["state"])
    op.create_index("ix_gateway_keys_subject_id", "gateway_keys", ["subject_id"])

    op.create_table(
        "upstream_targets",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_alias_id", sa.Uuid(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("base_url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("api_key_ref", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("api_key_value", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("health_path", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.Column(
            "extra_headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.ForeignKeyConstraint(["model_alias_id"], ["model_aliases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_upstream_targets_model_alias_id", "upstream_targets", ["model_alias_id"]
    )
    op.create_index("ix_upstream_targets_state", "upstream_targets", ["state"])

    op.create_table(
        "router_command_configs",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_alias_id", sa.Uuid(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "worker_urls", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("policy", router_policy, nullable=False),
        sa.Column("host", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("extra_args", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["model_alias_id"], ["model_aliases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_router_command_configs_model_alias_id",
        "router_command_configs",
        ["model_alias_id"],
    )

    op.create_table(
        "model_entitlements",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("gateway_key_id", sa.Uuid(), nullable=True),
        sa.Column("model_alias_id", sa.Uuid(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.ForeignKeyConstraint(["gateway_key_id"], ["gateway_keys.id"]),
        sa.ForeignKeyConstraint(["model_alias_id"], ["model_aliases.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_model_entitlements_gateway_key_id", "model_entitlements", ["gateway_key_id"]
    )
    op.create_index(
        "ix_model_entitlements_model_alias_id", "model_entitlements", ["model_alias_id"]
    )
    op.create_index(
        "ix_model_entitlements_project_id", "model_entitlements", ["project_id"]
    )
    op.create_index("ix_model_entitlements_state", "model_entitlements", ["state"])
    op.create_index(
        "ix_model_entitlements_subject_id", "model_entitlements", ["subject_id"]
    )

    op.create_table(
        "rate_policies",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("requests_per_minute", sa.Integer(), nullable=True),
        sa.Column("concurrency_limit", sa.Integer(), nullable=True),
        sa.Column("state", resource_state, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rate_policies_scope", "rate_policies", ["scope"])
    op.create_index("ix_rate_policies_scope_id", "rate_policies", ["scope_id"])
    op.create_index("ix_rate_policies_state", "rate_policies", ["state"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("actor_subject_id", sa.Uuid(), nullable=True),
        sa.Column("action", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("resource_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["actor_subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index(
        "ix_audit_events_actor_subject_id", "audit_events", ["actor_subject_id"]
    )
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_outcome", "audit_events", ["outcome"])
    op.create_index("ix_audit_events_resource_id", "audit_events", ["resource_id"])
    op.create_index("ix_audit_events_resource_type", "audit_events", ["resource_type"])

    op.create_table(
        "request_facts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=False),
        sa.Column("endpoint_family", endpoint_family, nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("subject_type", subject_type, nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("model_alias", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("upstream_target_id", sa.Uuid(), nullable=True),
        sa.Column("streaming", sa.Boolean(), nullable=False),
        sa.Column("outcome", request_outcome, nullable=False),
        sa.Column("usage_source", usage_source, nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("error_class", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("error_detail", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.ForeignKeyConstraint(["upstream_target_id"], ["upstream_targets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_request_facts_ended_at", "request_facts", ["ended_at"])
    op.create_index(
        "ix_request_facts_endpoint_family", "request_facts", ["endpoint_family"]
    )
    op.create_index("ix_request_facts_error_class", "request_facts", ["error_class"])
    op.create_index("ix_request_facts_model_alias", "request_facts", ["model_alias"])
    op.create_index("ix_request_facts_outcome", "request_facts", ["outcome"])
    op.create_index("ix_request_facts_project_id", "request_facts", ["project_id"])
    op.create_index("ix_request_facts_request_id", "request_facts", ["request_id"])
    op.create_index("ix_request_facts_started_at", "request_facts", ["started_at"])
    op.create_index("ix_request_facts_streaming", "request_facts", ["streaming"])
    op.create_index("ix_request_facts_subject_id", "request_facts", ["subject_id"])
    op.create_index("ix_request_facts_subject_type", "request_facts", ["subject_type"])
    op.create_index(
        "ix_request_facts_upstream_target_id", "request_facts", ["upstream_target_id"]
    )
    op.create_index("ix_request_facts_usage_source", "request_facts", ["usage_source"])


def downgrade() -> None:
    for table in [
        "request_facts",
        "audit_events",
        "rate_policies",
        "model_entitlements",
        "router_command_configs",
        "upstream_targets",
        "gateway_keys",
        "project_memberships",
        "model_aliases",
        "projects",
        "subjects",
    ]:
        op.drop_table(table)
    for enum_name in [
        "usagesource",
        "requestoutcome",
        "endpointfamily",
        "routerpolicy",
        "ippolicymode",
        "resourcestate",
        "subjecttype",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
