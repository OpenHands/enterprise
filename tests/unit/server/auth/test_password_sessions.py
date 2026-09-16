from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from server.auth import auth_config
from server.auth.native_password import (
    NativeAuthError,
    hash_password,
)
from server.auth.native_types import SessionFactory
from server.services.native_auth_service import NativeAuthService, NativeLogin
from storage.native_auth import AuthAccount, BrowserSession, PasswordCredential
from storage.org import Org
from storage.user import User

PASSWORD = 'A long password with spaces 987!'


async def test_password_mode_cannot_activate_before_application_wiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    monkeypatch.setattr(auth_config, 'ENABLE_KEYCLOAK', False)
    with pytest.raises(RuntimeError, match='not available in this release'):
        await SaasAppLifespanService().__aenter__()


@pytest.fixture
async def browser(
    async_session_maker: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[NativeAuthService, NativeLogin]]:
    monkeypatch.setenv('OH_WEB_URL', 'https://auth.example.test')
    auth_config.get_native_auth_settings.cache_clear()
    account_id = uuid4()
    service = NativeAuthService(async_session_maker)
    async with async_session_maker() as session, session.begin():
        account = AuthAccount(
            id=account_id,
            normalized_email='person@example.com',
            display_email='Person@example.com',
        )
        session.add_all([account, Org(id=account_id, name=str(account_id))])
        await session.flush()
        user = User(
            id=account_id,
            current_org_id=account_id,
            email=account.display_email,
            accepted_tos=datetime.now(UTC).replace(tzinfo=None),
            onboarding_completed=True,
        )
        credential = PasswordCredential(
            account_id=account_id,
            normalized_login_email='person@example.com',
            display_email='Person@example.com',
            password_hash=await hash_password(PASSWORD),
        )
        session.add_all([user, credential])
        await session.flush()
    login = await service._new_session(account_id)
    yield service, login
    auth_config.get_native_auth_settings.cache_clear()


@pytest.mark.parametrize('value', ['true', '1', 'TRUE'])
def test_keycloak_enable_values(value: str) -> None:
    assert auth_config.parse_enable_keycloak(value)


@pytest.mark.parametrize('value', ['false', '0', 'FALSE'])
def test_keycloak_disable_values(value: str) -> None:
    assert not auth_config.parse_enable_keycloak(value)


@pytest.mark.parametrize('value', ['', 'yes', ' true ', '2'])
def test_invalid_mode_fails_closed(value: str) -> None:
    with pytest.raises(ValueError, match='ENABLE_KEYCLOAK'):
        auth_config.parse_enable_keycloak(value)


@pytest.mark.parametrize('enabled', ['true', '1'])
def test_canonical_settings_win_and_legacy_settings_remain_compatible(
    monkeypatch: pytest.MonkeyPatch, enabled: str
) -> None:
    monkeypatch.setenv('OH_WEB_URL', 'https://old.example.test')
    auth_config.get_native_auth_settings.cache_clear()
    try:
        assert (
            auth_config.get_native_auth_settings().web_url == 'https://old.example.test'
        )
        monkeypatch.setenv('OH_WEB_URL', 'http://localhost:3000')
        monkeypatch.setenv('AUTH_ALLOW_INSECURE_LOCALHOST', enabled)
        auth_config.get_native_auth_settings.cache_clear()
        config = auth_config.get_native_auth_settings()
        assert config.app_origin == 'http://localhost:3000'
        monkeypatch.setenv('OH_WEB_URL', '')
        auth_config.get_native_auth_settings.cache_clear()
        with pytest.raises(ValueError, match='web_url'):
            auth_config.get_native_auth_settings()
    finally:
        auth_config.get_native_auth_settings.cache_clear()


async def test_default_database_tokens_and_logout(
    browser: tuple[NativeAuthService, NativeLogin],
) -> None:
    service, login = browser
    async with service.sessions() as session:
        stored = await session.scalar(select(BrowserSession))
        assert stored is not None
        assert stored.token == login.token
    assert await service.authenticate_session(login.token) == login.principal
    await service.revoke_session(login.token)
    assert await service.authenticate_session(login.token) is None


@pytest.mark.parametrize('invalidate', ['disabled', 'missing'])
async def test_session_rechecks_current_account_and_credential(
    browser: tuple[NativeAuthService, NativeLogin], invalidate: str
) -> None:
    service, login = browser
    async with service.sessions() as session, session.begin():
        credential = await session.get(PasswordCredential, login.principal.account_id)
        user = await session.get(User, login.principal.account_id)
        stored = await session.get(BrowserSession, login.token)
        assert credential is not None and user is not None and stored is not None
        if invalidate == 'disabled':
            user.is_disabled = True
        else:
            await session.delete(credential)
    assert await service.authenticate_session(login.token) is None


async def test_shared_throttle_commits_failed_attempts(
    async_session_maker: SessionFactory,
) -> None:
    service = NativeAuthService(async_session_maker)
    for _ in range(10):
        await service.throttle('login', '127.0.0.1', 'person@example.com')
    with pytest.raises(NativeAuthError) as error:
        await service.throttle('login', '127.0.0.2', 'person@example.com')
    assert error.value.status_code == 429


async def test_default_sessions_have_no_server_expiry(
    browser: tuple[NativeAuthService, NativeLogin],
) -> None:
    service, login = browser
    async with service.sessions() as session, session.begin():
        stored = await session.get(BrowserSession, login.token)
        assert stored is not None
        stored.created_at = datetime.now(UTC) - timedelta(days=365)
    assert await service.authenticate_session(login.token) == login.principal


async def test_logout_only_removes_the_presented_session(
    browser: tuple[NativeAuthService, NativeLogin],
) -> None:
    service, first = browser
    second = await service._new_session(first.principal.account_id)
    await service.revoke_session(first.token)
    assert await service.authenticate_session(first.token) is None
    assert await service.authenticate_session(second.token) is not None
