"""PostgreSQL-backed account enrollment fixtures shared with recovery tests."""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest

from server.auth import auth_config, composition
from server.auth.bootstrap import initialize_auth_installation
from server.auth.native_types import InvitationLink, SessionFactory
from server.services.native_auth_service import NativeLogin
from server.services.native_enrollment_service import NativeEnrollmentService
from storage.native_auth import AuthInstallation
from storage.role import Role

PASSWORD = 'Original Password 123!'
NEW_PASSWORD = 'Replacement Password 456!'
NativeFixture = tuple[NativeEnrollmentService, UUID]


def present[T](value: T | None) -> T:
    assert value is not None
    return value


@pytest.fixture
async def configured(
    async_session_maker: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[SessionFactory]:
    monkeypatch.setattr(auth_config, 'ENABLE_KEYCLOAK', False)
    monkeypatch.setattr(auth_config, 'AUTH_MODE', 'native')
    monkeypatch.setenv('OH_WEB_URL', 'https://native.example.test')
    monkeypatch.setenv('SUPERADMIN_EMAIL', 'Admin@Example.test')
    monkeypatch.setenv('SUPERADMIN_PASSWORD', PASSWORD)
    monkeypatch.setenv('OPENHANDS_DEFAULT_ORG_ENABLED', 'false')
    composition.get_auth_services.cache_clear()
    auth_config.get_native_auth_settings.cache_clear()
    async with async_session_maker() as session, session.begin():
        session.add_all(
            [
                Role(name='admin', rank=1),
                Role(name='owner', rank=0),
                Role(name='member', rank=2),
            ]
        )
    yield async_session_maker
    composition.get_auth_services.cache_clear()
    auth_config.get_native_auth_settings.cache_clear()


@pytest.fixture
async def native(configured: SessionFactory) -> NativeFixture:
    await initialize_auth_installation(session_factory=configured)
    async with configured() as session:
        installation = await session.get(AuthInstallation, 1)
        account_id = present(present(installation).bootstrap_account_id)
    return NativeEnrollmentService(configured), account_id


def link_token(link: InvitationLink) -> str:
    return link['invite_url'].split('#token=', 1)[1]


async def enroll(
    service: NativeEnrollmentService, admin_id: UUID, email: str = 'person@example.test'
) -> NativeLogin:
    invitation = await service.issue_invitation(admin_id, email)
    result = await service.complete_enrollment(
        link_token(invitation), PASSWORD, client_ip='127.0.0.1'
    )
    assert result is not None
    return result
