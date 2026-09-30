"""Add the Procrastinate 3.10.0 task queue schema.

Revision ID: 174
Revises: 173
Create Date: 2026-09-30 00:00:00.000000

The DDL is the literal output of ``procrastinate schema --read`` at 3.10.0,
vendored in ``migrations/procrastinate/``. It is never generated here from the
installed library: a historical revision has to apply the same schema on every
future install, whatever Procrastinate version is installed by then. Upgrading
Procrastinate vendors the release's ``*_01_pre_*.sql`` / ``*_50_post_*.sql``
files as new revisions; ``post`` files wait until the rollback window closes.

The script contains ``:`` and ``%``, so it runs on the raw driver cursor with no
parameters rather than through SQLAlchemy's parameter parsing.

The task queue role's grants live in ``migrations/env.py`` and run on every
migration, so a role created after this revision was applied still gets them.
"""

import re
from pathlib import Path
from typing import Sequence

from alembic import op

revision: str = '174'
down_revision: str | None = '173'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_SQL = (
    Path(__file__).resolve().parents[1] / 'procrastinate' / '03.10.00_schema.sql'
)
TABLES = (
    'procrastinate_workers',
    'procrastinate_jobs',
    'procrastinate_periodic_defers',
    'procrastinate_events',
)


def _run_script(sql: str) -> None:
    cursor = op.get_bind().connection.cursor()
    try:
        cursor.execute(sql)
    finally:
        cursor.close()


def upgrade() -> None:
    _run_script(SCHEMA_SQL.read_text())


def downgrade() -> None:
    script = SCHEMA_SQL.read_text()
    functions = re.findall(r'^CREATE FUNCTION (\w+)', script, re.M)
    types = re.findall(r'^CREATE TYPE (\w+)', script, re.M)
    # Dropping procrastinate_jobs also drops the functions returning its row
    # type, hence IF EXISTS on the functions only.
    _run_script(
        f'DROP TABLE {", ".join(reversed(TABLES))} CASCADE;\n'
        + ''.join(f'DROP FUNCTION IF EXISTS {name};\n' for name in functions)
        + ''.join(f'DROP TYPE {name};\n' for name in types)
    )
