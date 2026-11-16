"""Real-DB tests for ``SaasUserAuth.get_named_oauth_secrets`` and
``SaasUserAuth.get_named_oauth_secret_value``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import SecretStr

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth.saas_user_auth import SaasUserAuth
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import OAuthProviderDetails
from storage.auth_provider_store import AuthProviderStore
from storage.oauth_token_store import OAuthTokenStore


@pytest.fixture
def patched_db(async_session_maker, monkeypatch):
    monkeypatch.setenv('ENABLE_OAUTH_V2_LOGIN', 'true')
    jwt_svc = JwtService(keys=[EncryptionKey(key=SecretStr('test-secret-key'))])
    with (
        patch('storage.auth_provider_store.a_session_maker', async_session_maker),
        patch('storage.oauth_token_store.a_session_maker', async_session_maker),
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
    ):
        yield


async def _provider(
    provider_type: ProviderType,
    *,
    is_idp: bool = False,
    named_secret: str | None = None,
    title: str = 'Provider',
) -> AuthProvider:
    return await AuthProviderStore().create(
        AuthProvider(
            title=title,
            is_idp=is_idp,
            enabled=True,
            details=OAuthProviderDetails(
                provider_type=provider_type,
                client_id='cid',
                token_url='https://example.com/token',
                named_secret=named_secret,
            ),
        )
    )


async def _link(user_id: UUID, provider: AuthProvider, access_token: str) -> None:
    await OAuthTokenStore(user_id=user_id, oauth_provider_id=provider.id).store_tokens(
        access_token=access_token,
        refresh_token='refresh',
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        refresh_token_expires_at=None,
    )


def _auth(user_id: UUID) -> SaasUserAuth:
    return SaasUserAuth(refresh_token=SecretStr('unused'), user_id=str(user_id))


@pytest.mark.asyncio
async def test_none_when_oauth_v2_login_is_disabled(
    patched_db, create_user, monkeypatch
):
    monkeypatch.setenv('ENABLE_OAUTH_V2_LOGIN', 'false')

    assert await _auth(create_user().id).get_named_oauth_secrets() is None


@pytest.mark.asyncio
async def test_lists_named_secrets_for_linked_providers(patched_db, create_user):
    user_id = create_user().id
    github_login = await _provider(
        ProviderType.GITHUB, is_idp=True, named_secret='GITHUB_TOKEN'
    )
    github_git = await _provider(ProviderType.GITHUB, named_secret='GITHUB_TOKEN')
    okta = await _provider(
        ProviderType.ENTERPRISE_SSO,
        is_idp=True,
        named_secret='OKTA_TOKEN',
        title='Login with Okta',
    )
    unnamed_gitlab = await _provider(ProviderType.GITLAB)
    await _provider(ProviderType.BITBUCKET, named_secret='BITBUCKET_TOKEN')
    for provider in (github_login, github_git, okta, unnamed_gitlab):
        await _link(user_id, provider, f'token-{provider.id}')

    result = await _auth(user_id).get_named_oauth_secrets()

    assert result is not None
    by_name = {secret.secret_name: secret for secret in result.secrets}
    assert set(by_name) == {'GITHUB_TOKEN', 'OKTA_TOKEN'}
    # A git provider wins a shared name over a login IDP.
    assert by_name['GITHUB_TOKEN'].oauth_provider_id == github_git.id
    assert by_name['OKTA_TOKEN'].description == 'Login with Okta OAuth access token'
    # Only git provider types are replaced, whether or not the row is a login
    # IDP; ``enterprise_sso`` never supplied ``<PROVIDER_TYPE>_TOKEN``.
    assert result.linked_provider_types == {ProviderType.GITHUB, ProviderType.GITLAB}


@pytest.mark.asyncio
async def test_a_git_provider_login_idp_counts_as_a_linked_provider_type(
    patched_db, create_user
):
    user_id = create_user().id
    github_login = await _provider(ProviderType.GITHUB, is_idp=True)
    await _link(user_id, github_login, 'github-access')

    result = await _auth(user_id).get_named_oauth_secrets()

    assert result is not None
    assert result.linked_provider_types == {ProviderType.GITHUB}


@pytest.mark.asyncio
async def test_secret_value_is_the_users_access_token(patched_db, create_user):
    user_id = create_user().id
    github = await _provider(ProviderType.GITHUB, named_secret='GITHUB_TOKEN')
    await _link(user_id, github, 'github-access')

    assert await _auth(user_id).get_named_oauth_secret_value(github.id) == (
        'github-access'
    )


@pytest.mark.asyncio
async def test_secret_value_is_none_once_the_row_names_no_secret(
    patched_db, create_user
):
    user_id = create_user().id
    github = await _provider(ProviderType.GITHUB, named_secret='GITHUB_TOKEN')
    await _link(user_id, github, 'github-access')
    github.details.named_secret = None
    await AuthProviderStore().update(github.id, details=github.details)

    assert await _auth(user_id).get_named_oauth_secret_value(github.id) is None


@pytest.mark.asyncio
async def test_secret_value_is_none_without_a_token_or_row(patched_db, create_user):
    user_id = create_user().id
    github = await _provider(ProviderType.GITHUB, named_secret='GITHUB_TOKEN')

    auth = _auth(user_id)
    assert await auth.get_named_oauth_secret_value(github.id) is None
    assert await auth.get_named_oauth_secret_value(github.id + 1000) is None
