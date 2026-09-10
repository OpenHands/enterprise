import time
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from openhands.app_server.integrations.provider import ProviderToken, ProviderType
from openhands.app_server.integrations.service_types import AuthenticationError
from openhands.app_server.integrations.service_types import User as ProviderUser
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth.contracts import (
    AuthenticationUnavailable,
    InvalidCredentials,
    ProviderReconnectRequired,
)
from server.auth.mode import AuthMode
from server.auth.provider_credentials import (
    ProviderCredentialService,
    normalize_provider_host,
)
from storage.auth_tokens import AuthTokens
from storage.saas_secrets_store import SaasSecretsStore
from storage.user import User


@pytest.fixture
async def credentials(async_session_maker, monkeypatch, create_org):
    monkeypatch.setattr(
        'server.auth.provider_credentials.a_session_maker', async_session_maker
    )
    monkeypatch.setattr(
        'storage.saas_secrets_store.a_session_maker', async_session_maker
    )
    monkeypatch.setattr('server.auth.mode._auth_mode', AuthMode.LOCAL)
    monkeypatch.setattr('storage.user_store.a_session_maker', async_session_maker)
    user_id = uuid4()
    create_org(id=user_id)
    async with async_session_maker() as session, session.begin():
        session.add(
            User(
                id=user_id,
                current_org_id=user_id,
                email='user@example.com',
                email_verified=True,
            )
        )
    jwt_service = JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('secret'), active=True)]
    )
    service = ProviderCredentialService(jwt_service)
    return service, user_id, async_session_maker


@pytest.fixture
def provider_user():
    return ProviderUser(id='123', login='actor', avatar_url='')


@pytest.mark.asyncio
async def test_manual_persistence_identity_host_and_disconnect(
    credentials, provider_user
):
    service, user_id, sessions = credentials
    with patch.object(
        service, '_validate_token', AsyncMock(return_value=provider_user)
    ) as validate:
        await service.connect(
            user_id, ProviderType.GITHUB, SecretStr('pat'), 'https://GitHub.COM:443/'
        )
    validate.assert_awaited_once_with(
        ProviderType.GITHUB, SecretStr('pat'), 'github.com'
    )
    async with sessions() as session:
        row = await session.scalar(select(AuthTokens))
        assert row.access_token != 'pat'
        assert row.credential_kind == 'manual'
        assert row.refresh_token is None
        assert row.access_token_expires_at is None
        assert row.provider_account_id == '123'
        assert row.provider_host == 'github.com'
    with patch(
        'server.auth.keycloak.manager.get_keycloak_admin',
        side_effect=AssertionError('Keycloak called'),
    ):
        assert await service.resolve_user(ProviderType.GITHUB, '123') == user_id
        assert await service.resolve_user(ProviderType.GITHUB, 'unlinked') is None
        assert (
            await service.get_token(user_id, ProviderType.GITHUB)
        ).token.get_secret_value() == 'pat'
        assert ProviderType.GITHUB in await service.list_tokens(user_id)
        await service.disconnect(user_id, ProviderType.GITHUB)
    assert not await service.stored_tokens(user_id)
    assert await service.resolve_user(ProviderType.GITHUB, '123') is None


@pytest.mark.asyncio
async def test_account_cannot_be_linked_to_two_users(
    credentials, provider_user, create_org
):
    service, user_id, sessions = credentials
    other_id = uuid4()
    create_org(id=other_id)
    async with sessions() as session, session.begin():
        session.add(User(id=other_id, current_org_id=other_id))
    with patch.object(
        service, '_validate_token', AsyncMock(return_value=provider_user)
    ):
        await service.connect(user_id, ProviderType.GITHUB, SecretStr('pat'))
        with pytest.raises(ProviderReconnectRequired, match='already connected'):
            await service.connect(
                other_id, ProviderType.GITHUB, SecretStr('another-pat')
            )
    assert await service.resolve_user(ProviderType.GITHUB, '123') == user_id


