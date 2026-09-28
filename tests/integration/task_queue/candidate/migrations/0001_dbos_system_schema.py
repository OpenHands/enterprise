"""DBOS system schema, applied with `dbos migrate` as the owner role.

Revision ID: 0001
"""

import os
import subprocess

from alembic import op

revision = '0001'
down_revision = None


def upgrade() -> None:
    # dbos migrate uses its own connection and runs CREATE INDEX CONCURRENTLY,
    # which would wait on Alembic's open transaction, so step outside it.
    # -r grants poc_app usage, DML, sequences and functions on the schema.
    with op.get_context().autocommit_block():
        subprocess.run(
            ['dbos', 'migrate', '-s', os.environ['POC_MIGRATE_URL'], '-r', 'poc_app'],
            check=True,
        )


def downgrade() -> None:
    op.execute('DROP SCHEMA dbos CASCADE')
