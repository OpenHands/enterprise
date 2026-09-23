"""Add is_free and is_default to verified_models.

Revision ID: 158
Revises: 157
Create Date: 2026-08-13 00:00:00.000000

"""

import os
import re
from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '158'
down_revision: str | None = '157'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# One default per provider is enforced by a partial unique index rather than a
# per-row flag alone, so the invariant cannot be violated by concurrent writes.
_DEFAULT_INDEX = 'uq_verified_model_default_per_provider'

# The model the frontend previously hard-coded as free. Seeding it keeps the
# "Free" badge identical the moment the client switches to the DB-driven flag.
_FREE_MODELS = ('deepseek-v4-flash',)

# Keep the database-seeded default aligned with the current code default.
_DEFAULT_MODEL = 'deepseek-v4-flash'

# Hosts of the All-Hands-managed deployments. The ``deepseek-v4-flash`` default
# is a managed-default pointer: the runtime materializer
# (``materialize_default_llm_profile``) only points the logical ``Default``
# profile at it where the managed LiteLLM proxy actually serves the model. A
# self-hosted install runs the same image and chart against its own proxy, so
# seeding the managed default there synthesizes a ``Default deepseek-v4-flash``
# profile for orgs that never configured one — overwriting a concrete BYOK
# ``Default`` on activate and creating a phantom default for everyone else.
# Gate the seed on ``WEB_HOST`` (the one value that differs per deployment),
# mirroring the kimi/glm/minimax model-rewrite migrations (143/145/153).
SAAS_WEB_HOSTS = frozenset(
    {
        'app.all-hands.dev',
        'staging.all-hands.dev',
        'dev.all-hands.dev',
    }
)

# Preview deployments render WEB_HOST as ``{branchSanitized}.{ingress.host}``,
# so they are always a single extra label under staging. ``branchSanitized`` is
# a generic host prefix (the feature ApplicationSet passes ``pr-<number>``, but
# the name and the chart both allow any sanitized branch), so match the whole
# label.
SAAS_PREVIEW_HOST = re.compile(r'[a-z0-9][a-z0-9-]*\.staging\.all-hands\.dev')


def _is_saas_web_host(host: str) -> bool:
    return host in SAAS_WEB_HOSTS or bool(SAAS_PREVIEW_HOST.fullmatch(host))


def _is_saas() -> bool:
    # Unlike server.constants.WEB_HOST this does not default to a managed host:
    # an unset value must not be read as "this is SaaS".
    return _is_saas_web_host(os.environ.get('WEB_HOST', '').strip())


def upgrade() -> None:
    op.add_column(
        'verified_models',
        sa.Column(
            'is_free',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )
    op.add_column(
        'verified_models',
        sa.Column(
            'is_default',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )
    op.create_index(
        _DEFAULT_INDEX,
        'verified_models',
        ['provider'],
        unique=True,
        postgresql_where=sa.text('is_default'),
    )

    # Seeding the managed ``deepseek-v4-flash`` default/free rows is a
    # managed-deployment concern (see ``_is_saas``). The columns and the
    # default-enforcing index above still ship to every deployment so the
    # invariant holds; only the seed data is gated.
    if not _is_saas():
        return

    for model_name in _FREE_MODELS:
        op.execute(
            sa.text(
                """
                INSERT INTO verified_models (
                    model_name, provider, is_enabled, is_free, is_default
                )
                VALUES (:model_name, 'openhands', true, true, false)
                ON CONFLICT (model_name, provider) DO UPDATE
                SET is_enabled = true,
                    is_free = true,
                    updated_at = CURRENT_TIMESTAMP
                """
            ).bindparams(model_name=model_name)
        )

    op.execute(
        sa.text(
            """
            INSERT INTO verified_models (
                model_name, provider, is_enabled, is_free, is_default
            )
            VALUES (:model_name, 'openhands', true, true, true)
            ON CONFLICT (model_name, provider) DO UPDATE
            SET is_enabled = true,
                is_free = true,
                is_default = true,
                updated_at = CURRENT_TIMESTAMP
            """
        ).bindparams(model_name=_DEFAULT_MODEL)
    )


def downgrade() -> None:
    op.drop_index(_DEFAULT_INDEX, table_name='verified_models')
    op.drop_column('verified_models', 'is_default')
    op.drop_column('verified_models', 'is_free')
