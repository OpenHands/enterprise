"""Durable account deletion crosses profile and external-work transactions."""

from uuid import UUID

import pytest
from sqlalchemy import select

from server.auth import bootstrap, keycloak_manager
from server.auth.native_password import NativeAuthError
from server.auth.native_types import SessionFactory
from server.services import native_auth_service, native_litellm_adapter
from server.services.admin_user_lifecycle_service import OpenHandsUserLifecycleService
from server.services.native_auth_service import NativeAuthService
from server.services.native_provisioning_service import NativeProvisioningService
from storage import database, org_store, role_store, saas_settings_store
from storage.api_key import ApiKey
from storage.native_auth import AuthAccount, PasswordCredential
from storage.native_external_work import NativeExternalPayload, NativeExternalWork
from storage.user import User
from tests.unit.server.auth.native_test_types import NativeFixture, present
from tests.unit.server.auth.test_native_account_provisioning import (
    PASSWORD,
    create_account,
)

pytest_plugins = ['tests.unit.server.auth.test_native_account_administration']


@pytest.fixture
def delete_runtime(
    admin_native: NativeFixture,
    admin_configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> NativeFixture:
    for module in (bootstrap, database, org_store, role_store, saas_settings_store):
        monkeypatch.setattr(module, 'a_session_maker', admin_configured)
    service, _ = admin_native

    def auth_service() -> NativeAuthService:
        return service

    def reject_keycloak(*args: object, **kwargs: object) -> None:
        raise AssertionError('Deletion must not construct a Keycloak client')

    monkeypatch.setattr(native_auth_service, 'get_native_auth_service', auth_service)
    monkeypatch.setattr(keycloak_manager, 'KeycloakOpenID', reject_keycloak)
    monkeypatch.setattr(keycloak_manager, 'KeycloakAdmin', reject_keycloak)
    return admin_native


async def test_administrative_delete_revokes_before_profile_cleanup_and_retries(
    delete_runtime: NativeFixture,
    admin_configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, admin_id = delete_runtime
    account_id = await create_account(admin_configured)
    email = f'{account_id}@example.com'
    login = await service.login(email, PASSWORD, client_ip='1')
    async with admin_configured() as session, session.begin():
        session.add(
            ApiKey(
                user_id=str(account_id), org_id=account_id, key='delete-this-api-key'
            )
        )

    lifecycle = OpenHandsUserLifecycleService()
    finish = lifecycle._finish_native_deletion
    attempts = 0

    async def fail_first_cleanup(target: UUID) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError('Simulated transient profile cleanup failure')
        await finish(target)

    monkeypatch.setattr(lifecycle, '_finish_native_deletion', fail_first_cleanup)
    result = present(
        await lifecycle.delete_user(str(account_id), actor_user_id=str(admin_id))
    )
    assert result.cleanup_warnings == [
        'Account access revoked; data cleanup is pending'
    ]
    assert await service.authenticate_session(login.token) is None
    async with admin_configured() as session:
        account = present(await session.get(AuthAccount, account_id))
        assert (
            account.state == 'deleted'
            and account.provisioning_status == 'cleanup_pending'
        )
        assert await session.get(User, account_id) is not None
        assert await session.get(PasswordCredential, account_id) is None
        assert (
            await session.scalar(
                select(ApiKey).where(ApiKey.user_id == str(account_id))
            )
            is None
        )
        assert (
            await session.scalar(
                select(NativeExternalWork.id).where(
                    NativeExternalWork.account_id == account_id,
                    NativeExternalWork.kind == 'delete_user',
                    NativeExternalWork.status != 'complete',
                )
            )
            is not None
        )

    assert await lifecycle.retry_native_deletions() == 0
    assert attempts == 2
    async with admin_configured() as session:
        assert await session.get(User, account_id) is None
        account = present(await session.get(AuthAccount, account_id))
        assert account.state == 'deleted' and account.provisioning_status == 'complete'
    assert await lifecycle.retry_native_deletions() == 0
    assert attempts == 2


async def test_self_delete_waits_for_external_cleanup_before_restoring_same_identity(
    delete_runtime: NativeFixture,
    admin_configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = delete_runtime
    account_id = await create_account(admin_configured, admin=True)
    email = f'{account_id}@example.com'
    login = await service.login(email, PASSWORD, client_ip='1')
    await org_store.OrgStore.delete_org_cascade(account_id, str(account_id))
    assert await service.authenticate_session(login.token) is None
    async with admin_configured() as session:
        assert (
            present(await session.get(AuthAccount, account_id)).state == 'reonboardable'
        )
        assert await session.get(User, account_id) is None
        assert await session.get(PasswordCredential, account_id) is not None
    with pytest.raises(NativeAuthError) as pending:
        await service.login(email, PASSWORD, client_ip='1')
    assert pending.value.status_code == 503
    cleanup_calls: list[tuple[str, UUID | None, UUID | None]] = []

    async def cleanup(
        kind: str,
        user_id: UUID | None,
        org_id: UUID | None,
        payload: NativeExternalPayload,
    ) -> None:
        cleanup_calls.append((kind, user_id, org_id))

    monkeypatch.setattr(native_litellm_adapter, 'cleanup_native_resource', cleanup)
    assert (await NativeProvisioningService(admin_configured).cleanup())[1] == 0
    restored = await service.login(email, PASSWORD, client_ip='2')
    assert restored.principal.account_id == account_id
    async with admin_configured() as session:
        assert present(await session.get(User, account_id)).role_id is None
    count = len(cleanup_calls)
    assert count > 0
    await NativeProvisioningService(admin_configured).cleanup()
    assert len(cleanup_calls) == count


async def test_profileless_disable_enable_and_terminal_delete(
    delete_runtime: NativeFixture,
    admin_configured: SessionFactory,
) -> None:
    service, admin_id = delete_runtime
    account_id = await create_account(admin_configured)
    email = f'{account_id}@example.com'
    await org_store.OrgStore.delete_org_cascade(account_id, str(account_id))
    lifecycle = OpenHandsUserLifecycleService()
    await lifecycle.disable_user(str(account_id), actor_user_id=str(admin_id))
    async with admin_configured() as session:
        assert (
            present(await session.get(AuthAccount, account_id)).state
            == 'profile_absent_blocked'
        )
    with pytest.raises(NativeAuthError) as denied:
        await service.login(email, PASSWORD, client_ip='1')
    assert denied.value.status_code == 401
    await lifecycle.enable_user(str(account_id), actor_user_id=str(admin_id))
    deleted = present(
        await lifecycle.delete_user(str(account_id), actor_user_id=str(admin_id))
    )
    assert not deleted.cleanup_warnings
    async with admin_configured() as session:
        assert present(await session.get(AuthAccount, account_id)).state == 'deleted'
        assert await session.get(PasswordCredential, account_id) is None
    with pytest.raises(NativeAuthError):
        await lifecycle.enable_user(str(account_id), actor_user_id=str(admin_id))


async def test_personal_org_deletion_rolls_back_revocation_and_outbox_together(
    delete_runtime: NativeFixture,
    admin_configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    from server.auth.composition import get_auth_services
    from storage.org import Org
    from storage.org_store import OrgStore

    service, _ = delete_runtime
    account_id = await create_account(admin_configured)
    login = await service.login(
        f'{account_id}@example.com', PASSWORD, client_ip='rollback'
    )
    async with admin_configured() as session, session.begin():
        session.add(
            ApiKey(user_id=str(account_id), org_id=account_id, key='rollback-api-key')
        )
        version = present(await session.get(AuthAccount, account_id)).session_version
    provisioning = get_auth_services().provisioning
    enqueue = provisioning.delete_org_resources

    async def fail_after_enqueue(
        session: AsyncSession, org_id: UUID, orphan_ids: list[str]
    ) -> None:
        await enqueue(session, org_id, orphan_ids)
        raise RuntimeError('Rollback after cleanup enqueue')

    monkeypatch.setattr(provisioning, 'delete_org_resources', fail_after_enqueue)
    with pytest.raises(RuntimeError, match='Rollback after cleanup enqueue'):
        await OrgStore.delete_org_cascade(account_id, requester_user_id=str(account_id))
    async with admin_configured() as session:
        account = present(await session.get(AuthAccount, account_id))
        assert account.state == 'profile_present'
        assert account.session_version == version
        assert await session.get(Org, account_id) is not None
        assert await session.get(User, account_id) is not None
        assert (
            await session.scalar(
                select(ApiKey).where(ApiKey.user_id == str(account_id))
            )
            is not None
        )
        assert (
            await session.scalar(
                select(NativeExternalWork.id).where(
                    NativeExternalWork.account_id == account_id,
                    NativeExternalWork.kind == 'delete_user',
                )
            )
            is None
        )
        assert (
            await session.scalar(
                select(NativeExternalWork.id).where(
                    NativeExternalWork.org_id == account_id,
                    NativeExternalWork.kind == 'delete_team',
                )
            )
            is None
        )
    assert await service.authenticate_session(login.token) is not None
