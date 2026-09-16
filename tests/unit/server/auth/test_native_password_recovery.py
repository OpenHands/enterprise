"""FastAPI Users password recovery keeps existing sessions by default."""

import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from openhands.app_server.user_auth import get_user_id
from server.auth import auth_config, bootstrap
from server.auth.native_password import NativeAuthError
from server.auth.native_types import PasswordResetLink, SessionFactory
from server.routes import auth_passwords, native_password
from server.services import native_password_service
from server.services.native_account_service import (
    set_account_enabled,
    tombstone_account,
)
from server.services.native_password_service import NativePasswordService
from storage.api_key import ApiKey
from storage.native_auth import BrowserSession
from storage.user import User
from tests.unit.server.auth.enrollment_fixtures import (
    NEW_PASSWORD,
    PASSWORD,
    NativeFixture,
    enroll,
    link_token,
    present,
)

pytest_plugins = ['tests.unit.server.auth.enrollment_fixtures']


def test_recovery_cli_passes_exact_uuid_after_installation_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    account_id = UUID('a533afdf-30eb-46e7-b6a4-43739ad89dfc')
    calls: list[str] = []
    service = NativePasswordService()

    async def verify() -> None:
        calls.append('verify')

    async def recover(target: UUID, password: str) -> None:
        assert target == account_id
        assert password == NEW_PASSWORD
        calls.append('recover')

    def password_service() -> NativePasswordService:
        return service

    def read_password(prompt: str) -> str:
        return NEW_PASSWORD

    monkeypatch.setattr(sys, 'argv', ['bootstrap', 'recover', str(account_id)])
    monkeypatch.setattr(auth_config, 'ENABLE_KEYCLOAK', False)
    monkeypatch.setattr(bootstrap, 'verify_auth_installation', verify)
    monkeypatch.setattr(bootstrap.getpass, 'getpass', read_password)
    monkeypatch.setattr(service, 'recover_password', recover)
    monkeypatch.setattr(
        native_password_service, 'get_native_password_service', password_service
    )
    bootstrap.main()
    assert calls == ['verify', 'recover']
    output = capsys.readouterr()
    assert str(account_id) in output.out
    assert NEW_PASSWORD not in output.out + output.err


