"""PostgreSQL round-trip tests for migration 226 (settings documents).

The migration must preserve sparse ownership, reject meaningful inline
credentials, and restore the legacy column layout on downgrade.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from openhands.sdk.settings import AGENT_SETTINGS_SCHEMA_VERSION
from openhands.sdk.tool.defaults import (
    SUB_AGENT_TOOL_NAME,
    SWITCH_LLM_TOOL_NAME,
    selects_tool,
)
from storage.encrypt_utils import encrypt_legacy_value, encrypt_value
from storage.org import Org
from storage.org_member import OrgMember
from storage.settings_document import resolve_scope, save_scope
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
    custom_key: bool = False,
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
                    (:org_id, :user_id, :role_id, :llm_api_key, :custom_key,
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
                'custom_key': custom_key,
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
        test_database.server, test_database.name, 'downgrade', '225'
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
        test_database.server, test_database.name, 'upgrade', '226'
    )

    assert _read_settings(engine, 'org', org_id, 'id') == {
        'schema_version': 1,
        'agent_settings': {
            'schema_version': AGENT_SETTINGS_SCHEMA_VERSION,
            'agent_kind': 'openhands',
            'agent': 'a',
        },
        'conversation_settings': {'max_iterations': 42},
    }
    assert _read_settings(engine, 'org_member', member_id, 'user_id') == {
        'schema_version': 1,
        'agent_settings': {
            'schema_version': AGENT_SETTINGS_SCHEMA_VERSION,
            'agent_kind': 'openhands',
            'llm': {'model': 'member-model'},
            'tools': None,
        },
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
        test_database.server, test_database.name, 'downgrade', '225'
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
            test_database.server, test_database.name, 'upgrade', '226'
        )

    # The failed upgrade rolled back: the legacy column is still there and no
    # document was created.
    assert 'agent_settings' in _table_columns(engine, 'org')
    assert 'settings' not in _table_columns(engine, 'org')
    with engine.connect() as conn:
        version = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
    assert version == '225'


def test_upgrade_aborts_when_org_inline_mcp_config_is_real(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '225'
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
            test_database.server, test_database.name, 'upgrade', '226'
        )

    assert 'settings' not in _table_columns(engine, 'org')


def test_upgrade_aborts_when_member_mcp_config_is_real(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '225'
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
            test_database.server, test_database.name, 'upgrade', '226'
        )

    assert 'settings' not in _table_columns(engine, 'org_member')


def test_upgrade_aborts_when_real_secret_has_an_encrypted_owner(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    """A populated owner column is not proof that the inline copy is equal."""
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '225'
    )
    _insert_legacy_org(
        engine,
        agent_settings={'llm': {'api_key': 'sk-live'}},
        conversation_settings={},
        llm_api_key='ciphertext-org-key',
    )

    with pytest.raises(
        postgres_testdb.PostgresUnavailableError, match='real inline llm.api_key'
    ):
        postgres_testdb.run_alembic(
            test_database.server, test_database.name, 'upgrade', '226'
        )

    assert 'settings' not in _table_columns(engine, 'org')


def test_upgrade_allows_placeholder_credentials(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '225'
    )
    placeholders: list[dict] = [
        {'llm': {'model': 'm', 'api_key': None}, 'mcp_config': {}},
        {'llm': {'model': 'm', 'api_key': ''}, 'mcp_config': None},
        {'llm': {'model': 'm', 'api_key': '**********'}},
        {'llm': {'model': 'm', 'api_key': '   '}, 'mcp_config': {}},
        {'llm': {'model': 'm', 'api_key': '***'}},
    ]
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
        test_database.server, test_database.name, 'upgrade', '226'
    )

    for org_id in org_ids:
        assert _read_settings(engine, 'org', org_id, 'id')['agent_settings'] == {
            'agent_kind': 'openhands',
            'llm': {'model': 'm'},
            'schema_version': AGENT_SETTINGS_SCHEMA_VERSION,
        }
    assert _read_settings(engine, 'org_member', member_id, 'user_id')[
        'agent_settings'
    ] == {
        'agent_kind': 'openhands',
        'llm': {'model': 'mm'},
        'schema_version': AGENT_SETTINGS_SCHEMA_VERSION,
    }


def test_downgrade_restores_legacy_columns(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '225'
    )
    org_agent = {'schema_version': 7, 'agent': 'a', 'tools': None}
    org_conversation = {'max_iterations': 42}
    member_agent = {'llm': {'model': 'member-model'}}
    member_conversation = {'max_iterations': 7}
    canonical = {
        'agent_kind': 'openhands',
        'schema_version': AGENT_SETTINGS_SCHEMA_VERSION,
    }
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
        test_database.server, test_database.name, 'upgrade', '226'
    )
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '225'
    )

    assert _read_legacy_columns(engine, 'org', 'id = :row_id', {'row_id': org_id}) == (
        org_agent | canonical,
        org_conversation,
    )
    assert _read_legacy_columns(
        engine, 'org_member', 'user_id = :row_id', {'row_id': member_id}
    ) == (member_agent | canonical, member_conversation)

    # A second upgrade is stable and idempotent for the document contents.
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '226'
    )
    assert (
        _read_settings(engine, 'org', org_id, 'id')['agent_settings']
        == org_agent | canonical
    )
    assert (
        _read_settings(engine, 'org_member', member_id, 'user_id')['agent_settings']
        == member_agent | canonical
    )


def test_preflight_on_last_main_revision(engine, test_database):
    from importlib import import_module

    preflight = import_module(
        'migrations.versions.226_consolidate_settings_documents'
    ).preflight
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '224'
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
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '224'


@pytest.mark.parametrize('kind', ['openhands', 'acp'])
def test_migration_preserves_ownership_across_parent_changes(
    engine, test_database, session_maker, kind
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '224'
    )
    org_kind = 'llm' if kind == 'openhands' else kind
    pin = (
        {'tool_concurrency_limit': 3}
        if kind == 'openhands'
        else {'acp_model': 'legacy-model'}
    )
    org_values = {'agent_kind': org_kind, **pin}
    member_values = dict(pin)
    if kind == 'openhands':
        member_values |= {
            'schema_version': 7,
            'enable_sub_agents': True,
            'enable_switch_llm_tool': False,
        }
    org_key = encrypt_legacy_value('old-org-key')
    member_key = encrypt_value('old-member-key')
    mcp = {'legacy': {'command': 'test-fixture', 'env': {'TOKEN': 'old-mcp-secret'}}}
    member_mcp = encrypt_legacy_value(json.dumps(mcp))
    org_id = _insert_legacy_org(
        engine,
        agent_settings=org_values,
        conversation_settings={'max_iterations': 80, 'worktree_base_ref': 'main'},
        llm_api_key=org_key,
    )
    pinned_id = _insert_legacy_member(
        engine,
        org_id,
        agent_settings_diff=member_values,
        conversation_settings_diff={'max_iterations': 80, 'worktree_base': 'ref'},
        llm_api_key=member_key,
        mcp_config=member_mcp,
        custom_key=True,
    )
    inherited_id = _insert_legacy_member(
        engine,
        org_id,
        agent_settings_diff={'agent_context': {'load_memory': True}},
        conversation_settings_diff={},
        llm_api_key=encrypt_value(''),
    )
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )
    with session_maker() as session:
        org = session.get(Org, org_id)
        pinned = session.get(OrgMember, (org_id, pinned_id))
        inherited = session.get(OrgMember, (org_id, inherited_id))
        pinned_values = pinned.settings['agent_settings']
        assert pinned_values['agent_kind'] == kind
        assert all(pinned_values[key] == value for key, value in pin.items())
        assert inherited.settings['agent_settings'] == {
            'schema_version': AGENT_SETTINGS_SCHEMA_VERSION,
            'agent_context': {'load_memory': True},
        }
        assert pinned.settings['conversation_settings'] == {
            'max_iterations': 80,
            'worktree_base': 'ref',
        }
        assert inherited.settings['conversation_settings'] == {}
        assert org._llm_api_key == org_key
        assert org.llm_api_key.get_secret_value() == 'old-org-key'
        assert pinned._llm_api_key == member_key
        assert pinned.llm_api_key.get_secret_value() == 'old-member-key'
        assert pinned.mcp_config == mcp
        assert (
            session.execute(
                text('SELECT mcp_config FROM org_member WHERE user_id = :id'),
                {'id': pinned_id},
            ).scalar_one()
            == member_mcp
        )
        assert all(
            'mcp_config' not in owner.settings['agent_settings']
            for owner in (org, pinned, inherited)
        )
        assert all(
            'llm' not in owner.settings['agent_settings']
            for owner in (org, pinned, inherited)
        )

        def resolved(block, member):
            return resolve_scope(block, org.settings, member.settings)

        before = resolved('agent_settings', pinned)
        assert before.agent_kind == kind
        assert all(getattr(before, key) == value for key, value in pin.items())
        if kind == 'openhands':
            assert selects_tool(before.tools, SUB_AGENT_TOOL_NAME)
            assert not selects_tool(before.tools, SWITCH_LLM_TOOL_NAME)
        assert resolved('conversation_settings', pinned).max_iterations == 80
        assert resolved('conversation_settings', pinned).worktree_base_ref == 'main'
        assert resolved('conversation_settings', inherited).max_iterations == 80

        for member in (pinned, inherited):
            original = member.settings
            save_scope(
                member,
                (),
                org.settings,
                submitted={'conversation_settings': {'max_iterations': 80}},
            )
            assert member.settings == original
        session.commit()

        next_kind = 'acp' if kind == 'openhands' else 'openhands'
        org.settings = {
            **org.settings,
            'agent_settings': {'agent_kind': next_kind},
            'conversation_settings': {
                'max_iterations': 120,
                'worktree_base_ref': 'release',
            },
        }
        session.commit()
        assert resolved('agent_settings', inherited).agent_kind == next_kind
        after = resolved('agent_settings', pinned)
        assert after.agent_kind == kind
        assert all(getattr(after, key) == value for key, value in pin.items())
        if kind == 'openhands':
            assert after.tools == before.tools
        assert resolved('conversation_settings', pinned).max_iterations == 80
        assert resolved('conversation_settings', pinned).worktree_base_ref == 'release'
        assert resolved('conversation_settings', inherited).max_iterations == 120

        # Removing one migrated pin leaves its sibling and the parent intact.
        parent_document = org.settings
        save_scope(
            pinned,
            (),
            org.settings,
            inherit=[('conversation_settings', 'max_iterations')],
        )
        assert pinned.settings['conversation_settings'] == {'worktree_base': 'ref'}
        assert resolved('conversation_settings', pinned).max_iterations == 120
        save_scope(
            pinned,
            (),
            org.settings,
            inherit=[('conversation_settings', 'worktree_base')],
        )
        session.commit()
        assert pinned.settings['conversation_settings'] == {}
        assert org.settings == parent_document


@pytest.mark.parametrize(
    ('agent', 'reason'),
    [
        ({'future_field': 'synthetic-marker'}, 'unknown or non-persistable setting'),
        ({'acp_model': 'synthetic-marker'}, 'unknown or non-persistable setting'),
        (
            {'verification': {'critic_api_key': 'synthetic-marker'}},
            'real inline verification.critic_api_key',
        ),
        (
            {'llm': {'aws_session_token': 'synthetic-marker'}},
            'real inline llm.aws_session_token',
        ),
    ],
)
def test_preflight_blocks_unrecognized_rows_without_disclosing_values(
    engine, test_database, agent, reason
):
    from importlib import import_module

    module = import_module('migrations.versions.226_consolidate_settings_documents')
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '225'
    )
    org_id = _insert_legacy_org(engine, agent_settings={}, conversation_settings={})
    _insert_legacy_member(
        engine, org_id, agent_settings_diff=agent, conversation_settings_diff={}
    )
    with pytest.raises(RuntimeError, match=reason) as error:
        module.preflight(engine)
    assert 'synthetic-marker' not in str(error.value)
    assert 'settings' not in _table_columns(engine, 'org_member')
