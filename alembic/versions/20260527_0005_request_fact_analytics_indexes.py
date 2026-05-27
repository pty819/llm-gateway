"""Add request fact analytics indexes.

Revision ID: 20260527_0005
Revises: 20260526_0004
Create Date: 2026-05-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260527_0005"
down_revision: str | None = "20260526_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_request_facts_started_at_brin",
        "request_facts",
        ["started_at"],
        postgresql_using="brin",
        if_not_exists=True,
    )
    op.create_index(
        "ix_request_facts_started_model",
        "request_facts",
        ["started_at", "model_alias"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_request_facts_started_subject",
        "request_facts",
        ["started_at", "subject_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_request_facts_started_project",
        "request_facts",
        ["started_at", "project_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_request_facts_started_project",
        table_name="request_facts",
        if_exists=True,
    )
    op.drop_index(
        "ix_request_facts_started_subject",
        table_name="request_facts",
        if_exists=True,
    )
    op.drop_index(
        "ix_request_facts_started_model",
        table_name="request_facts",
        if_exists=True,
    )
    op.drop_index(
        "ix_request_facts_started_at_brin",
        table_name="request_facts",
        if_exists=True,
    )