@pytest.mark.parametrize('arguments', [['recover'], ['recover', 'invalid-uuid']])
def test_recovery_cli_requires_exact_uuid_before_prompt(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_prompt(prompt: str) -> str:
        pytest.fail('Invalid recovery arguments must not prompt for a password')

    monkeypatch.setattr(sys, 'argv', ['bootstrap', *arguments])
    monkeypatch.setattr(bootstrap.getpass, 'getpass', forbidden_prompt)
    with pytest.raises(SystemExit) as error:
        bootstrap.main()
    assert error.value.code == 2


def test_recovery_cli_rejects_keycloak_before_prompt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden_prompt(prompt: str) -> str:
        pytest.fail('Keycloak recovery must not prompt for a local password')

    monkeypatch.setattr(
        sys,
        'argv',
        ['bootstrap', 'recover', 'a533afdf-30eb-46e7-b6a4-43739ad89dfc'],
    )
    monkeypatch.setattr(auth_config, 'ENABLE_KEYCLOAK', True)
    monkeypatch.setattr(bootstrap.getpass, 'getpass', forbidden_prompt)
    with pytest.raises(SystemExit) as error:
        bootstrap.main()
    assert error.value.code == 2
    assert 'unavailable in Keycloak mode' in capsys.readouterr().err


def reset_token(link: PasswordResetLink) -> str:
    return link['reset_url'].split('#token=', 1)[1]


@pytest.mark.parametrize('web_path', ['', '/openhands/'])
async def test_reset_invalidates_old_links_and_preserves_sessions(
    native: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    web_path: str,
) -> None:
    enrollment, admin_id = native
    web_url = f'https://public.example.test:8443{web_path}'
    monkeypatch.setenv('OH_WEB_URL', web_url)
    auth_config.get_native_auth_settings.cache_clear()
    service = NativePasswordService(enrollment.sessions)
    person = await enroll(enrollment, admin_id)
    first = await service.issue_password_reset(admin_id, person.principal.account_id)
    second = await service.issue_password_reset(admin_id, person.principal.account_id)
    assert first['reset_url'].startswith(f'{web_url.rstrip("/")}/password-reset#token=')
    await service.complete_password_reset(
        reset_token(first), NEW_PASSWORD, client_ip='1'
    )
    assert await service.auth.authenticate_session(person.token) is not None
    with pytest.raises(NativeAuthError, match='Invalid or expired'):
        await service.complete_password_reset(
            reset_token(second), PASSWORD, client_ip='1'
        )
    with pytest.raises(NativeAuthError, match='Invalid email or password'):
        await service.auth.login(person.principal.email, PASSWORD, client_ip='old')


async def test_reset_requires_admin_but_no_recent_login(native: NativeFixture) -> None:
    enrollment, admin_id = native
    service = NativePasswordService(enrollment.sessions)
    user = await enroll(enrollment, admin_id)
    with pytest.raises(NativeAuthError):
        await service.issue_password_reset(user.principal.account_id, admin_id)
    reset = await service.issue_password_reset(admin_id, user.principal.account_id)
    await service.complete_password_reset(
        reset_token(reset), NEW_PASSWORD, client_ip='1'
    )
    invitation = await enrollment.issue_invitation(admin_id, 'setup@example.com')
    with pytest.raises(NativeAuthError, match='Invalid or expired'):
        await service.complete_password_reset(
            link_token(invitation), PASSWORD, client_ip='1'
        )


async def test_password_change_preserves_existing_sessions(
    native: NativeFixture,
) -> None:
    enrollment, _ = native
    service = NativePasswordService(enrollment.sessions)
    first = await service.auth.login('admin@example.com', PASSWORD, client_ip='1')
    second = await service.auth.login('admin@example.com', PASSWORD, client_ip='1')
    await service.change_password(
        first.principal.account_id, first.token, PASSWORD, NEW_PASSWORD, client_ip='1'
    )
    assert await service.auth.authenticate_session(first.token) is not None
    assert await service.auth.authenticate_session(second.token) is not None
    assert (
        await service.auth.login('admin@example.com', NEW_PASSWORD, client_ip='1')
    ).principal.account_id == first.principal.account_id


async def test_reset_rejects_disabled_users(
    native: NativeFixture, configured: SessionFactory
) -> None:
    enrollment, admin_id = native
    service = NativePasswordService(configured)
    person = await enroll(enrollment, admin_id)
    reset = await service.issue_password_reset(admin_id, person.principal.account_id)
    async with configured() as session, session.begin():
        present(await session.get(User, person.principal.account_id)).is_disabled = True
    with pytest.raises(NativeAuthError, match='Invalid or expired'):
        await service.complete_password_reset(
            reset_token(reset), NEW_PASSWORD, client_ip='1'
        )
    async with configured() as session:
        assert present(await session.get(User, person.principal.account_id)).is_disabled


async def test_tombstone_recovery_fails_and_disabling_revokes(
    native: NativeFixture, configured: SessionFactory
) -> None:
    enrollment, admin_id = native
    service = NativePasswordService(enrollment.sessions)
    person = await enroll(enrollment, admin_id)
    async with configured() as session, session.begin():
        await set_account_enabled(session, person.principal.account_id, False)
    assert await service.auth.get_identity(person.principal.account_id) is None
    assert await service.auth.authenticate_session(person.token) is None
    await service.recover_password(person.principal.account_id, NEW_PASSWORD)
    async with configured() as session:
        assert (
            present(await session.get(User, person.principal.account_id))
        ).is_disabled
    async with configured() as session, session.begin():
        await tombstone_account(session, person.principal.account_id)
    with pytest.raises(NativeAuthError, match='Account not found'):
        await service.recover_password(person.principal.account_id, PASSWORD)


async def test_password_operations_work_with_an_old_session(
    native: NativeFixture,
    configured: SessionFactory,
) -> None:
    _, admin_id = native
    service = NativePasswordService(configured)
    login = await service.auth.login('admin@example.com', PASSWORD, client_ip='1')
    async with configured() as session, session.begin():
        present(await session.get(BrowserSession, login.token)).created_at = (
            datetime.now(UTC) - timedelta(days=365)
        )
    await service.issue_password_reset(admin_id, admin_id)
    await service.change_password(
        admin_id, login.token, PASSWORD, NEW_PASSWORD, client_ip='1'
    )
    assert await service.auth.authenticate_session(login.token) is not None


async def test_recovery_routes_preserve_status_cookie_and_redaction(
    native: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, admin_id = native
    service = NativePasswordService(configured)
    login = await service.auth.login('admin@example.com', PASSWORD, client_ip='1')
    browser_api_key = 'sk-preserved-browser-transport-test'
    async with configured() as session, session.begin():
        session.add(ApiKey(key=browser_api_key, user_id=str(admin_id), org_id=admin_id))

    def password_service() -> NativePasswordService:
        return service

    async def actor() -> str:
        return str(admin_id)

    monkeypatch.setattr(
        native_password, 'get_native_password_service', password_service
    )
    monkeypatch.setattr(auth_passwords, 'get_native_password_service', password_service)
    app = FastAPI()
    app.include_router(native_password.native_password_router)
    app.include_router(auth_passwords.auth_passwords_router)
    app.dependency_overrides[get_user_id] = actor
    app.dependency_overrides[auth_passwords.MANAGE_USERS] = actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url='https://native.example.test',
        cookies={'openhands_session': login.token},
    ) as client:
        client.cookies.set(
            'api_key', browser_api_key, domain='native.example.test', path='/'
        )
        reset = await client.post(f'/api/admin/auth-accounts/{admin_id}/password-reset')
        assert reset.status_code == 200 and reset.headers['cache-control'] == 'no-store'
        changed = await client.post(
            '/api/auth/password/change',
            json={'current_password': PASSWORD, 'new_password': NEW_PASSWORD},
        )
        assert changed.status_code == 204
        assert 'set-cookie' not in changed.headers
        assert client.cookies['api_key'] == browser_api_key
        assert await service.auth.authenticate_session(login.token) is not None
        async with configured() as session:
            assert await session.scalar(
                select(ApiKey.user_id).where(ApiKey.key == browser_api_key)
            ) == str(admin_id)
        expired = await client.post(
            '/api/auth/password/reset/complete',
            json={
                'token': reset.json()['reset_url'].split('#token=', 1)[1],
                'new_password': PASSWORD,
            },
        )
        assert expired.status_code == 400
        invalid = await client.post(
            '/api/auth/password/change',
            json={
                'current_password': {'secret': 'never-reflect-this'},
                'new_password': PASSWORD,
            },
        )
        assert invalid.status_code == 422 and 'never-reflect-this' not in invalid.text
