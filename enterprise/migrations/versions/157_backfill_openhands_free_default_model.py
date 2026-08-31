"""Backfill OpenHands free/default verified model row.

Revision ID: 157
Revises: 156
Create Date: 2026-08-31 00:00:00.000000

"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '157'
down_revision: str | None = '156'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FREE_MODELS = ('deepseek-v4-flash',)
_DEFAULT_MODEL = 'deepseek-v4-flash'


def upgrade() -> None:
    for model_name in _FREE_MODELS:
        op.execute(
            sa.text(
                """
                INSERT INTO verified_models (
                    model_name,
                    provider,
                    is_enabled,
                    is_verified,
                    is_free,
                    is_default
                )
                VALUES (:model_name, 'openhands', true, true, true, false)
                ON CONFLICT (model_name, provider) DO UPDATE
                SET is_enabled = true,
                    is_verified = true,
                    is_free = true,
                    updated_at = CURRENT_TIMESTAMP
                """
            ).bindparams(model_name=model_name)
        )

    op.execute(
        sa.text(
            """
            UPDATE verified_models
            SET is_default = true,
                is_enabled = true,
                is_verified = true,
                is_free = true,
                updated_at = CURRENT_TIMESTAMP
            WHERE model_name = :model_name
              AND provider = 'openhands'
              AND NOT EXISTS (
                  SELECT 1
                  FROM verified_models
                  WHERE provider = 'openhands'
                    AND is_default = true
              )
            """
        ).bindparams(model_name=_DEFAULT_MODEL)
    )


def downgrade() -> None:
    for model_name in _FREE_MODELS:
        op.execute(
            sa.text(
                """
                UPDATE verified_models
                SET is_free = false,
                    updated_at = CURRENT_TIMESTAMP
                WHERE model_name = :model_name
                  AND provider = 'openhands'
                """
            ).bindparams(model_name=model_name)
        )

    op.execute(
        sa.text(
            """
            UPDATE verified_models
            SET is_default = false,
                updated_at = CURRENT_TIMESTAMP
            WHERE model_name = :model_name
              AND provider = 'openhands'
              AND is_default = true
            """
        ).bindparams(model_name=_DEFAULT_MODEL)
    )
