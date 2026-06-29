"""Drop unused request_facts indexes.

Revision ID: 20260629_0010
Revises: 20260615_0009
Create Date: 2026-06-29

These 16 indexes are either covered by composite indexes
(model_started/subject_started/project_started cover the single-column
model_alias/subject_id/project_id prefixes) or never participate in any
query filter/group under the current Postgres-direct analytics path. Dropping
them reduces per-insert write cost. downgrade recreates them all.
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260629_0010"
down_revision: str | None = "20260615_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (index_name, columns, extra_kwargs) —— extra_kwargs 用于 BRIN 索引重建
_DROPPED_INDEXES = [
    ("ix_request_facts_started_model", ["started_at", "model_alias"], {}),
    ("ix_request_facts_started_subject", ["started_at", "subject_id"], {}),
    ("ix_request_facts_started_project", ["started_at", "project_id"], {}),
    ("ix_request_facts_started_request", ["started_at", "request_id"], {}),
    ("ix_request_facts_started_at_brin", ["started_at"], {"postgresql_using": "brin"}),
    ("ix_request_facts_subject_id", ["subject_id"], {}),
    ("ix_request_facts_project_id", ["project_id"], {}),
    ("ix_request_facts_model_alias", ["model_alias"], {}),
    ("ix_request_facts_ended_at", ["ended_at"], {}),
    ("ix_request_facts_usage_source", ["usage_source"], {}),
    ("ix_request_facts_outcome", ["outcome"], {}),
    ("ix_request_facts_endpoint_family", ["endpoint_family"], {}),
    ("ix_request_facts_streaming", ["streaming"], {}),
    ("ix_request_facts_error_class", ["error_class"], {}),
    ("ix_request_facts_subject_type", ["subject_type"], {}),
    ("ix_request_facts_upstream_target_id", ["upstream_target_id"], {}),
]


def upgrade() -> None:
    for index_name, _columns, _kwargs in _DROPPED_INDEXES:
        op.drop_index(index_name, table_name="request_facts", if_exists=True)


def downgrade() -> None:
    for index_name, columns, kwargs in reversed(_DROPPED_INDEXES):
        op.create_index(
            index_name, "request_facts", columns, if_not_exists=True, **kwargs
        )
