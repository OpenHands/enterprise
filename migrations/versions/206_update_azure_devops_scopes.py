"""Request named Azure DevOps scopes and ``offline_access``.

Migration 168 seeded the Azure DevOps git provider with only
``499b84ac-.../.default`` (``499b84ac-...`` is Azure DevOps' resource ID in
Entra). That has two problems:

- Entra's v2.0 endpoint only issues a refresh token when the request includes
  ``offline_access``, so a linked token stopped working when the access token
  expired (60-90 minutes).
- ``.default`` only returns permissions already added to the Entra app
  registration, so it fails with AADSTS650057 unless an admin adds the Azure
  DevOps API permission. Named scopes are consented at sign-in instead.

Rows still holding the seeded scopes get the full named list, which also adds
``openid email profile`` so login can read the user from the ID token. Rows an
admin changed only get ``offline_access`` appended. Rows with no scopes request
nothing from Entra and need an admin to configure them.

Users linked before this migration have no stored refresh token and must
reconnect once.

Revision ID: 206
Revises: 205
Create Date: 2026-10-29 00:00:00.000000
"""

import json
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '206'
down_revision: str | None = '205'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AZURE_DEVOPS_RESOURCE = '499b84ac-1321-427f-aa17-267ca6975798'
_SEEDED_SCOPES = [
    [f'{_AZURE_DEVOPS_RESOURCE}/.default'],
    [f'{_AZURE_DEVOPS_RESOURCE}/.default', 'offline_access'],
]
_NAMED_SCOPES = [
    'openid',
    'email',
    'profile',
    'offline_access',
    f'{_AZURE_DEVOPS_RESOURCE}/vso.profile',
    f'{_AZURE_DEVOPS_RESOURCE}/vso.code_write',
    f'{_AZURE_DEVOPS_RESOURCE}/vso.project',
    f'{_AZURE_DEVOPS_RESOURCE}/vso.work_write',
]


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE auth_providers
            SET details = jsonb_set(
                    details::jsonb, '{scopes}', CAST(:named AS jsonb)
                )::json,
                updated_at = CURRENT_TIMESTAMP
            WHERE kind = 'OAuthProviderDetails'
              AND details ->> 'provider_type' = 'azure_devops'
              AND (details::jsonb -> 'scopes') IN (
                  CAST(:seeded_0 AS jsonb), CAST(:seeded_1 AS jsonb)
              )
            """
        ),
        {
            'named': json.dumps(_NAMED_SCOPES),
            'seeded_0': json.dumps(_SEEDED_SCOPES[0]),
            'seeded_1': json.dumps(_SEEDED_SCOPES[1]),
        },
    )
    bind.execute(
        sa.text(
            """
            UPDATE auth_providers
            SET details = jsonb_set(
                    details::jsonb,
                    '{scopes}',
                    (details::jsonb -> 'scopes') || '["offline_access"]'::jsonb
                )::json,
                updated_at = CURRENT_TIMESTAMP
            WHERE kind = 'OAuthProviderDetails'
              AND details ->> 'provider_type' = 'azure_devops'
              AND jsonb_typeof(details::jsonb -> 'scopes') = 'array'
              AND NOT (details::jsonb -> 'scopes') ? 'offline_access'
            """
        )
    )


def downgrade() -> None:
    # The new scopes are indistinguishable from ones an admin set, and
    # restoring .default would stop refresh tokens from being issued again.
    pass
