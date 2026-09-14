"""Native administrative routes retain database-backed lifecycle guards."""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from fastapi import HTTPException

from server.auth import account_lookup, auth_config, composition
from server.auth.bootstrap import initialize_auth_installation
from server.auth.native_types import SessionFactory
from server.routes import admin_users, super_admins
from server.services import admin_user_lifecycle_service
from server.services.native_auth_service import NativeAuthService
from storage import user_store
from storage.native_auth import AuthInstallation
from storage.role import Role
from storage.user import User
from tests.unit.server.auth.native_test_types import NativeFixture, present
from tests.unit.server.auth.test_native_account_provisioning import (
    PASSWORD,
    create_account,
)


@pytest.fixture
async def admin_configured(
    async_session_maker: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(account_lookup, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(
        admin_user_lifecycle_service, 'a_session_maker', async_session_maker
    )
    monkeypatch.setattr(user_store, 'a_session_maker', async_session_maker)
    yield async_session_maker
    composition.get_auth_services.cache_clear()
    auth_config.get_native_auth_settings.cache_clear()


@pytest.fixture
async def admin_native(admin_configured: SessionFactory) -> NativeFixture:
    await initialize_auth_installation(session_factory=admin_configured)
    async with admin_configured() as session:
        installation = present(await session.get(AuthInstallation, 1))
        account_id = present(installation.bootstrap_account_id)
    return NativeAuthService(admin_configured), account_id


async def test_native_routes_preserve_last_administrator(
    admin_native: NativeFixture,
) -> None:
    _, admin_id = admin_native
    identifier = str(admin_id)
    with pytest.raises(HTTPException) as disabled:
        await admin_users.disable_user(identifier, actor_user_id=identifier)
    assert disabled.value.status_code == 409
    with pytest.raises(HTTPException) as deleted:
        await admin_users.delete_user(identifier, actor_user_id=identifier)
    assert deleted.value.status_code == 409
    with pytest.raises(HTTPException) as demoted:
        await super_admins.revoke_super_admin(identifier, caller_user_id=identifier)
    assert demoted.value.status_code == 409


async def test_native_route_disable_revokes_sessions_and_enable_preserves_revocation(
    admin_native: NativeFixture,
    admin_configured: SessionFactory,
) -> None:
    service, admin_id = admin_native
    account_id = await create_account(admin_configured)
    login = await service.login(f'{account_id}@example.test', PASSWORD, client_ip='1')
    disabled = await admin_users.disable_user(
        str(account_id), actor_user_id=str(admin_id)
    )
    assert UUID(disabled.user_id) == account_id
    assert await service.authenticate_session(login.token) is None
    async with admin_configured() as session:
        assert present(await session.get(User, account_id)).is_disabled
    enabled = await admin_users.enable_user(
        str(account_id), actor_user_id=str(admin_id)
    )
    assert UUID(enabled.user_id) == account_id
    assert await service.authenticate_session(login.token) is None
    async with admin_configured() as session:
        assert not present(await session.get(User, account_id)).is_disabled


async def test_native_route_mutations_recheck_actor_authority(
    admin_native: NativeFixture,
    admin_configured: SessionFactory,
) -> None:
    _, admin_id = admin_native
    account_id = await create_account(admin_configured)
    actor = str(account_id)
    with pytest.raises(HTTPException) as disabled:
        await admin_users.disable_user(str(admin_id), actor_user_id=actor)
    assert disabled.value.status_code == 403
    with pytest.raises(HTTPException) as promoted:
        await super_admins.grant_super_admin(
            super_admins.GrantSuperAdminRequest(user_id=actor),
            caller_user_id=actor,
        )
    assert promoted.value.status_code == 403
    async with admin_configured() as session:
        assert present(await session.get(User, account_id)).role_id is None


async def test_native_route_promotion_allows_safe_self_demotion(
    admin_native: NativeFixture,
    admin_configured: SessionFactory,
) -> None:
    _, admin_id = admin_native
    account_id = await create_account(admin_configured)
    target = str(account_id)
    promoted = await super_admins.grant_super_admin(
        super_admins.GrantSuperAdminRequest(user_id=target),
        caller_user_id=str(admin_id),
    )
    assert promoted.user_id == target
    demoted = await super_admins.revoke_super_admin(
        str(admin_id), caller_user_id=str(admin_id)
    )
    assert demoted.user_id == str(admin_id)
    async with admin_configured() as session:
        assert present(await session.get(User, admin_id)).role_id is None
        assert present(await session.get(User, account_id)).role_id is not None


async def test_waiting_admin_operation_revalidates_actor_after_installation_lock(
    admin_native: NativeFixture,
    admin_configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession

    from server.auth.native_password import NativeAuthError
    from server.services import native_account_service
    from server.services.admin_user_lifecycle_service import (
        OpenHandsUserLifecycleService,
    )

    _, actor_id = admin_native
    await create_account(admin_configured, admin=True)
    target_id = await create_account(admin_configured)
    entered = asyncio.Event()
    lock = native_account_service.lock_native_lifecycle

    async def observe_lock(session: AsyncSession) -> None:
        entered.set()
        await lock(session)

    monkeypatch.setattr(native_account_service, 'lock_native_lifecycle', observe_lock)
    async with admin_configured() as blocker, blocker.begin():
        await lock(blocker)
        pending = asyncio.create_task(
            OpenHandsUserLifecycleService().disable_user(
                str(target_id), actor_user_id=str(actor_id)
            )
        )
        await entered.wait()
        await native_account_service.set_superadmin(blocker, actor_id, False)
    with pytest.raises(NativeAuthError, match='Global user management'):
        await pending
    async with admin_configured() as session:
        assert not present(await session.get(User, target_id)).is_disabled