@pytest.mark.asyncio
async def test_disabled_accounts_cannot_read_or_resolve_credentials(
    credentials, provider_user
):
    service, user_id, sessions = credentials
    with patch.object(
        service, '_validate_token', AsyncMock(return_value=provider_user)
    ):
        await service.connect(user_id, ProviderType.GITHUB, SecretStr('pat'))
    async with sessions() as session, session.begin():
        user = await session.get(User, user_id)
        user.is_disabled = True
    with pytest.raises(InvalidCredentials):
        await service.get_token(user_id, ProviderType.GITHUB)
    with pytest.raises(InvalidCredentials):
        await service.resolve_user(ProviderType.GITHUB, '123')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status,error,expected',
    [
        (503, None, AuthenticationUnavailable),
        (429, None, AuthenticationUnavailable),
        (400, 'invalid_grant', ProviderReconnectRequired),
        (401, None, ProviderReconnectRequired),
    ],
)
async def test_refresh_failure_preserves_encrypted_tokens(
    credentials, status, error, expected
):
    service, user_id, sessions = credentials
    encrypted = service._jwt_svc.encrypt_value('expired')
    async with sessions() as session, session.begin():
        session.add(
            AuthTokens(
                keycloak_user_id=str(user_id),
                identity_provider='github',
                access_token=encrypted,
                refresh_token=service._jwt_svc.encrypt_value('refresh'),
                access_token_expires_at=int(time.time()) - 1,
                refresh_token_expires_at=0,
            )
        )
    response = httpx.Response(
        status,
        request=httpx.Request('POST', 'https://github.com/oauth'),
        json={'error': error},
    )
    exc = httpx.HTTPStatusError(
        'provider failure', request=response.request, response=response
    )
    with patch.object(service._refresher, '_refresh_token', AsyncMock(side_effect=exc)):
        with pytest.raises(expected):
            await service.get_token(user_id, ProviderType.GITHUB)
    async with sessions() as session:
        row = await session.scalar(select(AuthTokens))
        assert row.access_token == encrypted
        assert row.refresh_token is not None


@pytest.mark.asyncio
async def test_refresh_success_rotates_tokens_without_keycloak(credentials):
    service, user_id, sessions = credentials
    async with sessions() as session, session.begin():
        session.add(
            AuthTokens(
                keycloak_user_id=str(user_id),
                identity_provider='github',
                access_token=service._jwt_svc.encrypt_value('expired'),
                refresh_token=service._jwt_svc.encrypt_value('refresh'),
                access_token_expires_at=int(time.time()) - 1,
                refresh_token_expires_at=0,
            )
        )
    refreshed = {
        'access_token': 'new',
        'refresh_token': 'rotated',
        'access_token_expires_at': int(time.time()) + 20000,
        'refresh_token_expires_at': 0,
    }
    with (
        patch.object(
            service._refresher, '_refresh_token', AsyncMock(return_value=refreshed)
        ),
        patch(
            'server.auth.keycloak.manager.get_keycloak_admin',
            side_effect=AssertionError('Keycloak called'),
        ),
    ):
        assert (
            await service.get_token(user_id, ProviderType.GITHUB)
        ).token.get_secret_value() == 'new'
    async with sessions() as session:
        row = await session.scalar(select(AuthTokens))
        assert service._jwt_svc.decrypt_value(row.refresh_token) == 'rotated'


@pytest.mark.asyncio
async def test_enterprise_secrets_connection_roundtrip(credentials, provider_user):
    service, user_id, sessions = credentials
    store = SaasSecretsStore(str(user_id), service._jwt_svc)
    async with sessions() as session:
        user = await session.get(User, user_id)
    with (
        patch(
            'storage.saas_secrets_store.UserStore.get_user_by_id',
            AsyncMock(return_value=user),
        ),
        patch.object(
            ProviderCredentialService,
            '_validate_token',
            AsyncMock(return_value=provider_user),
        ),
    ):
        await store.store_provider_tokens(
            {
                ProviderType.GITHUB: ProviderToken(
                    token=SecretStr('pat'), user_id='forged-id'
                ),
                **{
                    provider: ProviderToken()
                    for provider in ProviderType
                    if provider
                    not in (ProviderType.GITHUB, ProviderType.ENTERPRISE_SSO)
                },
            }
        )
        loaded = await store.load()
        assert loaded.provider_tokens[ProviderType.GITHUB].user_id == '123'
        assert (
            loaded.provider_tokens[ProviderType.GITHUB].token.get_secret_value()
            == 'pat'
        )
        # An org-scoped custom-secret write cannot overwrite account credentials.
        await store.store(loaded.model_copy(update={'provider_tokens': {}}))
        assert ProviderType.GITHUB in (await store.load()).provider_tokens
        await store.delete_provider_tokens()
        assert not (await store.load()).provider_tokens


