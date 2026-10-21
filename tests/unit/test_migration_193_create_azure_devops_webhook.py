"""Real-DB tests for migration 193 (``azure_devops_webhook`` table)."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from tests import postgres_testdb


def test_downgrade_drops_and_upgrade_recreates_the_table(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '192'
    )
    assert not inspect(engine).has_table('azure_devops_webhook')

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '193'
    )
    assert inspect(engine).has_table('azure_devops_webhook')


def test_organization_is_unique(engine: Engine):
    insert = text("INSERT INTO azure_devops_webhook (organization) VALUES ('contoso')")
    with engine.begin() as conn:
        conn.execute(insert)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert)
