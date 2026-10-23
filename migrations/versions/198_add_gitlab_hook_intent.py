"""Add gitlab_hook_intent, an identity for every GitLab hook we create (PLTF-3689).

Before each create, the installer records an open intent with a fresh uuid and
puts that uuid in the hook's URL (``?oh_hook=<uuid>``). The row takes the new
credentials only in the transaction that commits the intent, and that write
names the uuid it replaces, so at most one hook ever carries the row's current
identity. Every existing row's uuid gets a committed intent, so the hooks
already installed keep authenticating.

Revision ID: 198
Revises: 197
Create Date: 2026-10-20 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = '198'
down_revision: str | None = '197'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'gitlab_hook_intent',
        sa.Column('uuid', sa.String(), primary_key=True),
        sa.Column(
            'webhook_id',
            sa.Integer(),
            sa.ForeignKey('gitlab_webhook.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('run_id', UUID(as_uuid=True)),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.CheckConstraint(
            "state IN ('open', 'committed', 'replaced', 'abandoned')",
            name='ck_gitlab_hook_intent_state',
        ),
    )
    op.create_index(
        'ix_gitlab_hook_intent_webhook_id', 'gitlab_hook_intent', ['webhook_id']
    )
    op.create_index(
        'uq_gitlab_hook_intent_committed',
        'gitlab_hook_intent',
        ['webhook_id'],
        unique=True,
        postgresql_where=sa.text("state = 'committed'"),
    )
    op.execute(
        """
        INSERT INTO gitlab_hook_intent (uuid, webhook_id, state)
        SELECT webhook_uuid, id, 'committed' FROM gitlab_webhook
        WHERE webhook_uuid IS NOT NULL
        ON CONFLICT (uuid) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table('gitlab_hook_intent')
