"""PostgreSQL round-trip tests for migration 223 (settings documents).

The migration must copy both legacy blocks into one document, refuse to drop a
plaintext credential that has no encrypted owner, and restore the legacy
columns on rollback.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _table_columns(engine: Engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    'SELECT column_name FROM information_schema.columns '
                    'WHERE table_name = :table'
                ),
                {'table': table},
            ).scalars()
        )


def _insert_legacy_org(
    engine: Engine,
    *,
    agent_settings: dict[str, Any],
    conversation_settings: dict[str, Any],
    llm_api_key: str | None = None,
) -> UUID:
    org_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO org
                    (id, name, agent_settings, conversation_settings, _llm_api_key)
                VALUES
                    (:id, :name, CAST(:agent AS json), CAST(:conversation AS json),
                     :llm_api_key)
                """
            ),
            {
                'id': org_id,
                'name': f'legacy-org-{org_id}',
                'agent': json.dumps(agent_settings),
                'conversation': json.dumps(conversation_settings),
                'llm_api_key': llm_api_key,
            },
        )
    return org_id


def _insert_legacy_member(
    engine: Engine,
    org_id: UUID,
    *,
    agent_settings_diff: dict[str, Any],
    conversation_settings_diff: dict[str, Any],
    llm_api_key: str = 'ciphertext-key',
    mcp_config: str | None = None,
) -> UUID:
    user_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text('INSERT INTO "user" (id, current_org_id) VALUES (:id, :org_id)'),
            {'id': user_id, 'org_id': org_id},
        )
        role_id = conn.execute(
            text('INSERT INTO role (name, rank) VALUES (:name, :rank) RETURNING id'),
            {'name': f'legacy-role-{uuid.uuid4().hex}', 'rank': 3},
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO org_member
                    (org_id, user_id, role_id, _llm_api_key,
                     has_custom_llm_api_key, agent_settings_diff,
                     conversation_settings_diff, mcp_config)
                VALUES
                    (:org_id, :user_id, :role_id, :llm_api_key, false,
                     CAST(:agent AS json), CAST(:conversation AS json), :mcp_config)
                """
            ),
            {
                'org_id': org_id,
                'user_id': user_id,
                'role_id': role_id,
                'llm_api_key': llm_api_key,
                'agent': json.dumps(agent_settings_diff),
                'conversation': json.dumps(conversation_settings_diff),
                'mcp_config': mcp_config,
            },
        )
    return user_id


def _read_settings(engine: Engine, table: str, row_id: UUID, id_column: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text(f'SELECT settings FROM {table} WHERE {id_column} = :row_id'),
            {'row_id': row_id},
        ).one()
    return _json(row.settings)


def _read_legacy_columns(
    engine: Engine, table: str, where: str, params: dict
) -> tuple[Any, Any]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f'SELECT agent_settings AS agent, conversation_settings AS conversation '
                f'FROM {table} WHERE {where}'
            )
            if table == 'org'
            else text(
                'SELECT agent_settings_diff AS agent, '
                'conversation_settings_diff AS conversation '
                f'FROM {table} WHERE {where}'
            ),
            params,
        ).one()
    return _json(row.agent), _json(row.conversation)


def test_upgrade_merges_both_blocks_into_one_document(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '222'
    )
    org_id = _insert_legacy_org(
        engine,
        agent_settings={'schema_version': 7, 'agent_kind': 'openhands', 'agent': 'a'},
        conversation_settings={'max_iterations': 42},
    )
    member_id = _insert_legacy_member(
        engine,
        org_id,
        agent_settings_diff={'llm': {'model': 'member-model'}, 'tools': None},
        conversation_settings_diff={'max_iterations': 7},
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '223'
    )

    assert _read_settings(engine, 'org', org_id, 'id') == {
        'schema_version': 1,
        'agent_settings': {
            'schema_version': 7,
            'agent_kind': 'openhands',
            'agent': 'a',
        },
        'conversation_settings': {'max_iterations': 42},
    }
    assert _read_settings(engine, 'org_member', member_id, 'user_id') == {
        'schema_version': 1,
        'agent_settings': {'llm': {'model': 'member-model'}, 'tools': None},
        'conversation_settings': {'max_iterations': 7},
    }

    assert 'agent_settings' not in _table_columns(engine, 'org')
    assert 'conversation_settings' not in _table_columns(engine, 'org')
    assert 'agent_settings_diff' not in _table_columns(engine, 'org_member')
    assert 'conversation_settings_diff' not in _table_columns(engine, 'org_member')


def test_upgrade_aborts_when_org_inline_key_is_real(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '222'
    )
    _insert_legacy_org(
        engine,
        agent_settings={'llm': {'model': 'm', 'api_key': 'sk-plaintext'}},
        conversation_settings={},
        llm_api_key=None,
    )

    with pytest.raises(
        postgres_testdb.PostgresUnavailableError, match='real inline llm.api_key'
    ):
        postgres_testdb.run_alembic(
            test_database.server, test_database.name, 'upgrade', '223'
        )

    # The failed upgrade rolled back: the legacy column is still there and no
    # document was created.
    assert 'agent_settings' in _table_columns(engine, 'org')
    assert 'settings' not in _table_columns(engine, 'org')
    with engine.connect() as conn:
        version = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
    assert version == '222'


def test_upgrade_aborts_when_org_inline_mcp_config_is_real(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '222'
    )
    _insert_legacy_org(
        engine,
        agent_settings={'mcp_config': {'srv': {'command': 'run'}}},
        conversation_settings={},
    )

    with pytest.raises(
        postgres_testdb.PostgresUnavailableError, match='real inline mcp_config'
    ):
        postgres_testdb.run_alembic(
            test_database.server, test_database.name, 'upgrade', '223'
        )

    assert 'settings' not in _table_columns(engine, 'org')


def test_upgrade_aborts_when_member_mcp_config_is_real(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '222'
    )
    org_id = _insert_legacy_org(engine, agent_settings={}, conversation_settings={})
    _insert_legacy_member(
        engine,
        org_id,
        agent_settings_diff={'mcp_config': {'srv': {}}},
        conversation_settings_diff={},
        mcp_config=None,
    )

    with pytest.raises(
        postgres_testdb.PostgresUnavailableError, match='real inline mcp_config'
    ):
        postgres_testdb.run_alembic(
            test_database.server, test_database.name, 'upgrade', '223'
        )

    assert 'settings' not in _table_columns(engine, 'org_member')


def test_upgrade_aborts_when_real_secret_has_an_encrypted_owner(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    """A populated owner column is not proof that the inline copy is equal."""
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '222'
    )
    _insert_legacy_org(
        engine,
        agent_settings={'llm': {'api_key': 'sk-live'}},
        conversation_settings={},
        llm_api_key='ciphertext-org-key',
    )

    with pytest.raises(
        postgres_testdb.PostgresUnavailableError, match='real credentials'
    ):
        postgres_testdb.run_alembic(
            test_database.server, test_database.name, 'upgrade', '223'
        )

    assert 'settings' not in _table_columns(engine, 'org')


def test_upgrade_allows_placeholder_credentials(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '222'
    )
    placeholders: list[dict] = [
        {'llm': {'model': 'm', 'api_key': None}, 'mcp_config': {}},
        {'llm': {'model': 'm', 'api_key': ''}, 'mcp_config': None},
        {'llm': {'model': 'm', 'api_key': '**********'}},
        {'llm': {'model': 'm', 'api_key': '   '}, 'mcp_config': {}},
        {'llm': {'model': 'm', 'api_key': '***'}},
    ]
    # The first org has no encrypted key owner at all: placeholders must not
    # block a normal row.
    org_ids = [
        _insert_legacy_org(
            engine,
            agent_settings=agent_settings,
            conversation_settings={},
            llm_api_key=None,
        )
        for agent_settings in placeholders
    ]
    member_id = _insert_legacy_member(
        engine,
        org_ids[0],
        agent_settings_diff={
            'llm': {'model': 'mm', 'api_key': '***'},
            'mcp_config': {},
        },
        conversation_settings_diff={},
        llm_api_key='ciphertext-member-key',
        mcp_config=None,
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '223'
    )

    for org_id, agent_settings in zip(org_ids, placeholders, strict=True):
        assert (
            _read_settings(engine, 'org', org_id, 'id')['agent_settings']
            == agent_settings
        )
    assert _read_settings(engine, 'org_member', member_id, 'user_id')[
        'agent_settings'
    ] == {'llm': {'model': 'mm', 'api_key': '***'}, 'mcp_config': {}}


def test_downgrade_restores_legacy_columns(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '222'
    )
    org_agent = {'schema_version': 7, 'agent': 'a', 'tools': None}
    org_conversation = {'max_iterations': 42}
    member_agent = {'llm': {'model': 'member-model'}}
    member_conversation = {'max_iterations': 7}
    org_id = _insert_legacy_org(
        engine,
        agent_settings=org_agent,
        conversation_settings=org_conversation,
    )
    member_id = _insert_legacy_member(
        engine,
        org_id,
        agent_settings_diff=member_agent,
        conversation_settings_diff=member_conversation,
    )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '223'
    )
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '222'
    )

    assert _read_legacy_columns(engine, 'org', 'id = :row_id', {'row_id': org_id}) == (
        org_agent,
        org_conversation,
    )
    assert _read_legacy_columns(
        engine, 'org_member', 'user_id = :row_id', {'row_id': member_id}
    ) == (member_agent, member_conversation)

    # A second upgrade is stable and idempotent for the document contents.
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '223'
    )
    assert _read_settings(engine, 'org', org_id, 'id')['agent_settings'] == org_agent
    assert (
        _read_settings(engine, 'org_member', member_id, 'user_id')['agent_settings']
        == member_agent
    )


def test_preflight_on_last_main_revision(engine, test_database):
    from importlib import import_module

    preflight = import_module(
        'migrations.versions.223_consolidate_settings_documents'
    ).preflight
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '221'
    )
    preflight(engine)
    _insert_legacy_org(
        engine,
        agent_settings={'llm': {'api_key': 'synthetic-marker'}},
        conversation_settings={},
    )
    with pytest.raises(RuntimeError, match=r'org: 1 row\(s\)') as error:
        preflight(engine)
    assert 'synthetic-marker' not in str(error.value)
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '221'
