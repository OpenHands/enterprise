"""Record which connected git provider a conversation was started with.

With several providers of one type connected (e.g. two GitLab hosts), the
provider type alone does not say which token and host a conversation used.
``git_provider_id`` names the ``auth_providers`` row. It is nullable: existing
conversations, conversations started without a provider id, and conversations
started with OAuth v2 login off have none. Deleting the provider clears it.

Revision ID: 216
Revises: 215
Create Date: 2026-11-14
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '216'
down_revision: str | None = '215'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ('conversation_metadata', 'app_conversation_start_task')


def upgrade() -> None:
    for table in TABLES:
        op.add_column(
            table,
            sa.Column(
                'git_provider_id',
                sa.Integer(),
                sa.ForeignKey('auth_providers.id', ondelete='SET NULL'),
                nullable=True,
            ),
        )


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, 'git_provider_id')
