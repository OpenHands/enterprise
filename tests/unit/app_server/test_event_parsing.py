from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks

from openhands.agent_server.models import Success
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
)
from openhands.app_server.event.event_parsing import (
    UnparsedAction,
    UnparsedEvent,
    UnparsedObservation,
    parse_event_payload,
)
from openhands.app_server.event_callback.webhook_router import on_event
from openhands.sdk.event import ActionEvent, ConversationStateUpdateEvent


def _custom_action_event_payload() -> dict:
    return {
        'kind': 'ActionEvent',
        'id': '11111111-1111-1111-1111-111111111111',
        'timestamp': '2026-01-01T00:00:00',
        'source': 'agent',
        'parent_id': None,
        'thought': [],
        'reasoning_content': None,
        'thinking_blocks': [],
        'responses_reasoning_item': None,
        'action': {
            'kind': 'ClientDefinedBrowserAction',
            'selector': '#submit',
            'operation': 'click',
        },
        'tool_name': 'client_browser',
        'tool_call_id': 'call_1',
        'tool_call': {
            'id': 'call_1',
            'name': 'client_browser',
            'arguments': '{}',
            'origin': 'completion',
            'responses_item_id': None,
        },
        'llm_response_id': 'response_1',
        'security_risk': 'UNKNOWN',
        'critic_result': None,
        'summary': None,
    }


def test_parse_event_payload_preserves_unknown_action_payload():
    payload = _custom_action_event_payload()
    event = parse_event_payload(payload)

    assert isinstance(event, ActionEvent)
    assert isinstance(event.action, UnparsedAction)
    assert event.action.original_kind == 'ClientDefinedBrowserAction'
    assert event.action.raw_payload == {
        'kind': 'ClientDefinedBrowserAction',
        'selector': '#submit',
        'operation': 'click',
    }
    assert event.model_dump(mode='json') == payload

    round_tripped = parse_event_payload(event.model_dump(mode='json'))
    assert isinstance(round_tripped, ActionEvent)
    assert isinstance(round_tripped.action, UnparsedAction)
    assert round_tripped.action.raw_payload == event.action.raw_payload


def test_unparsed_fallback_types_serialize_raw_payload():
    raw_action = {'kind': 'ClientAction_custom', 'value': 1}
    raw_observation = {'kind': 'ClientObservation_custom', 'value': 2}
    raw_event = {'kind': 'FutureEvent', 'value': 3}

    assert (
        UnparsedAction(
            original_kind='ClientAction_custom', raw_payload=raw_action
        ).model_dump(mode='json')
        == raw_action
    )
    assert (
        UnparsedObservation(
            original_kind='ClientObservation_custom', raw_payload=raw_observation
        ).model_dump(mode='json')
        == raw_observation
    )
    assert (
        UnparsedEvent(
            source='environment', original_kind='FutureEvent', raw_payload=raw_event
        ).model_dump(mode='json')
        == raw_event
    )


def test_parse_event_payload_falls_back_when_known_event_validation_fails():
    payload = {
        'kind': 'MessageEvent',
        'id': '22222222-2222-2222-2222-222222222222',
        'timestamp': '2026-01-01T00:00:00',
        'source': 'agent',
        'parent_id': None,
        'llm_message': {
            'role': 'assistant',
            'content': [{'cache_prompt': False, 'type': 'text', 'text': 'hello'}],
            'tool_calls': None,
            'tool_call_id': None,
            'name': None,
            'reasoning_content': None,
            'thinking_blocks': [],
            'responses_reasoning_item': None,
        },
        'llm_response_id': None,
        'activated_skills': [],
        'extended_content': [],
        'sender': None,
        'critic_result': None,
        'future_field': 'from a newer SDK',
    }

    with patch('openhands.app_server.event.event_parsing._logger') as logger:
        event = parse_event_payload(payload)

    assert isinstance(event, UnparsedEvent)
    assert event.original_kind == 'MessageEvent'
    assert event.raw_payload == payload
    assert event.model_dump(mode='json') == payload
    logger.warning.assert_called_once()
    assert logger.warning.call_args.args[0] == (
        'event_parsing:falling_back_to_unparsed_event'
    )


def test_parse_event_payload_preserves_unknown_top_level_event_without_source():
    payload = {'kind': 'FutureEvent', 'future_field': 'value'}

    event = parse_event_payload(payload)

    assert isinstance(event, UnparsedEvent)
    assert event.source == 'environment'
    assert event.model_dump(mode='json') == payload


@pytest.mark.asyncio
async def test_on_event_accepts_unknown_action_payload_and_processes_batch():
    conversation_id = uuid4()
    app_conversation_info = AppConversationInfo(
        id=conversation_id,
        sandbox_id='sandbox_123',
        created_by_user_id='user_123',
    )
    event_service = AsyncMock()
    app_conversation_info_service = AsyncMock()
    execution_status_event = ConversationStateUpdateEvent(
        key='execution_status', value='running'
    )

    with patch(
        'openhands.app_server.event_callback.webhook_router._run_callbacks_in_bg_and_close'
    ) as callbacks:
        background_tasks = BackgroundTasks()
        result = await on_event(
            background_tasks=background_tasks,
            conversation_id=conversation_id,
            events=[
                _custom_action_event_payload(),
                execution_status_event.model_dump(mode='json'),
            ],
            app_conversation_info=app_conversation_info,
            app_conversation_info_service=app_conversation_info_service,
            event_service=event_service,
        )

    assert isinstance(result, Success)
    assert event_service.save_event.call_count == 2
    saved_custom_event = event_service.save_event.call_args_list[0].args[1]
    assert isinstance(saved_custom_event, ActionEvent)
    assert isinstance(saved_custom_event.action, UnparsedAction)
    assert saved_custom_event.action.original_kind == 'ClientDefinedBrowserAction'
    app_conversation_info_service.update_execution_status.assert_called_once_with(
        conversation_id, 'running'
    )
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is callbacks
    callback_events = background_tasks.tasks[0].args[2]
    assert isinstance(callback_events[0].action, UnparsedAction)
