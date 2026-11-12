"""Tell GitLab webhook and repository rows which auth provider they belong to.

GitLab project and group ids are only unique within one GitLab instance. With
several GitLab providers configured, rows from two instances can share an id, and
the unique constraints on ``gitlab_webhook.project_id`` and ``group_id`` would
drop the second one.

``provider_id`` is nullable: installs that never seeded a GitLab provider row,
and rows written before a provider is known, have none. Existing rows are
assigned to the lowest-id GitLab provider, the same one the legacy single-host
code paths resolve (``AuthProviderStore.get_provider_by_type``).

Revision ID: 215
Revises: 214
Create Date: 2026-11-12
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '215'
down_revision: str | None = '214'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ('gitlab_webhook', 'repos', 'user-repos')

_PRIMARY_GITLAB_PROVIDER = """
    (
        SELECT id FROM auth_providers
        WHERE kind = 'OAuthProviderDetails'
          AND details ->> 'provider_type' = 'gitlab'
        ORDER BY id
        LIMIT 1
    )
"""


def _index_name(table: str) -> str:
    return f'ix_{table.replace("-", "_")}_provider_id'


def upgrade() -> None:
    for table in TABLES:
        op.add_column(
            table,
            sa.Column(
                'provider_id',
                sa.Integer(),
                sa.ForeignKey('auth_providers.id', ondelete='CASCADE'),
                nullable=True,
            ),
        )
        op.create_index(_index_name(table), table, ['provider_id'])

    op.execute(
        f'UPDATE gitlab_webhook SET provider_id = {_PRIMARY_GITLAB_PROVIDER}'  # noqa: S608
    )
    for table in ('repos', 'user-repos'):
        op.execute(
            f'UPDATE "{table}" SET provider_id = {_PRIMARY_GITLAB_PROVIDER} '  # noqa: S608
            "WHERE repo_id LIKE 'gitlab##%'"
        )

    # COALESCE keeps rows without a provider unique among themselves, as the
    # old constraints did; NULLS NOT DISTINCT would need PostgreSQL 15.
    for column in ('group_id', 'project_id'):
        op.drop_constraint(f'uq_gitlab_webhook_{column}', 'gitlab_webhook', 'unique')
        op.execute(
            f'CREATE UNIQUE INDEX uq_gitlab_webhook_provider_{column} '
            f'ON gitlab_webhook (COALESCE(provider_id, 0), {column})'
        )


def downgrade() -> None:
    for column in ('group_id', 'project_id'):
        op.drop_index(f'uq_gitlab_webhook_provider_{column}', 'gitlab_webhook')
        # Two instances may have used the same id; keep the oldest row.
        op.execute(
            f'DELETE FROM gitlab_webhook a USING gitlab_webhook b '  # noqa: S608
            f'WHERE a.{column} = b.{column} AND a.id > b.id'
        )
        op.create_unique_constraint(
            f'uq_gitlab_webhook_{column}', 'gitlab_webhook', [column]
        )

    for table in TABLES:
        op.drop_index(_index_name(table), table)
        op.drop_column(table, 'provider_id')
