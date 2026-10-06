"""Revision 177 adds the scheduled-job schema, inactive until the R1 bootstrap."""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests import postgres_testdb

NEW_TABLES = {
    'scheduled_job_permit',
    'scheduled_job_pod',
    'scheduled_job_process',
    'scheduled_job_observation',
    'scheduled_job_occurrence',
    'scheduled_job_run',
    'scheduled_job_target_visit',
    'gitlab_hook_intent',
    'gitlab_delivery',
    'gitlab_dedupe_replica',
    'scheduled_job_bootstrap',
    'gitlab_handover_attempt',
    'scheduled_job_pre_r0_pod',
}
NEW_COLUMNS = {
    ('maintenance_tasks', 'claim_run_id'),
    ('maintenance_tasks', 'claimed_at'),
    ('gitlab_webhook', 'claim_run_id'),
    ('gitlab_webhook', 'claimed_at'),
    ('gitlab_webhook', 'webhook_secret_v2'),
    ('resend_synced_users', 'claim_run_id'),
    ('resend_synced_users', 'claimed_at'),
    ('resend_synced_users', 'welcome_email_status'),
    ('resend_synced_users', 'welcome_email_payload'),
    ('resend_synced_users', 'welcome_email_first_attempt_at'),
    ('resend_synced_users', 'welcome_email_next_attempt_at'),
    ('resend_synced_users', 'welcome_email_id'),
}
NEW_FUNCTIONS = {
    'scheduled_job_pod_permanent',
    'scheduled_job_pre_r0_pod_resolution_final',
    'gitlab_webhook_secret_moved',
}


def _run(engine: Engine, sql: str, **params):
    with engine.begin() as conn:
        return conn.execute(text(sql), params)


def _schema_objects(engine: Engine) -> dict[str, set]:
    with engine.connect() as conn:
        tables = set(
            conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )
        columns = {
            tuple(row)
            for row in conn.execute(
                text(
                    'SELECT table_name, column_name FROM information_schema.columns '
                    "WHERE table_schema = 'public'"
                )
            )
        }
        functions = set(
            conn.execute(
                text(
                    'SELECT proname FROM pg_proc p JOIN pg_namespace n '
                    "ON n.oid = p.pronamespace WHERE n.nspname = 'public'"
                )
            ).scalars()
        )
    return {
        'tables': tables & NEW_TABLES,
        'columns': columns & NEW_COLUMNS,
        'functions': functions & NEW_FUNCTIONS,
    }


def _insert_webhook(engine: Engine, secret: str | None) -> int:
    return _run(
        engine,
        'INSERT INTO gitlab_webhook (project_id, user_id, webhook_exists, '
        'webhook_secret) VALUES (:project, :user, false, :secret) RETURNING id',
        project=uuid.uuid4().hex,
        user='user-1',
        secret=secret,
    ).scalar_one()


def _start_handover(engine: Engine) -> None:
    _run(engine, 'INSERT INTO scheduled_job_bootstrap (id) VALUES (1)')
    _run(engine, 'UPDATE scheduled_job_bootstrap SET gitlab_handover_at = now()')


@pytest.fixture
def fresh_database(postgres_server: postgres_testdb.PostgresServer) -> Iterator[str]:
    name = f'{postgres_testdb.TEST_DB_PREFIX}{uuid.uuid4().hex[:12]}'
    postgres_testdb._run_admin_sql(
        postgres_server, f'CREATE DATABASE "{name}" TEMPLATE template0'
    )
    try:
        yield name
    finally:
        postgres_testdb.drop_test_database(postgres_server, name)


