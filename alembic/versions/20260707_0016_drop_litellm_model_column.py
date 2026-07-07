"""Drop the redundant litellm_model column from model_aliases.

Revision ID: 20260707_0016
Revises: 20260707_0015
Create Date: 2026-07-07

After LiteLLM was removed, ``litellm_model`` was a redundant copy of
``upstream_model_name`` — but its values still carried LiteLLM provider
prefixes (e.g. ``openai/Qwen3.6_Dense``) that vLLM does not recognize,
causing 404s. The gateway now forwards ``upstream_model_name`` (the bare
model name) to the upstream, so this column is dead weight. Drop it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260707_0016"
down_revision: str | None = "20260707_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("model_aliases", "litellm_model")


def downgrade() -> None:
    # NOT NULL column: server_default avoids NotNullViolation when repopulating.
    # Production never downgrades past this point.
    op.add_column(
        "model_aliases",
        sa.Column("litellm_model", sa.String(), nullable=False, server_default=""),
    )
