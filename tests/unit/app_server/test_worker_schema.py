"""procrastinate's schema, as the Alembic revisions apply it.

procrastinate ships its schema as SQL files, and the app applies copies of them
in Alembic revisions (see ``migrations/versions/175_add_procrastinate_schema.py``).
The test here fails when the installed procrastinate needs SQL that no
revision applies yet.
"""

from pathlib import Path

import procrastinate
from procrastinate import App, PsycopgConnector

from tests import postgres_testdb

VENDORED = Path(__file__).parents[3] / 'migrations' / 'procrastinate'

# The last procrastinate migration that the copied schema.sql already includes.
SCHEMA_INCLUDES_THROUGH = '03.04.00_50_post_add_retry_failed_job_procedure.sql'


def test_every_procrastinate_migration_is_applied():
    installed = Path(procrastinate.__file__).parent / 'sql' / 'migrations'
    vendored = {path.name for path in VENDORED.glob('*.sql')}

    missing = [
        path.name
        for path in sorted(installed.glob('*.sql'))
        if path.name > SCHEMA_INCLUDES_THROUGH and path.name not in vendored
    ]

    assert not missing, (
        f'procrastinate {procrastinate.__version__} ships migrations that no '
        f'Alembic revision applies: {missing}. Copy them to {VENDORED} and add '
        'a revision that runs them. A "pre" migration can run before the new '
        'code is deployed, and a "post" migration only after every old worker '
        'has stopped.'
    )


async def test_the_migrated_database_has_the_queue(
    test_database: postgres_testdb.TestDatabase,
):
    server = test_database.server
    connector = PsycopgConnector(
        kwargs={
            'host': server.host,
            'port': server.port,
            'user': server.user,
            'password': server.password,
            'dbname': test_database.name,
        },
        min_size=1,
        max_size=1,
    )

    async with App(connector=connector).open_async() as app:
        assert await app.check_connection_async()
