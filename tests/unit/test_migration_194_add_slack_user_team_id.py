"""Real-DB tests for migration 194 (``team_id`` on ``slack_users``).

The backfill is only safe when one workspace is installed, so assert that it
fills existing links in that case and leaves them NULL otherwise.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def _link_slack_user_pre_194(engine: Engine, team_ids: list[str]) -> int:
    """Install the given workspaces and link one Slack user before 194."""
    with engine.begin() as conn:
        for team_id in team_ids:
            conn.execute(
                text(
                    'INSERT INTO slack_teams (team_id, bot_access_token) '
                    "VALUES (:team_id, 'test-only')"
                ),
                {'team_id': team_id},
            )
        return conn.execute(
            text(
                """
                INSERT INTO slack_users
                    (keycloak_user_id, slack_user_id, slack_display_name)
                VALUES ('user-1', 'U1', 'Test User')
                RETURNING id
                """
            )
        ).scalar_one()


@pytest.mark.parametrize(
    ('installed_team_ids', 'expected_team_id'),
    [(['T1'], 'T1'), (['T1', 'T2'], None)],
)
def test_upgrade_backfills_team_id_only_for_a_single_workspace(
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
    installed_team_ids: list[str],
    expected_team_id: str | None,
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '193'
    )
    slack_user_id = _link_slack_user_pre_194(engine, installed_team_ids)

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '194'
    )

    with engine.connect() as conn:
        team_id = conn.execute(
            text('SELECT team_id FROM slack_users WHERE id = :id'),
            {'id': slack_user_id},
        ).scalar_one()
    assert team_id == expected_team_id
