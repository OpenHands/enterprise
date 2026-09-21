"""Unit tests for MemoryChangeCallbackProcessor.

These tests construct real ``ObservationEvent`` + ``FileEditorObservation``
instances (no mocks) and exercise the processor's detection and recording
logic directly.
"""

import json
from uuid import uuid4

import pytest

from openhands.app_server.event_callback.event_callback_models import (
    EventCallback,
    EventCallbackStatus,
)
from openhands.app_server.event_callback.event_callback_result_models import (
    EventCallbackResultStatus,
)
from openhands.app_server.event_callback.memory_change_callback_processor import (
    MemoryChangeCallbackProcessor,
)
from openhands.sdk import Message, MessageEvent, TextContent
from openhands.sdk.event import ObservationEvent
from openhands.tools.file_editor.definition import FileEditorObservation

_PROJECT_PATH = '/workspace/.openhands/memory/MEMORY.md'
_USER_PATH = '/home/user/.openhands/memory/memory/MEMORY.md'


def _make_observation_event(
    path: str,
    command: str = 'str_replace',
    old_content: str | None = 'old text',
    new_content: str | None = 'new text',
    prev_exist: bool = True,
    is_error: bool = False,
    tool_name: str = 'file_editor',
) -> ObservationEvent:
    observation = FileEditorObservation(
        command=command,
        path=path,
        old_content=old_content,
        new_content=new_content,
        prev_exist=prev_exist,
        is_error=is_error,
    )
    return ObservationEvent(
        observation=observation,
        tool_name=tool_name,
        action_id='action-1',
        tool_call_id='tc-1',
    )


def _make_callback(conversation_id) -> EventCallback:
    return EventCallback(
        conversation_id=conversation_id,
        event_kind=MemoryChangeCallbackProcessor.get_event_kind(),
        processor=MemoryChangeCallbackProcessor(),
    )


class TestMemoryChangeCallbackProcessor:
    """Tests for MemoryChangeCallbackProcessor."""

    @pytest.mark.asyncio
    async def test_fires_on_project_memory_edit(self):
        conversation_id = uuid4()
        callback = _make_callback(conversation_id)
        event = _make_observation_event(_PROJECT_PATH)
        processor = MemoryChangeCallbackProcessor()

        result = await processor(conversation_id, callback, event)

        assert result is not None
        assert result.status is EventCallbackResultStatus.SUCCESS
        assert result.event_callback_id == callback.id
        assert result.event_id == event.id
        assert result.conversation_id == conversation_id
        detail = json.loads(result.detail)
        assert detail['memory_tier'] == 'project'
        assert detail['path'] == _PROJECT_PATH
        assert detail['old_content'] == 'old text'
        assert detail['new_content'] == 'new text'

    @pytest.mark.asyncio
    async def test_fires_on_user_memory_edit(self):
        conversation_id = uuid4()
        callback = _make_callback(conversation_id)
        event = _make_observation_event(_USER_PATH)
        processor = MemoryChangeCallbackProcessor()

        result = await processor(conversation_id, callback, event)

        assert result is not None
        detail = json.loads(result.detail)
        assert detail['memory_tier'] == 'user'

    @pytest.mark.asyncio
    async def test_captures_create_command_with_no_old_content(self):
        conversation_id = uuid4()
        callback = _make_callback(conversation_id)
        event = _make_observation_event(
            _PROJECT_PATH,
            command='create',
            old_content=None,
            new_content='fresh memory',
            prev_exist=False,
        )
        processor = MemoryChangeCallbackProcessor()

        result = await processor(conversation_id, callback, event)

        assert result is not None
        detail = json.loads(result.detail)
        assert detail['old_content'] is None
        assert detail['new_content'] == 'fresh memory'

    @pytest.mark.asyncio
    async def test_stays_active_after_firing(self):
        """The callback must NOT self-disable (memory can change repeatedly)."""
        conversation_id = uuid4()
        callback = _make_callback(conversation_id)
        event = _make_observation_event(_PROJECT_PATH)
        processor = MemoryChangeCallbackProcessor()

        await processor(conversation_id, callback, event)

        assert callback.status is EventCallbackStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_skips_non_observation_event(self):
        conversation_id = uuid4()
        callback = _make_callback(conversation_id)
        event = MessageEvent(
            source='user',
            llm_message=Message(role='user', content=[TextContent(text='hi')]),
        )
        processor = MemoryChangeCallbackProcessor()

        result = await processor(conversation_id, callback, event)

        assert result is None

    @pytest.mark.asyncio
    async def test_skips_non_file_editor_tool(self):
        conversation_id = uuid4()
        callback = _make_callback(conversation_id)
        event = _make_observation_event(_PROJECT_PATH, tool_name='terminal')
        processor = MemoryChangeCallbackProcessor()

        result = await processor(conversation_id, callback, event)

        assert result is None

    @pytest.mark.asyncio
    async def test_skips_view_command(self):
        conversation_id = uuid4()
        callback = _make_callback(conversation_id)
        event = _make_observation_event(_PROJECT_PATH, command='view')
        processor = MemoryChangeCallbackProcessor()

        result = await processor(conversation_id, callback, event)

        assert result is None

    @pytest.mark.asyncio
    async def test_skips_error_observation(self):
        conversation_id = uuid4()
        callback = _make_callback(conversation_id)
        event = _make_observation_event(_PROJECT_PATH, is_error=True)
        processor = MemoryChangeCallbackProcessor()

        result = await processor(conversation_id, callback, event)

        assert result is None

    @pytest.mark.asyncio
    async def test_skips_non_memory_path(self):
        conversation_id = uuid4()
        callback = _make_callback(conversation_id)
        event = _make_observation_event('/workspace/src/main.py')
        processor = MemoryChangeCallbackProcessor()

        result = await processor(conversation_id, callback, event)

        assert result is None

    @pytest.mark.asyncio
    async def test_skips_none_path(self):
        conversation_id = uuid4()
        callback = _make_callback(conversation_id)
        observation = FileEditorObservation(
            command='str_replace',
            path=None,
            old_content='old',
            new_content='new',
            prev_exist=True,
            is_error=False,
        )
        event = ObservationEvent(
            observation=observation,
            tool_name='file_editor',
            action_id='action-1',
            tool_call_id='tc-1',
        )
        processor = MemoryChangeCallbackProcessor()

        result = await processor(conversation_id, callback, event)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_event_kind_is_observation_event(self):
        assert MemoryChangeCallbackProcessor.get_event_kind() == 'ObservationEvent'
