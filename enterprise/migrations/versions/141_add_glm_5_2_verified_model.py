"""Add GLM 5.2 to verified OpenHands models."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '141'
down_revision: Union[str, None] = '140'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO verified_models (model_name, provider)
            VALUES ('glm-5.2', 'openhands')
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
            WHERE model_name = 'glm-5.2'
              AND provider = 'openhands'
            """
        )
    )
