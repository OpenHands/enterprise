"""Revision 174 installs exactly the Procrastinate 3.10.0 schema.

The revision runs vendored SQL rather than asking the installed library for
its schema, so these tests pin the two together: the vendored file is what
3.10.0 ships, and a migrated database is indistinguishable from one Procrastinate
set up itself.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import procrastinate
import psycopg
import pytest
from procrastinate.schema import SchemaManager
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from tests import postgres_testdb

VENDORED = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'procrastinate'
    / '03.10.00_schema.sql'
)

CATALOG_QUERIES = {
    'columns': """
        SELECT table_name, column_name, data_type, column_default, is_nullable,
               ordinal_position
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name LIKE 'procrastinate%'
    """,
    'indexes': """
        SELECT indexname, indexdef FROM pg_indexes
         WHERE schemaname = 'public' AND tablename LIKE 'procrastinate%'
    """,
    'constraints': """
        SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
          FROM pg_constraint WHERE conrelid::regclass::text LIKE 'procrastinate%'
    """,
    'functions': """
        SELECT proname, pg_get_functiondef(oid) FROM pg_proc
         WHERE proname LIKE 'procrastinate%'
    """,
    'triggers': """
        SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger
         WHERE tgname LIKE 'procrastinate%'
    """,
    'types': """
        SELECT t.typname, e.enumlabel, e.enumsortorder
          FROM pg_type t LEFT JOIN pg_enum e ON e.enumtypid = t.oid
         WHERE t.typname LIKE 'procrastinate%'
    """,
}


def _catalog(engine: Engine) -> dict[str, list[tuple]]:
    with engine.connect() as conn:
        return {
            name: sorted(tuple(row) for row in conn.execute(text(query)))
            for name, query in CATALOG_QUERIES.items()
        }


def _conninfo(server: postgres_testdb.PostgresServer, database: str, **kw) -> str:
    params = dict(
        host=server.host,
        port=server.port,
        dbname=database,
        user=server.user,
        password=server.password,
    )
    params.update(kw)
    return psycopg.conninfo.make_conninfo(**params)


@pytest.fixture
def fresh_database(
    postgres_server: postgres_testdb.PostgresServer,
) -> Iterator[str]:
    name = f'{postgres_testdb.TEST_DB_PREFIX}{uuid.uuid4().hex[:12]}'
    postgres_testdb._run_admin_sql(
        postgres_server, f'CREATE DATABASE "{name}" TEMPLATE template0'
    )
    try:
        yield name
    finally:
        postgres_testdb.drop_test_database(postgres_server, name)


def test_vendored_schema_is_the_pinned_release():
    """Bumping Procrastinate must come with new vendored migration revisions."""
    assert procrastinate.__version__ == '3.10.0', (
        'Procrastinate was upgraded: vendor the new release migrations '
        '(procrastinate/sql/migrations/*_01_pre_*.sql and *_50_post_*.sql) as '
        'new Alembic revisions, then update this pin. Never edit revision 174.'
    )
    assert VENDORED.read_text() == SchemaManager.get_schema()


def test_migrated_schema_matches_a_fresh_procrastinate_install(
    engine: Engine,
    postgres_server: postgres_testdb.PostgresServer,
    fresh_database: str,
):
    with psycopg.connect(
        _conninfo(postgres_server, fresh_database), autocommit=True
    ) as conn:
        conn.execute(SchemaManager.get_schema())
    reference = create_engine(
        postgres_server.sync_url(fresh_database), poolclass=NullPool
    )
    try:
        expected = _catalog(reference)
    finally:
        reference.dispose()

    migrated = _catalog(engine)

    assert all(expected.values())
    assert migrated == expected


def test_downgrade_removes_every_procrastinate_object(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '173'
    )
    assert not any(_catalog(engine).values())

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )
    assert all(_catalog(engine).values())


class TestWorkerRole:
    @pytest.fixture
    def worker_role(
        self, test_database: postgres_testdb.TestDatabase
    ) -> Iterator[tuple[str, str]]:
        # Not a plain identifier, so the grants only work if the role is quoted.
        role = f'TQ-worker {uuid.uuid4().hex[:12]}'
        quoted = f'"{role}"'
        password = uuid.uuid4().hex
        server = test_database.server
        postgres_testdb._run_admin_sql(
            server, f"CREATE ROLE {quoted} LOGIN PASSWORD '{password}'"
        )
        try:
            yield role, password
        finally:
            # Grants live in the test database, so DROP OWNED must run there.
            engine = postgres_testdb._admin_engine(server, test_database.name)
            try:
                with engine.connect() as conn:
                    conn.execute(text(f'DROP OWNED BY {quoted} CASCADE'))
            finally:
                engine.dispose()
            postgres_testdb._run_admin_sql(server, f'DROP ROLE {quoted}')

    @pytest.fixture
    def worker_conninfo(
        self, test_database: postgres_testdb.TestDatabase, worker_role
    ) -> str:
        # The test database is already at head, so this run applies no revision:
        # the role is granted even though it did not exist when 174 ran.
        role, password = worker_role
        postgres_testdb.run_alembic(
            test_database.server,
            test_database.name,
            'upgrade',
            'head',
            extra_env={'TASK_QUEUE_DB_USER': role},
        )
        return _conninfo(
            test_database.server, test_database.name, user=role, password=password
        )

    async def test_worker_role_runs_the_queue(self, worker_conninfo: str):
        app = procrastinate.App(
            connector=procrastinate.PsycopgConnector(conninfo=worker_conninfo)
        )

        @app.task(name='noop', queue='business')
        async def noop() -> None:
            return None

        async with app.open_async():
            job_id = await noop.defer_async()
            await app.run_worker_async(queues=['business'], wait=False)
            status = await app.job_manager.get_job_status_async(job_id)

        assert status == procrastinate.jobs.Status.SUCCEEDED

    def test_worker_role_cannot_change_the_schema(self, worker_conninfo: str):
        statements = [
            'CREATE TABLE tq_probe (id int)',
            'ALTER TABLE procrastinate_jobs ADD COLUMN probe int',
            'DROP TABLE procrastinate_events',
            'CREATE OR REPLACE FUNCTION procrastinate_register_worker_v1() '
            'RETURNS TABLE(worker_id bigint) LANGUAGE sql AS $$ SELECT 1::bigint $$',
        ]
        for ddl in statements:
            with psycopg.connect(worker_conninfo) as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(ddl)

    def test_grants_procrastinate_objects_added_after_174(
        self, engine: Engine, test_database: postgres_testdb.TestDatabase, worker_role
    ):
        role, _ = worker_role
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE procrastinate_probe (id bigserial)'))

        postgres_testdb.run_alembic(
            test_database.server,
            test_database.name,
            'upgrade',
            'head',
            extra_env={'TASK_QUEUE_DB_USER': role},
        )

        with engine.connect() as conn:
            # A comma-separated privilege list is true if any one is held.
            granted = (
                conn.execute(
                    text(
                        "SELECT bool_and(has_table_privilege(:role, 'procrastinate_probe', p))"
                        " FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) p"
                        ' UNION ALL SELECT has_sequence_privilege('
                        ":role, 'procrastinate_probe_id_seq', 'USAGE')"
                    ),
                    {'role': role},
                )
                .scalars()
                .all()
            )
        assert granted == [True, True]

    def test_migration_fails_when_the_role_does_not_exist(
        self, test_database: postgres_testdb.TestDatabase
    ):
        with pytest.raises(postgres_testdb.PostgresUnavailableError):
            postgres_testdb.run_alembic(
                test_database.server,
                test_database.name,
                'upgrade',
                'head',
                extra_env={'TASK_QUEUE_DB_USER': f'missing_{uuid.uuid4().hex[:12]}'},
            )
