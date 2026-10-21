"""Runs against the test database with ``ENABLE_OAUTH_V2_LOGIN`` on. Only the
git providers' token endpoints and, where a test says so, Keycloak are faked.
``test_background_git_tokens_flag_off.py`` covers the flag being off."""

from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from integrations.azure_devops.azure_devops_manager import AzureDevOpsManager
from integrations.azure_devops.azure_devops_service import SaaSAzureDevOpsService
from integrations.bitbucket.bitbucket_manager import BitbucketManager
from integrations.bitbucket.bitbucket_service import SaaSBitBucketService
from integrations.bitbucket_data_center.bitbucket_dc_manager import (
    BitbucketDCManager,
)
from integrations.bitbucket_data_center.bitbucket_dc_service import (
    SaaSBitbucketDCService,
)
from integrations.github.github_service import SaaSGitHubService
from integrations.gitlab.gitlab_service import SaaSGitLabService
from integrations.types import UserData
from openhands.app_server.integrations.provider import ProviderHandler, ProviderToken
from openhands.app_server.integrations.service_types import ProviderType
from server.auth.saas_user_auth import get_user_auth_from_keycloak_id
from server.auth.token_manager import TokenManager
from storage.auth_token_store import AuthTokenStore
from storage.oauth_token_store import OAuthTokenStore

pytestmark = pytest.mark.usefixtures('oauth_v2_login_on')


def _token_endpoint(status_code: int = 200, body: dict | None = None) -> AsyncMock:
    response = MagicMock(
        status_code=status_code,
        headers={'content-type': 'application/json'},
        text='error',
    )
    response.json.return_value = body or {}
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__.return_value = client
    return client