@pytest.mark.asyncio
async def test_reconnect_does_not_refresh_rejected_existing_oauth(
    credentials, provider_user
):
    service, user_id, sessions = credentials
    async with sessions() as session, session.begin():
        session.add(
            AuthTokens(
                keycloak_user_id=str(user_id),
                identity_provider='github',
                access_token=service._jwt_svc.encrypt_value('old'),
                refresh_token=None,
                access_token_expires_at=1,
            )
        )
    store = SaasSecretsStore(str(user_id), service._jwt_svc)
    with patch.object(
        ProviderCredentialService,
        '_validate_token',
        AsyncMock(return_value=provider_user),
    ):
        await store.store_provider_tokens(
            {ProviderType.GITHUB: ProviderToken(token=SecretStr('replacement'))}
        )
    assert (
        await service.get_token(user_id, ProviderType.GITHUB)
    ).token.get_secret_value() == 'replacement'


@pytest.mark.asyncio
async def test_wrapped_provider_outage_is_not_reconnect(credentials):
    service, _, _ = credentials
    response = httpx.Response(
        503, request=httpx.Request('GET', 'https://api.github.com/user')
    )
    wrapped = AuthenticationError('upstream failed')
    wrapped.__cause__ = httpx.HTTPStatusError(
        'outage', request=response.request, response=response
    )
    with patch(
        'server.auth.provider_credentials.GitHubService.get_user',
        AsyncMock(side_effect=wrapped),
    ):
        with pytest.raises(AuthenticationUnavailable):
            await service._validate_token(
                ProviderType.GITHUB, SecretStr('pat'), 'github.com'
            )


@pytest.mark.parametrize(
    'host',
    [
        'evil.example',
        'github.com@evil.example',
        'http://github.com',
        'https://github.com/path',
        'https://github.com?host=evil',
    ],
)
def test_token_destination_is_only_configured_provider(host):
    with pytest.raises(ValueError):
        normalize_provider_host(ProviderType.GITHUB, host)


def test_enterprise_host_normalization(monkeypatch):
    monkeypatch.setattr(
        'server.auth.provider_credentials.GITLAB_HOST', 'gitlab.example.com:8443'
    )
    assert (
        normalize_provider_host(ProviderType.GITLAB, 'https://GitLab.Example.com:8443/')
        == 'gitlab.example.com:8443'
    )
    assert (
        normalize_provider_host(
            ProviderType.AZURE_DEVOPS, 'https://dev.azure.com/Contoso'
        )
        == 'contoso'
    )


@pytest.mark.asyncio
async def test_background_service_uses_verified_provider_mapping_without_offline_tokens(
    credentials, provider_user
):
    from integrations.github.github_service import SaaSGitHubService

    credentials_service, user_id, _ = credentials
    with patch.object(
        credentials_service, '_validate_token', AsyncMock(return_value=provider_user)
    ):
        await credentials_service.connect(
            user_id, ProviderType.GITHUB, SecretStr('pat')
        )
    service = SaaSGitHubService(user_id='123')
    service.provider_credentials = credentials_service
    with patch(
        'server.auth.token_manager.TokenManager.load_offline_token',
        side_effect=AssertionError('offline lookup'),
    ):
        assert (await service.get_latest_token()).get_secret_value() == 'pat'


@pytest.mark.asyncio
async def test_local_legacy_email_lookup_never_calls_keycloak(credentials):
    from server.auth.provider_compatibility import resolve_broker_email

    with patch(
        'server.auth.keycloak.manager.get_keycloak_admin',
        side_effect=AssertionError('Keycloak called'),
    ):
        assert await resolve_broker_email('same-email@example.com') is None


