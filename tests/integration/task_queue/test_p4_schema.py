"""P4: candidate schema comes only from Alembic; replicas cannot run DDL."""

import json

from conftest import MANIFEST


def test_p4_schema_through_alembic(stack, record):
    s = stack()
    migrate = [
        json.loads(line)
        for line in s.compose('ps', '-a', '--format', 'json', 'migrate').splitlines()
    ]
    # Replicas connect as poc_app, which has no CREATE privilege, so a run
    # proves they booted without creating schema at runtime.
    s.wait_for('SELECT 1 FROM poc_job_runs WHERE finished_at IS NOT NULL', timeout=120)
    tables = {
        f'{schema}.{name}'
        for schema, name in s.sql(
            'SELECT table_schema, table_name FROM information_schema.tables '
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
        )
    }
    missing = sorted(set(MANIFEST['expected_tables']) - tables)
    record(
        'P4',
        {
            'migrate_exit_code': [m['ExitCode'] for m in migrate],
            'alembic_version': s.sql('SELECT version_num FROM alembic_version'),
            'candidate_tables': sorted(
                t for t in tables if not t.startswith('public.poc_')
            ),
            'missing_expected_tables': missing,
        },
    )
    assert [m['ExitCode'] for m in migrate] == [0]
    assert not missing
