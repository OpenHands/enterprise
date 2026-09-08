"""Add Kimi K3 to verified OpenHands models."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '144'
down_revision: Union[str, None] = '143'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO verified_models (model_name, provider)
            VALUES ('kimi-k3', 'openhands')
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
            WHERE model_name = 'kimi-k3'
              AND provider = 'openhands'
            """
        )
    )
