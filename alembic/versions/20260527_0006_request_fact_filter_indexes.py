"""Add request fact filter-first analytics indexes.

Revision ID: 20260527_0006
Revises: 20260527_0005
Create Date: 2026-05-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260527_0006"
down_revision: str | None = "20260527_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_request_facts_started_request",
        "request_facts",
        ["started_at", "request_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_request_facts_model_started",
        "request_facts",
        ["model_alias", "started_at"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_request_facts_subject_started",
        "request_facts",
        ["subject_id", "started_at"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_request_facts_project_started",
        "request_facts",
        ["project_id", "started_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_request_facts_project_started",
        table_name="request_facts",
        if_exists=True,
    )
    op.drop_index(
        "ix_request_facts_subject_started",
        table_name="request_facts",
        if_exists=True,
    )
    op.drop_index(
        "ix_request_facts_model_started",
        table_name="request_facts",
        if_exists=True,
    )
    op.drop_index(
        "ix_request_facts_started_request",
        table_name="request_facts",
        if_exists=True,
    )
