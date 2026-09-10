"""Add DeepSeek V4.1 Flash to verified OpenHands models."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '158'
down_revision: str | None = '157'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO verified_models (model_name, provider)
            VALUES ('deepseek-v4.1-flash', 'openhands')
            ON CONFLICT (model_name, provider) DO UPDATE
            SET is_enabled = true,
                updated_at = CURRENT_TIMESTAMP
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM verified_models
            WHERE model_name = 'deepseek-v4.1-flash'
              AND provider = 'openhands'
            """
        )
    )
