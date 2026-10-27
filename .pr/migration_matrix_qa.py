"""Run migration CI's driver matrix against disposable PostgreSQL 16 databases."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text

from tests import postgres_testdb


def main():
    pg = postgres_testdb.start_server()
    results = []
    try:
        for driver in ('pg8000', ''):
            database = 'oh_test_' + uuid4().hex[:12]
            postgres_testdb._run_admin_sql(pg.server, f'CREATE DATABASE "{database}"')
            env = {
                k: v
                for k, v in os.environ.items()
                if not k.startswith(('DB_', 'GCP_', 'PG'))
            }
            env.update(
                DB_HOST=pg.server.host,
                DB_PORT=str(pg.server.port),
                DB_USER=pg.server.user,
                DB_PASS=pg.server.password,
                DB_NAME=database,
                DB_DRIVER=driver,
                WEB_HOST='',
            )
            env.pop('STRIPE_API_KEY', None)
            for args, expected in (
                (('upgrade', 'head'), '199'),
                (('downgrade', '-1'), '198'),
                (('upgrade', 'head'), '199'),
            ):
                result = subprocess.run(
                    [sys.executable, '-m', 'alembic', *args],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode:
                    raise RuntimeError(result.stderr)
                engine = create_engine(pg.server.url(database, driver=driver))
                try:
                    with engine.connect() as conn:
                        actual = conn.execute(
                            text('SELECT version_num FROM alembic_version')
                        ).scalar_one()
                    assert actual == expected, (driver, args, actual)
                finally:
                    engine.dispose()
                results.append(
                    {
                        'driver': driver or 'psycopg2',
                        'command': 'alembic ' + ' '.join(args),
                        'revision': actual,
                        'passed': True,
                    }
                )
            postgres_testdb.drop_test_database(pg.server, database)
    finally:
        pg.stop()
    report = Path(os.environ.get('QA_MIGRATION_REPORT', '.pr/migration-matrix-qa.json'))
    report.write_text(json.dumps({'postgres': '16', 'cases': results}, indent=2) + '\n')
    print(
        f'{len(results)} migration matrix checks passed; PostgreSQL removed; {report}'
    )


if __name__ == '__main__':
    main()
