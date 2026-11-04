import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from server.routes.integration.slack import on_event


def _request(event: dict) -> MagicMock:
    body = json.dumps(
        {'type': 'event_callback', 'team_id': 'T1', 'event': event}
    ).encode()
    request = MagicMock()
    request.body = AsyncMock(return_value=body)
    request.headers = {'x-slack-request-timestamp': '1', 'x-slack-signature': 'v0=x'}
    return request


def _mention(**extra) -> dict:
    return {
        'type': 'app_mention',
        'text': '<@UBOT> please take a look',
        'ts': '1700000000.000100',
        'channel': 'C1',
        'user': 'U1',
        **extra,
    }


@pytest.fixture
def redis():
    with patch('server.routes.integration.slack.get_redis_client_async') as get:
        client = MagicMock()
        client.set = AsyncMock(return_value=True)
        get.return_value = client
        yield client


@pytest.fixture(autouse=True)
def enabled_with_valid_signature():
    with (
        patch('server.routes.integration.slack.SLACK_WEBHOOKS_ENABLED', True),
        patch('server.routes.integration.slack.signature_verifier') as verifier,
    ):
        verifier.is_valid.return_value = True
        yield


@pytest.mark.asyncio
async def test_typed_message_is_deduplicated_by_client_msg_id(redis):
    tasks = BackgroundTasks()

    response = await on_event(_request(_mention(client_msg_id='abc-123')), tasks)

    assert response.status_code == 200
    assert redis.set.await_args.args[0] == 'slack_msg:abc-123'
    assert len(tasks.tasks) == 1


@pytest.mark.asyncio
async def test_mention_without_client_msg_id_is_handled(redis):
    """Workflow and app-posted mentions carry no client_msg_id."""
    tasks = BackgroundTasks()

    response = await on_event(_request(_mention()), tasks)

    assert response.status_code == 200
    assert redis.set.await_args.args[0] == 'slack_msg:C1:1700000000.000100'
    assert len(tasks.tasks) == 1
    message = tasks.tasks[0].args[0]
    assert message.message['message_ts'] == '1700000000.000100'
    assert message.message['channel_id'] == 'C1'


@pytest.mark.asyncio
async def test_retried_delivery_without_client_msg_id_is_ignored(redis):
    redis.set = AsyncMock(return_value=False)
    tasks = BackgroundTasks()

    response = await on_event(_request(_mention()), tasks)

    assert response.status_code == 200
    assert tasks.tasks == []