@pytest.mark.asyncio
async def test_the_token_comes_from_oauth_tokens_without_keycloak(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat-linked')

    token = await TokenManager().get_idp_token_for_user(
        str(user.id), ProviderType.GITLAB
    )

    assert token == 'glpat-linked'


@pytest.mark.asyncio
async def test_an_expired_token_is_refreshed_at_the_provider(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(
        user.id,
        gitlab,
        'glpat-expired',
        access_token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    client = _token_endpoint(
        body={'access_token': 'glpat-fresh', 'refresh_token': 'r2', 'expires_in': 7200}
    )

    with patch('server.auth.oauth_v2_refresh.httpx.AsyncClient', return_value=client):
        token = await TokenManager().get_idp_token_for_user(
            str(user.id), ProviderType.GITLAB
        )

    assert token == 'glpat-fresh'
    assert client.post.await_args.args[0] == 'https://gitlab.example.com/oauth/token'
    stored = await OAuthTokenStore(
        user_id=user.id, oauth_provider_id=gitlab.id
    ).get_valid_access_token()
    assert stored == 'glpat-fresh'


@pytest.mark.asyncio
async def test_the_provider_the_user_linked_wins_over_another_of_the_same_type(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    await create_git_provider(ProviderType.GITLAB)
    linked = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(user.id, linked, 'glpat-second-provider')

    token = await TokenManager().get_idp_token_for_user(
        str(user.id), ProviderType.GITLAB
    )

    assert token == 'glpat-second-provider'


@pytest.mark.asyncio
async def test_another_users_link_to_a_newer_provider_does_not_hide_the_token(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    mine = await create_git_provider(ProviderType.GITLAB)
    theirs = await create_git_provider(ProviderType.GITLAB)
    me, someone_else = create_user(), create_user()
    await store_oauth_token(me.id, mine, 'glpat-mine')
    await store_oauth_token(someone_else.id, theirs, 'glpat-theirs')

    token = await TokenManager().get_idp_token_for_user(str(me.id), ProviderType.GITLAB)

    assert token == 'glpat-mine'


@pytest.mark.asyncio
async def test_without_a_token_or_keycloak_there_is_none(
    no_keycloak, create_git_provider, create_user
):
    await create_git_provider(ProviderType.GITLAB)

    token = await TokenManager().get_idp_token_for_user(
        str(create_user().id), ProviderType.GITLAB
    )

    assert token is None


@pytest.mark.asyncio
async def test_a_token_for_one_provider_does_not_answer_for_another(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    await create_git_provider(ProviderType.GITHUB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat-linked')

    token = await TokenManager().get_idp_token_for_user(
        str(user.id), ProviderType.GITHUB
    )

    assert token is None


@pytest.mark.asyncio
async def test_a_login_provider_token_is_not_a_git_token(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    gitlab_login = await create_git_provider(ProviderType.GITLAB, is_idp=True)
    user = create_user()
    await store_oauth_token(user.id, gitlab_login, 'login-session-token')

    token = await TokenManager().get_idp_token_for_user(
        str(user.id), ProviderType.GITLAB
    )

    assert token is None


@pytest.mark.asyncio
async def test_without_an_oauth_v2_token_keycloak_is_used(
    create_git_provider, create_user
):
    await create_git_provider(ProviderType.GITLAB)
    user_id = str(create_user().id)
    token_manager = TokenManager()

    with (
        patch.object(
            token_manager, 'load_offline_token', AsyncMock(return_value='offline')
        ),
        patch.object(
            token_manager,
            'get_idp_token_from_offline_token',
            AsyncMock(return_value='glpat-from-keycloak'),
        ) as from_keycloak,
    ):
        token = await token_manager.get_idp_token_for_user(user_id, ProviderType.GITLAB)

    assert token == 'glpat-from-keycloak'
    from_keycloak.assert_awaited_once_with(
        offline_token='offline', idp=ProviderType.GITLAB
    )


@pytest.mark.asyncio
async def test_an_oauth_tokens_row_wins_over_a_keycloak_offline_token(
    create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat-linked')
    token_manager = TokenManager()

    with (
        patch.object(
            token_manager, 'load_offline_token', AsyncMock(return_value='offline')
        ),
        patch.object(
            token_manager,
            'get_idp_token_from_offline_token',
            AsyncMock(return_value='glpat-from-keycloak'),
        ) as from_keycloak,
    ):
        token = await token_manager.get_idp_token_for_user(
            str(user.id), ProviderType.GITLAB
        )

    assert token == 'glpat-linked'
    from_keycloak.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_refresh_falls_back_to_keycloak_and_keeps_the_token(
    create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(
        user.id,
        gitlab,
        'glpat-expired',
        access_token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    token_manager = TokenManager()

    with (
        patch(
            'server.auth.oauth_v2_refresh.httpx.AsyncClient',
            return_value=_token_endpoint(status_code=400),
        ),
        patch.object(
            token_manager, 'load_offline_token', AsyncMock(return_value='offline')
        ),
        patch.object(
            token_manager,
            'get_idp_token_from_offline_token',
            AsyncMock(return_value='glpat-from-keycloak'),
        ),
    ):
        token = await token_manager.get_idp_token_for_user(
            str(user.id), ProviderType.GITLAB
        )

    assert token == 'glpat-from-keycloak'
    assert (
        await OAuthTokenStore(user_id=user.id, oauth_provider_id=gitlab.id).get_raw()
        is not None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('service_cls', 'provider_type'),
    [
        (SaaSGitLabService, ProviderType.GITLAB),
        (SaaSGitHubService, ProviderType.GITHUB),
        (SaaSBitBucketService, ProviderType.BITBUCKET),
    ],
)
async def test_a_held_token_is_not_treated_as_rejected_without_a_401(
    no_keycloak,
    create_git_provider,
    store_oauth_token,
    create_user,
    service_cls,
    provider_type,
):
    """ProviderHandler.get_service passes the token in, and callers then ask
    for the latest token without any request having failed."""
    provider = await create_git_provider(provider_type)
    user = create_user()
    await store_oauth_token(user.id, provider, 'linked-token')
    handler = ProviderHandler(
        MappingProxyType(
            {provider_type: ProviderToken(token=SecretStr('linked-token'))}
        ),
        external_auth_id=str(user.id),
    )
    handler.service_class_map[provider_type] = service_cls
    service = handler.get_service(provider_type)

    token = await service.get_latest_token()

    assert token is not None
    assert token.get_secret_value() == 'linked-token'


@pytest.mark.asyncio
async def test_background_provider_tokens_include_an_oauth_v2_git_token(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    """A background job's user auth (no Keycloak auth_tokens row) still gets
    the git token the user linked through OAuth v2."""
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat-linked')
    auth = await get_user_auth_from_keycloak_id(str(user.id))

    with patch.object(auth, 'get_secrets', AsyncMock(return_value=None)):
        tokens = await auth.get_provider_tokens()

    assert tokens is not None
    assert tokens[ProviderType.GITLAB].token == SecretStr('glpat-linked')


async def _store_auth_tokens(user_id: str) -> None:
    store = await AuthTokenStore.get_instance(
        keycloak_user_id=user_id, idp=ProviderType.GITLAB
    )
    in_an_hour = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
    await store.store_tokens(
        access_token='stored',
        refresh_token='refresh',
        access_token_expires_at=in_an_hour,
        refresh_token_expires_at=0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('has_auth_tokens', [True, False])
async def test_a_failed_oauth_v2_read_leaves_provider_tokens_to_auth_tokens(
    no_keycloak, create_git_provider, store_oauth_token, create_user, has_auth_tokens
):
    """A failed oauth_tokens read must not fail the whole request through the
    Keycloak offline session; auth_tokens answers for that provider instead."""
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(
        user.id,
        gitlab,
        'glpat-revoked',
        access_token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    if has_auth_tokens:
        await _store_auth_tokens(str(user.id))
    auth = await get_user_auth_from_keycloak_id(str(user.id))

    with (
        patch(
            'server.auth.oauth_v2_refresh.httpx.AsyncClient',
            return_value=_token_endpoint(status_code=400),
        ),
        patch.object(auth, 'get_secrets', AsyncMock(return_value=None)),
        # An offline session exists, so a Keycloak fallback would be attempted;
        # no_keycloak makes that exchange fail.
        patch.object(
            TokenManager, 'load_offline_token', AsyncMock(return_value='offline')
        ),
        # The auth_tokens loop's own reader; it needs no Keycloak.
        patch.object(
            TokenManager,
            'get_idp_token_by_user_id',
            AsyncMock(return_value='glpat-from-auth-tokens'),
        ),
    ):
        tokens = await auth.get_provider_tokens()

    assert tokens is not None
    if has_auth_tokens:
        assert tokens[ProviderType.GITLAB].token == SecretStr('glpat-from-auth-tokens')
    else:
        assert ProviderType.GITLAB not in tokens


@pytest.mark.asyncio
async def test_a_token_the_provider_rejected_falls_back_to_keycloak(
    create_git_provider, store_oauth_token, create_user
):
    """A copy of a Keycloak grant dies when the Keycloak side refreshes, though
    its stored expiry is still ahead."""
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat-revoked')
    token_manager = TokenManager()

    with (
        patch.object(
            token_manager, 'load_offline_token', AsyncMock(return_value='offline')
        ),
        patch.object(
            token_manager,
            'get_idp_token_from_offline_token',
            AsyncMock(return_value='glpat-from-keycloak'),
        ),
    ):
        token = await token_manager.get_idp_token_for_user(
            str(user.id), ProviderType.GITLAB, rejected='glpat-revoked'
        )

    assert token == 'glpat-from-keycloak'
    assert (
        await OAuthTokenStore(user_id=user.id, oauth_provider_id=gitlab.id).get_raw()
        is not None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('service_cls', 'provider_type'),
    [
        (SaaSGitLabService, ProviderType.GITLAB),
        (SaaSGitHubService, ProviderType.GITHUB),
        (SaaSBitBucketService, ProviderType.BITBUCKET),
    ],
)
async def test_a_401_retries_once_with_the_keycloak_token(
    create_git_provider,
    store_oauth_token,
    create_user,
    service_cls,
    provider_type,
):
    provider = await create_git_provider(provider_type)
    user = create_user()
    await store_oauth_token(user.id, provider, 'revoked')
    service = service_cls(external_auth_id=str(user.id))
    ok = MagicMock(status_code=200, headers={'Content-Type': 'application/json'})
    ok.json.return_value = {'ok': True}
    responses = [MagicMock(status_code=401, headers={}), ok]
    sent: list[dict] = []

    async def execute_request(client, url, headers, params, method):
        sent.append(dict(headers))
        return responses[len(sent) - 1]

    with (
        patch.object(service, 'execute_request', side_effect=execute_request),
        patch.object(
            service.token_manager,
            'load_offline_token',
            AsyncMock(return_value='offline'),
        ),
        patch.object(
            service.token_manager,
            'get_idp_token_from_offline_token',
            AsyncMock(return_value='from-keycloak'),
        ),
    ):
        body, _ = await service._make_request('https://git.example.com/api/user')

    assert body == {'ok': True}
    assert len(sent) == 2
    assert sent[0] != sent[1]
    assert service.token.get_secret_value() == 'from-keycloak'


@pytest.mark.parametrize(
    'service_cls', [SaaSGitLabService, SaaSGitHubService, SaaSBitBucketService]
)
def test_only_services_acting_for_a_user_retry_a_401(service_cls):
    # A signed-in request's token comes from its Keycloak session; a retry
    # could only resend the same token.
    signed_in = service_cls(
        external_auth_token=SecretStr('keycloak-access'), external_auth_id='user'
    )

    assert service_cls(external_auth_id='user').refresh is True
    assert signed_in.refresh is False
    assert service_cls(user_id='git-user').refresh is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('service_cls', 'provider_type'),
    [
        (SaaSGitLabService, ProviderType.GITLAB),
        (SaaSGitHubService, ProviderType.GITHUB),
        (SaaSBitBucketService, ProviderType.BITBUCKET),
        (SaaSBitbucketDCService, ProviderType.BITBUCKET_DATA_CENTER),
        (SaaSAzureDevOpsService, ProviderType.AZURE_DEVOPS),
    ],
)
async def test_services_acting_for_a_user_read_oauth_tokens(
    no_keycloak,
    create_git_provider,
    store_oauth_token,
    create_user,
    service_cls,
    provider_type,
):
    provider = await create_git_provider(provider_type)
    user = create_user()
    await store_oauth_token(user.id, provider, f'{provider_type.value}-token')

    token = await service_cls(external_auth_id=str(user.id)).get_latest_token()

    assert token is not None
    assert token.get_secret_value() == f'{provider_type.value}-token'


@pytest.mark.asyncio
async def test_a_lookup_by_git_user_id_reads_oauth_tokens(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await store_oauth_token(user.id, gitlab, 'glpat-linked')
    token_manager = TokenManager()

    # Resolving the git user id to our user still goes through Keycloak.
    with patch.object(
        token_manager,
        'get_user_id_from_idp_user_id',
        AsyncMock(return_value=str(user.id)),
    ):
        token = await token_manager.get_idp_token_from_idp_user_id(
            '4242', ProviderType.GITLAB
        )

    assert token == 'glpat-linked'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('manager_cls', 'provider_type'),
    [
        (BitbucketManager, ProviderType.BITBUCKET),
        (BitbucketDCManager, ProviderType.BITBUCKET_DATA_CENTER),
        (AzureDevOpsManager, ProviderType.AZURE_DEVOPS),
    ],
)
async def test_managers_start_jobs_with_the_oauth_tokens_token(
    no_keycloak,
    create_git_provider,
    store_oauth_token,
    create_user,
    manager_cls,
    provider_type,
):
    provider = await create_git_provider(provider_type)
    user = create_user()
    await store_oauth_token(user.id, provider, f'{provider_type.value}-token')
    manager = manager_cls(TokenManager())
    # The view and conversation startup are out of scope; only the token
    # handed to the new conversation matters here.
    view = MagicMock(
        user_info=UserData(
            user_id='git-user', username='alice', keycloak_user_id=str(user.id)
        ),
        initialize_new_conversation=AsyncMock(return_value='conversation-id'),
        create_new_conversation=AsyncMock(),
    )

    with (
        patch(f'{manager_cls.__module__}.get_saas_user_auth', AsyncMock()),
        patch.object(manager, 'send_message', AsyncMock()),
    ):
        await manager.start_job(view)

    provider_tokens = view.create_new_conversation.await_args.args[1]
    assert (
        provider_tokens[provider_type].token.get_secret_value()
        == f'{provider_type.value}-token'
    )
