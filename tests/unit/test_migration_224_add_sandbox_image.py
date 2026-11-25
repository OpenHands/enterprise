"""Real-DB test for migration 224: the image a sandbox was launched with."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def _sandbox_columns(engine: Engine) -> set[str]:
    return {c['name'] for c in inspect(engine).get_columns('v1_remote_sandbox')}


def test_upgrade_adds_a_nullable_image_that_existing_sandboxes_leave_empty(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '223'
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO v1_remote_sandbox (id, sandbox_spec_id) VALUES ('s1', 'img:1')"
            )
        )
    assert 'sandbox_image' not in _sandbox_columns(engine)

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '224'
    )

    with engine.connect() as conn:
        image = conn.execute(
            text("SELECT sandbox_image FROM v1_remote_sandbox WHERE id = 's1'")
        ).scalar_one()
    assert image is None


def test_downgrade_drops_the_image_column(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '223'
    )

    assert 'sandbox_image' not in _sandbox_columns(engine)