def test_upgrade_seeds_closed_permits_and_a_pending_bootstrap(
    postgres_server: postgres_testdb.PostgresServer, fresh_database: str
):
    postgres_testdb.run_alembic(postgres_server, fresh_database, 'upgrade', 'head')
    engine = create_engine(postgres_server.sync_url(fresh_database), poolclass=NullPool)
    try:
        permits = _run(
            engine, 'SELECT mode, epoch, open FROM scheduled_job_permit ORDER BY mode'
        ).all()
        bootstrap = _run(
            engine,
            'SELECT id, state, legacy_exposure, gitlab_handover_at, '
            'gitlab_handover_gen FROM scheduled_job_bootstrap',
        ).all()
    finally:
        engine.dispose()

    assert [tuple(row) for row in permits] == [
        ('cronjob', 0, False),
        ('worker', 0, False),
    ]
    assert [tuple(row) for row in bootstrap] == [(1, 'pending', 'unknown', None, 0)]


def test_bootstrap_holds_one_row(engine: Engine):
    _run(engine, 'INSERT INTO scheduled_job_bootstrap (id) VALUES (1)')
    with pytest.raises(DBAPIError, match='ck_scheduled_job_bootstrap_single_row'):
        _run(engine, 'INSERT INTO scheduled_job_bootstrap (id) VALUES (2)')


def test_permit_mode_is_cronjob_or_worker(engine: Engine):
    with pytest.raises(DBAPIError, match='ck_scheduled_job_permit_mode'):
        _run(engine, "INSERT INTO scheduled_job_permit (mode) VALUES ('legacy')")


def test_pod_retirement_is_permanent(engine: Engine):
    _run(engine, "INSERT INTO scheduled_job_pod (pod_uid) VALUES ('pod-1')")
    _run(
        engine,
        "INSERT INTO scheduled_job_pod (pod_uid, retired_at) VALUES ('pod-1', now()) "
        'ON CONFLICT (pod_uid) DO UPDATE SET retired_at = '
        'COALESCE(scheduled_job_pod.retired_at, EXCLUDED.retired_at)',
    )

    with pytest.raises(DBAPIError, match='retired_at is never cleared'):
        _run(engine, 'UPDATE scheduled_job_pod SET retired_at = NULL')
    with pytest.raises(DBAPIError, match='scheduled_job_pod rows are never deleted'):
        _run(engine, 'DELETE FROM scheduled_job_pod')

    registered = _run(
        engine,
        "INSERT INTO scheduled_job_pod (pod_uid) VALUES ('pod-1') "
        'ON CONFLICT (pod_uid) DO NOTHING RETURNING pod_uid',
    ).all()
    retired = _run(
        engine, 'SELECT retired_at IS NOT NULL FROM scheduled_job_pod'
    ).scalar_one()
    assert registered == []
    assert retired is True


def test_pre_r0_resolution_is_final(engine: Engine):
    _run(
        engine,
        'INSERT INTO scheduled_job_pre_r0_pod (pod_uid, pod_name, job) '
        "VALUES ('pod-1', 'resend-sync-1', 'resend-sync')",
    )
    _run(
        engine,
        "UPDATE scheduled_job_pre_r0_pod SET resolution = 'e4', "
        "resolved_by = 'oncall', resolved_at = now()",
    )

    with pytest.raises(DBAPIError, match='resolution is final once set'):
        _run(engine, "UPDATE scheduled_job_pre_r0_pod SET resolution = 'accepted'")
    with pytest.raises(DBAPIError, match='resolution is final once set'):
        _run(engine, "UPDATE scheduled_job_pre_r0_pod SET resolved_by = 'someone'")
    with pytest.raises(DBAPIError, match='rows are never deleted'):
        _run(engine, 'DELETE FROM scheduled_job_pre_r0_pod')
    _run(engine, "UPDATE scheduled_job_pre_r0_pod SET detail = 'confirmed shutdown'")


@pytest.mark.parametrize('table', ['scheduled_job_pod', 'scheduled_job_pre_r0_pod'])
def test_append_only_tables_grant_no_delete_or_truncate(engine: Engine, table: str):
    privileges = set(
        _run(
            engine,
            'SELECT a.privilege_type FROM pg_class c, aclexplode(c.relacl) a '
            'WHERE c.relname = :table AND a.grantee = c.relowner',
            table=table,
        ).scalars()
    )
    assert {'SELECT', 'INSERT', 'UPDATE'} <= privileges
    assert privileges.isdisjoint({'DELETE', 'TRUNCATE'})


