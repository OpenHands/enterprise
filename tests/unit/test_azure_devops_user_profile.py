from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr

from integrations.azure_devops.azure_devops_service import SaaSAzureDevOpsService
from openhands.app_server.integrations.azure_devops.azure_devops_service import (
    AzureDevOpsService,
)
from openhands.app_server.integrations.service_types import (
    AuthenticationError,
    UnknownException,
)


@pytest.mark.asyncio
@pytest.mark.parametrize('organization', [None, ''])
@pytest.mark.parametrize('service_class', [AzureDevOpsService, SaaSAzureDevOpsService])
async def test_get_user_without_organization_uses_authenticated_profile(
    organization, service_class, monkeypatch
):
    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_service.AZURE_DEVOPS_ORGANIZATION', ''
    )
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.host != 'app.vssps.visualstudio.com':
            return httpx.Response(404)
        assert request.url.path == '/_apis/profile/profiles/me'
        assert request.url.params['api-version'] == '7.1'
        return httpx.Response(
            200,
            json={
                'id': 'profile-id',
                'displayName': 'Test User',
                'emailAddress': 'test@example.test',
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    service = service_class(token=SecretStr('test-pat'), base_domain=organization)
    with patch('httpx.AsyncClient', return_value=client):
        user = await service.get_user()

    assert user.id == 'profile-id'
    assert user.login == user.name == 'Test User'
    assert user.email == 'test@example.test'
    assert len(requests) == 1
    assert service.organization == ''


@pytest.mark.asyncio
async def test_get_user_with_organization_preserves_connection_identity():
    def respond(request):
        assert request.url.host == 'dev.azure.com'
        assert request.url.path == '/test-org/_apis/connectionData'
        return httpx.Response(
            200,
            json={
                'authenticatedUser': {
                    'id': 'connection-id',
                    'providerDisplayName': 'Connection User',
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    service = AzureDevOpsService(token=SecretStr('test-pat'), base_domain='test-org')
    with patch('httpx.AsyncClient', return_value=client):
        user = await service.get_user()

    assert user.id == 'connection-id'
    assert user.login == user.name == 'Connection User'
    assert user.email == ''


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('status', 'error'), [(401, AuthenticationError), (503, UnknownException)]
)
async def test_profile_failures_keep_provider_error_semantics(status, error):
    def respond(request):
        assert request.url.host == 'app.vssps.visualstudio.com'
        return httpx.Response(status)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    service = AzureDevOpsService(token=SecretStr('test-pat'))
    with patch('httpx.AsyncClient', return_value=client), pytest.raises(error):
        await service.get_user()
