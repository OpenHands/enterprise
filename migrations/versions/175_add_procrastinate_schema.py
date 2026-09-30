"""Add the procrastinate job queue.

The background worker (``openhands.app_server.worker``) runs its jobs through
procrastinate, which keeps its queue in Postgres. This applies procrastinate's
own schema, as shipped in procrastinate 3.9.0, from
``migrations/procrastinate/schema.sql``. Every table, type and function in it
is named ``procrastinate_*``.

The file is a copy so that this revision never changes. A procrastinate upgrade
that ships new migration files needs a new revision that applies them;
``tests/unit/app_server/test_worker_schema.py`` fails until one exists.

Revision ID: 175
Revises: 174
Create Date: 2026-09-30 00:00:00.000000
"""

from pathlib import Path
from typing import Sequence

from alembic import op

revision: str = '175'
down_revision: str | None = '174'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = Path(__file__).parents[1] / 'procrastinate' / 'schema.sql'

DROP_SCHEMA = """
DROP TABLE IF EXISTS
    procrastinate_events,
    procrastinate_periodic_defers,
    procrastinate_jobs,
    procrastinate_workers
CASCADE;

DO $$
DECLARE
    routine regprocedure;
BEGIN
    FOR routine IN
        SELECT p.oid::regprocedure
        FROM pg_proc p
        WHERE p.proname LIKE 'procrastinate\\_%'
        AND p.pronamespace = current_schema()::regnamespace
    LOOP
        EXECUTE format('DROP ROUTINE %s CASCADE', routine);
    END LOOP;
END
$$;

DROP TYPE IF EXISTS
    procrastinate_job_to_defer_v1,
    procrastinate_job_event_type,
    procrastinate_job_status
CASCADE;
"""


def _run_script(sql: str) -> None:
    # Sent to the driver as it is. SQLAlchemy would read ``:name`` and ``%`` in
    # the SQL as parameters, and a statement with no parameters goes over the
    # simple query protocol, which runs many statements at once.
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run_script(SCHEMA.read_text())


def downgrade() -> None:
    _run_script(DROP_SCHEMA)