def test_one_open_handover_attempt_at_a_time(engine: Engine):
    _run(
        engine,
        'INSERT INTO gitlab_handover_attempt (gen, committed_at, outcome) '
        "VALUES (1, now(), 'invalidated'), (2, now(), 'open')",
    )
    with pytest.raises(DBAPIError, match='uq_gitlab_handover_attempt_one_open'):
        _run(
            engine,
            'INSERT INTO gitlab_handover_attempt (gen, committed_at) VALUES (3, now())',
        )


def test_webhook_secret_writes_are_unchanged_before_the_handover(engine: Engine):
    webhook_id = _insert_webhook(engine, 'secret-1')
    _run(
        engine,
        "UPDATE gitlab_webhook SET webhook_secret = 'secret-2' WHERE id = :id",
        id=webhook_id,
    )
    _run(engine, 'INSERT INTO scheduled_job_bootstrap (id) VALUES (1)')
    _insert_webhook(engine, 'secret-3')


def test_webhook_secret_writes_are_refused_after_the_handover(engine: Engine):
    webhook_id = _insert_webhook(engine, 'secret-1')
    _start_handover(engine)

    with pytest.raises(DBAPIError, match='webhook_secret is moved'):
        _insert_webhook(engine, 'secret-2')
    # A row still holding an old-column secret cannot be rewritten until the
    # sweep moves it.
    with pytest.raises(DBAPIError, match='webhook_secret is moved'):
        _run(engine, 'UPDATE gitlab_webhook SET webhook_exists = true')
    with pytest.raises(DBAPIError, match='webhook_secret is moved'):
        _run(
            engine,
            "UPDATE gitlab_webhook SET webhook_secret = 'secret-2' WHERE id = :id",
            id=webhook_id,
        )

    _run(
        engine,
        'UPDATE gitlab_webhook SET webhook_secret_v2 = webhook_secret, '
        'webhook_secret = NULL WHERE webhook_secret IS NOT NULL',
    )
    _insert_webhook(engine, None)
    moved = _run(
        engine,
        'SELECT webhook_secret, webhook_secret_v2 FROM gitlab_webhook WHERE id = :id',
        id=webhook_id,
    ).one()
    assert tuple(moved) == (None, 'secret-1')


def test_abort_can_restore_webhook_secret(engine: Engine):
    webhook_id = _insert_webhook(engine, None)
    _start_handover(engine)
    _run(
        engine,
        "UPDATE gitlab_webhook SET webhook_secret_v2 = 'secret-1' WHERE id = :id",
        id=webhook_id,
    )

    with engine.begin() as conn:
        conn.execute(
            text('UPDATE scheduled_job_bootstrap SET gitlab_handover_at = NULL')
        )
        conn.execute(
            text(
                'UPDATE gitlab_webhook SET webhook_secret = '
                'COALESCE(webhook_secret, webhook_secret_v2), webhook_secret_v2 = NULL'
            )
        )

    restored = _run(
        engine,
        'SELECT webhook_secret, webhook_secret_v2 FROM gitlab_webhook WHERE id = :id',
        id=webhook_id,
    ).one()
    assert tuple(restored) == ('secret-1', None)


def test_downgrade_removes_the_schema_and_upgrade_restores_it(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    assert _schema_objects(engine) == {
        'tables': NEW_TABLES,
        'columns': NEW_COLUMNS,
        'functions': NEW_FUNCTIONS,
    }

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '176'
    )
    assert _schema_objects(engine) == {
        'tables': set(),
        'columns': set(),
        'functions': set(),
    }

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )
    assert _schema_objects(engine) == {
        'tables': NEW_TABLES,
        'columns': NEW_COLUMNS,
        'functions': NEW_FUNCTIONS,
    }