@pytest.mark.asyncio
async def test_local_background_context_has_no_browser_refresh_token(credentials):
    from server.auth.saas_user_auth import SaasUserAuth

    _, user_id, sessions = credentials
    async with sessions() as session:
        user = await session.get(User, user_id)
    with (
        patch(
            'server.auth.saas_user_auth.UserStore.get_user_by_id',
            AsyncMock(return_value=user),
        ),
        patch(
            'server.auth.token_manager.TokenManager.load_offline_token',
            side_effect=AssertionError('offline lookup'),
        ),
    ):
        context = await SaasUserAuth.for_background(str(user_id))
        assert context.refresh_token is None
        assert context.principal.authentication_method == 'background'
        assert await context.get_access_token() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [204, 404, 503])
async def test_keycloak_disconnect_preserves_compatibility_and_outage_retention(
    credentials, provider_user, monkeypatch, status
):
    from keycloak.exceptions import KeycloakDeleteError

    service, user_id, _ = credentials
    with patch.object(
        service, '_validate_token', AsyncMock(return_value=provider_user)
    ):
        await service.connect(user_id, ProviderType.GITHUB, SecretStr('pat'))
    monkeypatch.setattr('server.auth.mode._auth_mode', AuthMode.KEYCLOAK)
    admin = AsyncMock()
    if status != 204:
        admin.a_delete_user_social_login.side_effect = KeycloakDeleteError(
            error_message='provider response', response_code=status
        )
    with patch('server.auth.keycloak.manager.get_keycloak_admin', return_value=admin):
        if status == 503:
            with pytest.raises(AuthenticationUnavailable):
                await service.disconnect(user_id, ProviderType.GITHUB)
        else:
            await service.disconnect(user_id, ProviderType.GITHUB)
    admin.a_delete_user_social_login.assert_awaited_once_with(str(user_id), 'github')
    assert bool(await service.stored_tokens(user_id)) == (status == 503)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status,expected',
    [(401, ProviderReconnectRequired), (503, AuthenticationUnavailable)],
)
@pytest.mark.parametrize('method', ['_make_request', 'execute_graphql_query'])
async def test_provider_requests_preserve_browser_auth_on_failure(
    status, expected, method
):
    from integrations.github.github_service import SaaSGitHubService

    service = SaaSGitHubService(token=SecretStr('stored-token'))
    response = httpx.Response(
        status, request=httpx.Request('GET', 'https://api.github.com/user')
    )
    error = AuthenticationError('provider request failed')
    error.__cause__ = httpx.HTTPStatusError(
        'provider response', request=response.request, response=response
    )
    with patch(
        f'openhands.app_server.integrations.github.github_service.GitHubService.{method}',
        AsyncMock(side_effect=error),
    ):
        with pytest.raises(expected):
            await getattr(service, method)('request')
    assert service.token.get_secret_value() == 'stored-token'


@pytest.mark.asyncio
async def test_bitbucket_dc_identity_proof_rejects_fuzzy_user_match():
    from openhands.app_server.integrations.bitbucket_data_center.bitbucket_dc_service import (
        BitbucketDCService,
    )

    service = BitbucketDCService(
        token=SecretStr('pat'), base_domain='bitbucket.example.com'
    )
    with patch.object(
        service,
        '_make_request',
        AsyncMock(
            side_effect=[
                ('alice', {}),
                (
                    {'values': [{'id': 99, 'name': 'alice-two', 'slug': 'alice-two'}]},
                    {},
                ),
            ]
        ),
    ):
        with pytest.raises(AuthenticationError, match='uniquely'):
            await ProviderCredentialService._validate_bitbucket_dc(service)


@pytest.mark.asyncio
@pytest.mark.parametrize('match', ['exact', 'substring', 'ambiguous'])
async def test_keycloak_actor_fallback_requires_exact_unique_provider_identity(
    credentials, monkeypatch, match
):
    service, user_id, _ = credentials
    monkeypatch.setattr('server.auth.mode._auth_mode', AuthMode.KEYCLOAK)
    record = {
        'id': str(user_id),
        'attributes': {'github_id': ['123' if match != 'substring' else '1234']},
    }
    records = [record, record] if match == 'ambiguous' else [record]
    admin = AsyncMock()
    admin.a_get_users.return_value = records
    with patch('server.auth.keycloak.manager.get_keycloak_admin', return_value=admin):
        assert await service.resolve_user(ProviderType.GITHUB, '123') == (
            user_id if match == 'exact' else None
        )
