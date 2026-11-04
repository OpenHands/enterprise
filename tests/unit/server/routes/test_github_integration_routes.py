"""Tests for the GitHub webhook events route."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openhands.app_server.integrations.service_types import ProviderType

# The route module builds a GithubManager and a GitHubDataCollector at import,
# and both need App credentials.
with (
    patch('integrations.github.github_manager.GITHUB_APP_CLIENT_ID', 'app-id'),
    patch('integrations.github.github_manager.GITHUB_APP_PRIVATE_KEY', 'private-key'),
    patch('integrations.github.data_collector.GITHUB_APP_CLIENT_ID', 'app-id'),
    patch('integrations.github.data_collector.GITHUB_APP_PRIVATE_KEY', 'private-key'),
):
    from server.routes.integration.github import github_events

SECRET = 'github-app-secret'
DELIVERY_ID = '72d3162e-cc78-11e3-81ab-4c9367dc0958'


def _request_with_body(payload: dict) -> MagicMock:
    body = json.dumps(payload).encode()
    request = MagicMock()
    request.body = AsyncMock(return_value=body)
    request.json = AsyncMock(return_value=payload)
    return request, body


@pytest.mark.asyncio
@patch('server.routes.integration.github.automation_event_service')
@patch('server.routes.integration.github.github_manager')
@patch('server.routes.integration.github.GITHUB_APP_WEBHOOK_SECRET', SECRET)
@patch('server.routes.integration.github.GITHUB_WEBHOOKS_ENABLED', True)
@patch('server.routes.integration.github.AUTOMATION_EVENT_FORWARDING_ENABLED', True)
async def test_github_events_forward_with_the_delivery_id(
    mock_manager, mock_automation_service
):
    """GitHub's X-GitHub-Delivery reaches the automation service, which drops
    a redelivery of the same id instead of starting a second run."""
    mock_manager.receive_message = AsyncMock()
    payload = {
        'action': 'opened',
        'installation': {'id': 99999},
        'repository': {'owner': {'login': 'test-org', 'type': 'Organization'}},
    }
    request, body = _request_with_body(payload)
    signature = 'sha256=' + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    background_tasks = MagicMock()

    response = await github_events(
        request=request,
        background_tasks=background_tasks,
        x_hub_signature_256=signature,
        x_github_event='pull_request',
        x_github_delivery=DELIVERY_ID,
    )

    assert response.status_code == 200
    background_tasks.add_task.assert_called_once_with(
        mock_automation_service.forward_event,
        provider=ProviderType.GITHUB,
        payload=payload,
        installation_id=99999,
        event_type='pull_request',
        delivery_id=DELIVERY_ID,
    )
    mock_manager.receive_message.assert_awaited_once()
