"""Restore the legacy Bitbucket Cloud and Data Center OAuth scopes.

Before OAuth v2, Keycloak's Bitbucket identity providers requested these
scopes. Migration 168 seeded narrower or different ones, so a customer who
moved to OAuth v2 got a Bitbucket integration with fewer permissions than
before:

- Bitbucket Cloud: ``repository account`` -> the legacy list, which adds the
  write scopes, ``pullrequest:write``, ``issue:write`` and ``pipeline``.
- Bitbucket Data Center: ``PROJECT_READ REPO_READ REPO_WRITE`` ->
  ``REPO_ADMIN``, which implies read and write access and is what webhook
  install/reinstall needs. Data Center rejects scopes beyond what the
  application link grants.

Only rows still holding exactly the seeded scopes are changed, so scopes an
admin set by hand are kept.

Revision ID: 225
Revises: 224
Create Date: 2026-11-27 00:00:00.000000
"""

import json
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '225'
down_revision: str | None = '224'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCOPE_CHANGES: dict[str, tuple[list[str], list[str]]] = {
    'bitbucket': (
        ['repository', 'account'],
        [
            'account',
            'repository:write',
            'project:write',
            'pullrequest:write',
            'issue:write',
            'snippet',
            'email',
            'pipeline',
        ],
    ),
    'bitbucket_data_center': (
        ['PROJECT_READ', 'REPO_READ', 'REPO_WRITE'],
        ['REPO_ADMIN'],
    ),
}


def _replace_scopes(provider_type: str, old: list[str], new: list[str]) -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE auth_providers
            SET details = jsonb_set(
                    details::jsonb, '{scopes}', CAST(:new AS jsonb)
                )::json,
                updated_at = CURRENT_TIMESTAMP
            WHERE kind = 'OAuthProviderDetails'
              AND details ->> 'provider_type' = :provider_type
              AND (details::jsonb -> 'scopes') = CAST(:old AS jsonb)
            """
        ),
        {
            'provider_type': provider_type,
            'old': json.dumps(old),
            'new': json.dumps(new),
        },
    )


def upgrade() -> None:
    for provider_type, (seeded, legacy) in _SCOPE_CHANGES.items():
        _replace_scopes(provider_type, seeded, legacy)


def downgrade() -> None:
    for provider_type, (seeded, legacy) in _SCOPE_CHANGES.items():
        _replace_scopes(provider_type, legacy, seeded)
