"""Unit tests for the single-document settings envelope."""

from __future__ import annotations

from storage.settings_document import (
    AGENT_SETTINGS_KEY,
    CONVERSATION_SETTINGS_KEY,
    SCHEMA_VERSION_KEY,
    SETTINGS_DOCUMENT_SCHEMA_VERSION,
    agent_settings_from_document,
    coerce_settings_document,
    conversation_settings_from_document,
    empty_settings_document,
    with_agent_settings,
    with_conversation_settings,
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


def test_block_accessors_are_detached():
    document = {
        AGENT_SETTINGS_KEY: {'agent': 'old'},
        CONVERSATION_SETTINGS_KEY: {'max_iterations': 10},
    }

    agent = agent_settings_from_document(document)
    conversation = conversation_settings_from_document(document)
    agent['agent'] = 'new'
    conversation['max_iterations'] = 99

    assert document[AGENT_SETTINGS_KEY]['agent'] == 'old'
    assert document[CONVERSATION_SETTINGS_KEY]['max_iterations'] == 10


def test_writing_one_block_preserves_the_other_and_metadata():
    document = with_agent_settings(
        {
            SCHEMA_VERSION_KEY: SETTINGS_DOCUMENT_SCHEMA_VERSION,
            CONVERSATION_SETTINGS_KEY: {'max_iterations': 10},
            'future_metadata': {'owner': 'x'},
        },
        {'agent': 'new'},
    )

    assert document[AGENT_SETTINGS_KEY] == {'agent': 'new'}
    assert document[CONVERSATION_SETTINGS_KEY] == {'max_iterations': 10}
    assert document['future_metadata'] == {'owner': 'x'}

    document = with_conversation_settings(document, {'max_iterations': 20})
    assert document[AGENT_SETTINGS_KEY] == {'agent': 'new'}
    assert document[CONVERSATION_SETTINGS_KEY] == {'max_iterations': 20}


def test_writing_a_block_does_not_alias_the_input():
    value = {'llm': {'model': 'm'}}
    document = with_agent_settings(empty_settings_document(), value)
    value['llm']['model'] = 'changed'

    assert document[AGENT_SETTINGS_KEY]['llm']['model'] == 'm'
