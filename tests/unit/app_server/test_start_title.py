import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest

from openhands.agent_server.utils import utc_now
from openhands.app_server.app_conversation.start_title import (
    generate_start_title,
    pending_start_title,
)
from openhands.sdk.llm import LLM


@pytest.mark.asyncio
async def test_title_uses_first_message_and_keeps_fallback_on_failure():
    pending = pending_start_title('Fix the failing checkout test')
    llm = LLM(model='test', api_key='test')
    with patch(
        'openhands.app_server.app_conversation.start_title.generate_title_with_llm',
        return_value='Repair checkout',
    ) as generate:
        result = await generate_start_title(
            pending, 'Fix the failing checkout test', llm
        )
    assert result.status == 'generated'
    assert result.title == 'Repair checkout'
    assert generate.call_args.args[0] == 'Fix the failing checkout test'
    assert pending.status == 'pending'

    with patch(
        'openhands.app_server.app_conversation.start_title.generate_title_with_llm',
        return_value=None,
    ):
        result = await generate_start_title(
            pending, 'Fix the failing checkout test', llm
        )
    assert result.status == 'fallback'
    assert result.title == 'Fix the failing checkout test'


@pytest.mark.asyncio
async def test_deadline_discards_late_title_without_waiting_for_provider():
    pending = pending_start_title('Keep the initial message')
    pending.deadline_at = utc_now() + timedelta(milliseconds=20)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_call(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with patch('asyncio.to_thread', side_effect=slow_call):
        result = await generate_start_title(pending, pending.title, LLM(model='test'))
    assert started.is_set()
    assert cancelled.is_set()
    assert result.status == 'fallback'
    assert result.title == pending.title


@pytest.mark.asyncio
async def test_missing_model_settles_without_an_ai_request():
    pending = pending_start_title('A useful first message')
    with patch('asyncio.to_thread') as generate:
        result = await generate_start_title(pending, pending.title, None)
    generate.assert_not_called()
    assert result.status == 'fallback'
