"""Rename daily conversation quota tables/columns to sandbox.

The metered unit for the daily quota changes from conversations to sandbox
creations: a sandbox is what allocates infrastructure. Tables and columns are
renamed in place so all history is preserved:

- ``daily_conversation_usage`` -> ``daily_sandbox_usage``
- ``daily_sandbox_usage.conversation_count`` -> ``sandbox_count``
- ``user.daily_conversation_limit`` -> ``daily_sandbox_limit``
- ``org.daily_conversation_limit`` -> ``daily_sandbox_limit``

Existing overrides (including -1 exemptions) and historical usage rows carry
over untouched, so nobody gains a fresh allowance mid-day at rollout.

Note: renaming the user/org limit columns means code from before this
migration (which maps ``daily_conversation_limit``) cannot run against a
database migrated past it, and migration tests for older revisions whose
fixtures insert these ORM models need the columns under their new names.
"""

from typing import Sequence

from alembic import op

revision: str = '189'
down_revision: str | None = '188'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    op.rename_table('daily_conversation_usage', 'daily_sandbox_usage')
    op.alter_column(
        'daily_sandbox_usage',
        'conversation_count',
        new_column_name='sandbox_count',
    )
    op.alter_column(
        'user',
        'daily_conversation_limit',
        new_column_name='daily_sandbox_limit',
    )
    op.alter_column(
        'org',
        'daily_conversation_limit',
        new_column_name='daily_sandbox_limit',
    )


def downgrade() -> None:
    op.alter_column(
        'org',
        'daily_sandbox_limit',
        new_column_name='daily_conversation_limit',
    )
    op.alter_column(
        'user',
        'daily_sandbox_limit',
        new_column_name='daily_conversation_limit',
    )
    op.alter_column(
        'daily_sandbox_usage',
        'sandbox_count',
        new_column_name='conversation_count',
    )
    op.rename_table('daily_sandbox_usage', 'daily_conversation_usage')
