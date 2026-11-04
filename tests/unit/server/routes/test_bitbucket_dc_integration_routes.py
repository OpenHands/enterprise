from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openhands.app_server.integrations.service_types import ProviderType
from server.routes.integration import bitbucket_dc
from server.services.automation_event_service import UnroutedEvent


class _FakeWebhookStore:
    def __init__(self, webhooks=None):
        self.webhooks = webhooks or {}

    async def get_webhooks_by_repos(self, repo_identities):
        return {
            key: hook for key, hook in self.webhooks.items() if key in repo_identities
        }


@pytest.fixture
def repositories(monkeypatch):
    class FakeBitbucketDCService:
        def __init__(self, **kwargs):
            pass

        async def get_all_repositories(self, sort, app_mode):
            return [
                SimpleNamespace(full_name='PROJ/myrepo'),
                SimpleNamespace(full_name='PROJ/second'),
                SimpleNamespace(full_name='OPS/platform'),
            ]

    monkeypatch.setattr(bitbucket_dc, 'SaaSBitbucketDCService', FakeBitbucketDCService)
    monkeypatch.setattr(bitbucket_dc, 'webhook_store', _FakeWebhookStore())
    monkeypatch.setattr(bitbucket_dc, 'AUTOMATION_EVENT_FORWARDING_ENABLED', True)


@pytest.mark.asyncio
async def test_resources_report_dropped_events_for_unclaimed_projects(
    repositories, monkeypatch
):
    """Every repository in a project with a dropped event carries the record."""
    dropped = UnroutedEvent(
        provider='bitbucket_data_center',
        git_org='PROJ',
        event_key='pr:opened',
        repository='PROJ/myrepo',
        received_at='2026-11-04T10:00:00+00:00',
        count=2,
    )
    get_unrouted_events = AsyncMock(return_value={'PROJ': dropped})
    monkeypatch.setattr(bitbucket_dc, 'get_unrouted_events', get_unrouted_events)

    response = await bitbucket_dc.get_bitbucket_dc_resources(user_id='user-id')

    get_unrouted_events.assert_awaited_once_with(
        ProviderType.BITBUCKET_DATA_CENTER, ['PROJ', 'PROJ', 'OPS']
    )
    by_name = {resource.full_name: resource for resource in response.resources}
    assert by_name['PROJ/myrepo'].unrouted_event == dropped
    assert by_name['PROJ/second'].unrouted_event == dropped
    assert by_name['OPS/platform'].unrouted_event is None
    assert by_name['PROJ/myrepo'].webhook_enrolled is False


@pytest.mark.asyncio
async def test_resources_skip_dropped_event_lookup_without_forwarding(
    repositories, monkeypatch
):
    """Without automation forwarding nothing is dropped, so Redis is not read."""
    monkeypatch.setattr(bitbucket_dc, 'AUTOMATION_EVENT_FORWARDING_ENABLED', False)
    get_unrouted_events = AsyncMock()
    monkeypatch.setattr(bitbucket_dc, 'get_unrouted_events', get_unrouted_events)

    response = await bitbucket_dc.get_bitbucket_dc_resources(user_id='user-id')

    get_unrouted_events.assert_not_awaited()
    assert all(resource.unrouted_event is None for resource in response.resources)
