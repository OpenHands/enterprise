from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from openhands.app_server.integrations.service_types import ProviderType
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import OAuthProviderDetails

migration = import_module('migrations.versions.215_add_provider_id_to_gitlab_data')


def _provider(provider_type: ProviderType) -> AuthProvider:
    return AuthProvider(
        title=provider_type.value,
        is_idp=False,
        details=OAuthProviderDetails(
            provider_type=provider_type,
            client_id='client',
            token_url=f'https://{provider_type.value}.example.com/oauth/token',
        ),
    )


def _insert_gitlab_data(connection) -> None:
    connection.execute(
        text("""
            INSERT INTO gitlab_webhook (project_id, user_id, webhook_exists)
            VALUES ('5', 'user-1', false)
        """)
    )
    connection.execute(
        text("""
            INSERT INTO repos (repo_name, repo_id, is_public)
            VALUES ('a/b', 'gitlab##7', false), ('c/d', 'github##7', false)
        """)
    )
    connection.execute(
        text("""
            INSERT INTO "user-repos" (user_id, repo_id)
            VALUES ('user-1', 'gitlab##7'), ('user-1', 'github##7')
        """)
    )


def _provider_ids(connection, query: str) -> list:
    return [row[0] for row in connection.execute(text(query))]


def test_existing_gitlab_rows_get_the_lowest_id_gitlab_provider(engine, session_maker):
    with session_maker() as session:
        github, first, second = (
            _provider(ProviderType.GITHUB),
            _provider(ProviderType.GITLAB),
            _provider(ProviderType.GITLAB),
        )
        session.add_all([github, first, second])
        session.commit()
        first_id = first.id

    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            _insert_gitlab_data(connection)
            migration.upgrade()

        assert _provider_ids(connection, 'SELECT provider_id FROM gitlab_webhook') == [
            first_id
        ]
        assert _provider_ids(
            connection, 'SELECT provider_id FROM repos ORDER BY repo_id'
        ) == [None, first_id]
        assert _provider_ids(
            connection, 'SELECT provider_id FROM "user-repos" ORDER BY repo_id'
        ) == [None, first_id]


def test_rows_stay_without_a_provider_when_none_is_configured(engine):
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            _insert_gitlab_data(connection)
            migration.upgrade()

        assert _provider_ids(connection, 'SELECT provider_id FROM gitlab_webhook') == [
            None
        ]


def test_a_project_id_is_unique_per_provider(engine, session_maker):
    with session_maker() as session:
        first, second = _provider(ProviderType.GITLAB), _provider(ProviderType.GITLAB)
        session.add_all([first, second])
        session.commit()
        first_id, second_id = first.id, second.id

    insert = text("""
        INSERT INTO gitlab_webhook (project_id, group_id, user_id, webhook_exists, provider_id)
        VALUES (:project_id, :group_id, 'user-1', false, :provider_id)
    """)
    with engine.begin() as connection:
        for provider_id in (first_id, second_id):
            connection.execute(
                insert,
                {'project_id': '5', 'group_id': None, 'provider_id': provider_id},
            )
            connection.execute(
                insert,
                {'project_id': None, 'group_id': '9', 'provider_id': provider_id},
            )

    for params in (
        {'project_id': '5', 'group_id': None, 'provider_id': first_id},
        {'project_id': None, 'group_id': '9', 'provider_id': first_id},
        {'project_id': '5', 'group_id': None, 'provider_id': None},
    ):
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(insert, params)
                connection.execute(insert, params)


def test_downgrade_keeps_the_oldest_row_of_a_project_both_providers_hold(
    engine, session_maker
):
    with session_maker() as session:
        first, second = _provider(ProviderType.GITLAB), _provider(ProviderType.GITLAB)
        session.add_all([first, second])
        session.commit()
        first_id, second_id = first.id, second.id

    with engine.begin() as connection:
        for provider_id in (first_id, second_id):
            connection.execute(
                text("""
                    INSERT INTO gitlab_webhook
                        (project_id, user_id, webhook_exists, provider_id)
                    VALUES ('5', 'user-1', false, :provider_id)
                """),
                {'provider_id': provider_id},
            )
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

        assert (
            connection.execute(text('SELECT count(*) FROM gitlab_webhook')).scalar_one()
            == 1
        )
