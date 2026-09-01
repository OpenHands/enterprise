"""Add DeepSeek V4 Flash to verified OpenHands models."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '156'
down_revision: Union[str, None] = '155'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO verified_models (model_name, provider)
            VALUES ('deepseek-v4-flash', 'openhands')
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
            WHERE model_name = 'deepseek-v4-flash'
              AND provider = 'openhands'
            """
        )
    )
