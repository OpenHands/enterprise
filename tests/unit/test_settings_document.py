"""Unit tests for the single-document settings envelope."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel

from storage.org import Org
from storage.org_member import OrgMember
from storage.settings_document import (
    AGENT_SETTINGS_KEY,
    CONVERSATION_SETTINGS_KEY,
    SCHEMA_VERSION_KEY,
    SETTINGS_DOCUMENT_SCHEMA_VERSION,
    coerce_settings_document,
    empty_settings_document,
)


def test_empty_document_has_both_blocks_and_schema_version():
    document = empty_settings_document()

    assert document == {
        SCHEMA_VERSION_KEY: SETTINGS_DOCUMENT_SCHEMA_VERSION,
        AGENT_SETTINGS_KEY: {},
        CONVERSATION_SETTINGS_KEY: {},
    }


def test_coerce_preserves_unknown_keys_and_literal_nulls():
    document = coerce_settings_document(
        {
            'future_metadata': {'owner': 'x'},
            AGENT_SETTINGS_KEY: {'tools': None, 'condenser': {'enabled': False}},
            CONVERSATION_SETTINGS_KEY: None,
        }
    )

    assert document['future_metadata'] == {'owner': 'x'}
    assert document[SCHEMA_VERSION_KEY] == SETTINGS_DOCUMENT_SCHEMA_VERSION
    # A literal null is a real value for the scoped resolver, not "missing".
    assert document[AGENT_SETTINGS_KEY]['tools'] is None
    assert document[CONVERSATION_SETTINGS_KEY] == {}


def test_coerce_returns_detached_copy():
    source = {AGENT_SETTINGS_KEY: {'agent': 'old'}}
    document = coerce_settings_document(source)
    document[AGENT_SETTINGS_KEY]['agent'] = 'new'

    assert source[AGENT_SETTINGS_KEY]['agent'] == 'old'


@pytest.fixture(params=[(Org, ''), (OrgMember, '_diff')], ids=['org', 'member'])
def projection(request):
    owner_type, suffix = request.param
    return owner_type, AGENT_SETTINGS_KEY + suffix, CONVERSATION_SETTINGS_KEY + suffix


def test_block_projections_are_detached(projection):
    owner_type, agent_attr, conversation_attr = projection
    agent = {'llm': {'model': 'stored'}}
    conversation = {'nested': {'max_iterations': 10}}
    owner = owner_type(**{agent_attr: agent, conversation_attr: conversation})

    agent['llm']['model'] = 'changed input'
    conversation['nested']['max_iterations'] = 99
    getattr(owner, agent_attr)['llm']['model'] = 'changed output'
    getattr(owner, conversation_attr)['nested']['max_iterations'] = 100

    assert owner.settings[AGENT_SETTINGS_KEY] == {'llm': {'model': 'stored'}}
    assert owner.settings[CONVERSATION_SETTINGS_KEY] == {
        'nested': {'max_iterations': 10}
    }


def test_writing_one_block_preserves_the_other_and_metadata(projection):
    owner_type, agent_attr, conversation_attr = projection
    source = {
        SCHEMA_VERSION_KEY: 2,
        CONVERSATION_SETTINGS_KEY: {'max_iterations': 10},
        'future_metadata': {'owner': 'x'},
    }
    owner = owner_type(settings=source, **{agent_attr: {'tools': None}})

    assert owner.settings[AGENT_SETTINGS_KEY] == {'tools': None}
    assert owner.settings[CONVERSATION_SETTINGS_KEY] == {'max_iterations': 10}
    assert owner.settings['future_metadata'] == {'owner': 'x'}
    assert owner.settings[SCHEMA_VERSION_KEY] == 2

    setattr(owner, conversation_attr, None)
    assert owner.settings[AGENT_SETTINGS_KEY] == {'tools': None}
    assert owner.settings[CONVERSATION_SETTINGS_KEY] == {}
    assert source[CONVERSATION_SETTINGS_KEY] == {'max_iterations': 10}
    owner.settings['future_metadata']['owner'] = 'changed'
    assert source['future_metadata'] == {'owner': 'x'}


def test_block_projections_dump_models_in_json_mode(projection):
    class Block(BaseModel):
        day: date

    owner_type, agent_attr, conversation_attr = projection
    day = Block(day=date(2026, 1, 1))
    owner = owner_type(**{agent_attr: day, conversation_attr: day})

    assert getattr(owner, agent_attr) == {'day': '2026-01-01'}
    assert getattr(owner, conversation_attr) == {'day': '2026-01-01'}


def test_block_projections_default_to_empty_dicts(projection):
    owner_type, agent_attr, conversation_attr = projection
    owner = owner_type()

    assert getattr(owner, agent_attr) == {}
    assert getattr(owner, conversation_attr) == {}

    owner.settings = {AGENT_SETTINGS_KEY: None, CONVERSATION_SETTINGS_KEY: 'invalid'}
    assert getattr(owner, agent_attr) == {}
    assert getattr(owner, conversation_attr) == {}


def test_all_scopes_validate_against_parents_without_materializing_them():
    import pytest

    from openhands.app_server.settings.scoped import Set
    from storage.org import Org
    from storage.org_member import OrgMember
    from storage.settings_document import save_scope
    from storage.team import Team

    org, team, member = Org(), Team(), OrgMember()
    branch = ('conversation_settings', 'worktree_base')
    ref = ('conversation_settings', 'worktree_base_ref')
    save_scope(org, [Set(ref, 'main')])
    save_scope(team, [Set(branch, 'ref')], org.settings)
    save_scope(member, [Set(ref, 'main')], org.settings, team.settings)
    assert not member.conversation_settings_diff
    save_scope(member, [Set(ref, 'other')], org.settings, team.settings)
    assert member.conversation_settings_diff == {'worktree_base_ref': 'other'}
    before = member.settings
    with pytest.raises(ValueError, match='worktree_base_ref'):
        save_scope(member, [Set(ref, None)], org.settings, team.settings)
    assert member.settings is before
    save_scope(member, [Set(ref, 'main')], org.settings, team.settings)
    assert member.conversation_settings_diff == {}
    member.conversation_settings_diff = {'worktree_base_ref': 'main'}
    save_scope(member, [Set(ref, 'main')], org.settings, team.settings)
    assert member.conversation_settings_diff == {'worktree_base_ref': 'main'}
    assert team.settings['conversation_settings'] == {'worktree_base': 'ref'}


def test_ordinary_tool_limit_submit_preserves_or_releases_ownership():
    from openhands.app_server.settings.scoped import Set
    from storage.settings_document import save_scope
    from storage.team import Team

    org, team = Org(), Team()
    limit = ('agent_settings', 'tool_concurrency_limit')
    save_scope(org, [Set(limit, 4)])
    save_scope(team, [Set(limit, 4)], org.settings)
    assert not team.settings
    save_scope(team, [Set(limit, 7)], org.settings)
    assert team.settings['agent_settings'] == {
        'agent_kind': 'openhands',
        'tool_concurrency_limit': 7,
    }
    save_scope(team, [Set(limit, 4)], org.settings)
    assert team.settings['agent_settings'] == {}


def test_member_map_and_nullable_values_are_atomic():
    from storage.settings_document import resolve_scope, save_scope

    org = Org(agent_settings={'llm': {'extra_headers': {'X-Parent': 'one'}}})
    member = OrgMember()
    save_scope(
        member,
        (),
        org.settings,
        submitted={
            'agent_settings': {'llm': {'extra_headers': {}}},
        },
    )
    assert member.agent_settings_diff['llm'] == {'extra_headers': {}}
    org.agent_settings = {'llm': {'extra_headers': {'X-Parent': 'two'}}}
    assert (
        resolve_scope('agent_settings', org.settings, member.settings).llm.extra_headers
        == {}
    )
    save_scope(
        member,
        (),
        org.settings,
        submitted={
            'agent_settings': {'llm': {'extra_headers': None}},
        },
    )
    assert member.agent_settings_diff['llm'] == {'extra_headers': None}
    save_scope(
        member, (), org.settings, inherit=[('agent_settings', 'llm', 'extra_headers')]
    )
    assert member.agent_settings_diff == {}


def test_agent_variant_changes_do_not_pin_defaults():
    from storage.settings_document import resolve_scope, save_scope

    org = Org(agent_settings={'llm': {'model': 'parent-model'}})
    member = OrgMember()
    save_scope(
        member,
        (),
        org.settings,
        submitted={
            'agent_settings': {'agent_kind': 'acp', 'acp_server': 'codex'},
        },
    )
    assert member.agent_settings_diff == {'agent_kind': 'acp', 'acp_server': 'codex'}
    save_scope(
        member,
        (),
        org.settings,
        submitted={'agent_settings': {'agent_kind': 'openhands'}},
    )
    assert member.agent_settings_diff == {}
    assert (
        resolve_scope('agent_settings', org.settings, member.settings).llm.model
        == 'parent-model'
    )


def test_owner_keeps_encrypted_secret_out_of_child_and_rejects_wrong_key(monkeypatch):
    from openhands.sdk.utils.cipher import Cipher
    from openhands.sdk.utils.pydantic_secrets import (
        REDACTED_SECRET_VALUE,
        SecretDecryptionError,
    )
    from storage.settings_document import resolve_scope, save_scope

    cipher = Cipher('fixture-key')
    monkeypatch.setattr('storage.settings_document.settings_cipher', lambda: cipher)
    org, member = Org(), OrgMember()

    def submission(value):
        return {'agent_settings': {'verification': {'critic_api_key': value}}}

    save_scope(org, submitted=submission('fixture-value'))
    token = org.agent_settings['verification']['critic_api_key']
    assert token != 'fixture-value'
    assert cipher.try_decrypt_str(token) == 'fixture-value'
    save_scope(member, (), org.settings, submitted=submission('fixture-value'))
    assert not member.agent_settings_diff
    with pytest.raises(ValueError, match='redacted'):
        save_scope(org, submitted=submission(REDACTED_SECRET_VALUE))
    monkeypatch.setattr(
        'storage.settings_document.settings_cipher', lambda: Cipher('other-key')
    )
    with pytest.raises(SecretDecryptionError):
        resolve_scope('agent_settings', org.settings)
    assert org.agent_settings['verification']['critic_api_key'] == token


def test_invalid_child_can_be_repaired_by_explicit_reset():
    from storage.settings_document import resolve_scope, save_scope

    org = Org(conversation_settings={'worktree_base_ref': 'main'})
    member = OrgMember(conversation_settings_diff={'worktree_base': 'ref'})
    save_scope(org, inherit=[('conversation_settings', 'worktree_base_ref')])
    with pytest.raises(ValueError, match='worktree_base_ref'):
        resolve_scope('conversation_settings', org.settings, member.settings)
    save_scope(
        member, (), org.settings, inherit=[('conversation_settings', 'worktree_base')]
    )
    assert member.conversation_settings_diff == {}
