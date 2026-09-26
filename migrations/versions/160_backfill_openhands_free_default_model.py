"""Backfill OpenHands free/default verified model row.

Revision ID: 160
Revises: 159
Create Date: 2026-08-31 00:00:00.000000

"""

import os
import re
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '160'
down_revision: str | None = '159'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FREE_MODELS = ('deepseek-v4-flash',)
_DEFAULT_MODEL = 'deepseek-v4-flash'

# The deepseek free/default backfill is a managed-deployment concern; gate on
# WEB_HOST so a self-hosted install does not get a phantom managed default.
SAAS_WEB_HOSTS = frozenset(
    {'app.all-hands.dev', 'staging.all-hands.dev', 'dev.all-hands.dev'}
)
SAAS_PREVIEW_HOST = re.compile(r'[a-z0-9][a-z0-9-]*\.staging\.all-hands\.dev')


def upgrade() -> None:
    # Only the managed deployments run this backfill. Unlike
    # server.constants.WEB_HOST, an unset value must not read as SaaS.
    web_host = os.environ.get('WEB_HOST', '').strip()
    if web_host not in SAAS_WEB_HOSTS and not SAAS_PREVIEW_HOST.fullmatch(web_host):
        return

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
