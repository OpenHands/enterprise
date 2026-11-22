"""ORM compatibility for the single settings document on org and member rows."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from openhands.sdk.settings import ConversationSettings, OpenHandsAgentSettings
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.settings_document import SETTINGS_DOCUMENT_SCHEMA_VERSION


def test_org_constructor_projects_legacy_blocks_into_one_document():
    org = Org(
        name='acme',
        agent_settings={'agent': 'openhands', 'tools': None},
        conversation_settings={'max_iterations': 42},
    )

    assert org.settings == {
        'schema_version': SETTINGS_DOCUMENT_SCHEMA_VERSION,
        'agent_settings': {'agent': 'openhands', 'tools': None},
        'conversation_settings': {'max_iterations': 42},
    }
    assert org.agent_settings == {'agent': 'openhands', 'tools': None}
    assert org.conversation_settings == {'max_iterations': 42}


def test_org_accepts_pydantic_models():
    org = Org(
        name='acme',
        agent_settings=OpenHandsAgentSettings(agent='CodeActAgent'),
        conversation_settings=ConversationSettings(max_iterations=7),
    )

    assert org.agent_settings['agent'] == 'CodeActAgent'
    assert org.conversation_settings['max_iterations'] == 7


def test_writing_one_org_block_preserves_the_other():
    org = Org(
        name='acme',
        agent_settings={'agent': 'old'},
        conversation_settings={'max_iterations': 10},
    )

    org.agent_settings = {'agent': 'new'}

    assert org.settings['agent_settings'] == {'agent': 'new'}
    assert org.settings['conversation_settings'] == {'max_iterations': 10}
    assert org.settings['schema_version'] == SETTINGS_DOCUMENT_SCHEMA_VERSION


def test_org_block_getters_are_detached():
    org = Org(
        name='acme',
        agent_settings={'agent': 'stored'},
        conversation_settings={'max_iterations': 10},
    )

    org.agent_settings['agent'] = 'mutated-copy'
    org.conversation_settings['max_iterations'] = 99

    assert org.agent_settings == {'agent': 'stored'}
    assert org.conversation_settings == {'max_iterations': 10}


def test_org_round_trips_through_postgres(session_maker: sessionmaker, create_org):
    org = create_org(
        agent_settings={'agent': 'openhands', 'tools': None},
        conversation_settings={'max_iterations': 42},
    )

    with session_maker() as session:
        loaded = session.execute(select(Org).where(Org.id == org.id)).scalar_one()
        assert loaded.settings == {
            'schema_version': SETTINGS_DOCUMENT_SCHEMA_VERSION,
            'agent_settings': {'agent': 'openhands', 'tools': None},
            'conversation_settings': {'max_iterations': 42},
        }

        loaded.agent_settings = {'agent': 'changed'}
        session.commit()

    with session_maker() as session:
        reloaded = session.execute(select(Org).where(Org.id == org.id)).scalar_one()
        assert reloaded.settings['agent_settings'] == {'agent': 'changed'}
        assert reloaded.settings['conversation_settings'] == {'max_iterations': 42}


def test_org_member_constructor_projects_legacy_diffs_into_one_document():
    member = OrgMember(
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        agent_settings_diff={'llm': {'model': 'member-model'}, 'tools': None},
        conversation_settings_diff={'max_iterations': 7},
    )

    assert member.settings == {
        'schema_version': SETTINGS_DOCUMENT_SCHEMA_VERSION,
        'agent_settings': {'llm': {'model': 'member-model'}, 'tools': None},
        'conversation_settings': {'max_iterations': 7},
    }
    assert member.agent_settings_diff == {
        'llm': {'model': 'member-model'},
        'tools': None,
    }
    assert member.conversation_settings_diff == {'max_iterations': 7}


def test_org_member_round_trips_through_postgres(
    session_maker: sessionmaker, create_org, create_user
):
    org_id = create_org().id
    user_id = create_user(current_org_id=org_id).id
    with session_maker() as session:
        role = Role(name=f'role-{uuid.uuid4().hex[:8]}', rank=3)
        session.add(role)
        session.flush()
        session.add(
            OrgMember(
                org_id=org_id,
                user_id=user_id,
                role_id=role.id,
                llm_api_key='k',
                status='active',
                agent_settings_diff={'llm': {'model': 'member-model'}},
                conversation_settings_diff={'max_iterations': 7},
            )
        )
        session.commit()

    with session_maker() as session:
        loaded = session.get(OrgMember, (org_id, user_id))
        assert loaded is not None
        assert loaded.settings == {
            'schema_version': SETTINGS_DOCUMENT_SCHEMA_VERSION,
            'agent_settings': {'llm': {'model': 'member-model'}},
            'conversation_settings': {'max_iterations': 7},
        }
        loaded.conversation_settings_diff = {'max_iterations': 9}
        session.commit()

    with session_maker() as session:
        reloaded = session.get(OrgMember, (org_id, user_id))
        assert reloaded is not None
        assert reloaded.settings['agent_settings'] == {'llm': {'model': 'member-model'}}
        assert reloaded.settings['conversation_settings'] == {'max_iterations': 9}


def test_effective_mcp_config_still_reads_the_member_document():
    member = OrgMember(
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        agent_settings_diff={'mcp_config': {'srv': {'command': 'run'}}},
    )

    assert member.effective_mcp_config == {'srv': {'command': 'run'}}
