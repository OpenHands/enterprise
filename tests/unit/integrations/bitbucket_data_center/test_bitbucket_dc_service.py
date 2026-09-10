"""Unit tests for SaaSBitbucketDCService."""

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from integrations.bitbucket_data_center.bitbucket_dc_service import (
    SaaSBitbucketDCService,
)
from openhands.app_server.integrations.service_types import ProviderType, RequestMethod


@pytest.fixture
def service():
    return SaaSBitbucketDCService()


@pytest.fixture
def service_with_external_auth_token():
    return SaaSBitbucketDCService(external_auth_token=SecretStr('test_keycloak_token'))


@pytest.fixture
def service_with_external_auth_id():
    return SaaSBitbucketDCService(external_auth_id='test_user_id')


@pytest.fixture
def service_with_user_id():
    return SaaSBitbucketDCService(user_id='test_user_id')


def test_refresh_flag_is_true():
    assert SaaSBitbucketDCService().refresh is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'context',
    [
        {'external_auth_token': SecretStr('legacy-broker-token')},
        {'external_auth_id': 'openhands-user'},
        {'user_id': 'provider-account'},
    ],
)
async def test_get_latest_token_uses_credential_service(context):
    service = SaaSBitbucketDCService(**context)
    with patch.object(
        service.provider_credentials,
        'token_for_service',
        AsyncMock(return_value=SecretStr('fresh-token')),
    ) as get_token:
        token = await service.get_latest_token()
    assert token.get_secret_value() == 'fresh-token'
    assert service.token.get_secret_value() == 'fresh-token'
    get_token.assert_awaited_once_with(
        ProviderType.BITBUCKET_DATA_CENTER,
        user_id=context.get('external_auth_id'),
        account_id=context.get('user_id'),
        access_token=context.get('external_auth_token'),
        host=None,
    )


@pytest.mark.asyncio
async def test_get_latest_token_without_context_returns_none(service):
    assert await service.get_latest_token() is None


@pytest.mark.asyncio
async def test_add_comment_reaction_uses_comment_likes_put():
    """BBDC reactions live in the comment-likes plugin, not core /rest/api/1.0.

    Regression guard: the call must be a PUT to
    ``/rest/comment-likes/latest/.../comments/{id}/reactions/{emoticon}`` with
    the bare emoticon name (``eyes``). The original endpoint
    (POST /rest/api/1.0/.../reactions with ``:eyes:``) returns 404/400.
    """
    service = SaaSBitbucketDCService()
    service.BASE_URL = 'https://bb.example.com/rest/api/1.0'

    with patch.object(service, '_make_request', new_callable=AsyncMock) as mock_request:
        await service.add_comment_reaction(
            owner='PROJ',
            repo_slug='myrepo',
            pr_id=7,
            comment_id=99,
            emoticon='eyes',
        )

    mock_request.assert_awaited_once()
    args, kwargs = mock_request.await_args
    assert args[0] == (
        'https://bb.example.com/rest/comment-likes/latest/projects/PROJ'
        '/repos/myrepo/pull-requests/7/comments/99/reactions/eyes'
    )
    assert kwargs['method'] == RequestMethod.PUT
