"""Exercise native authentication against the migrated PostgreSQL schema."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from server.auth import auth_config, composition
from server.auth.bootstrap import initialize_auth_installation, verify_auth_installation
from server.auth.native_password import (
    NativeAuthError,
    hash_password,
)
from server.auth.native_types import SessionFactory
from server.services.native_account_service import (
    create_profile,
    set_account_enabled,
    set_superadmin,
    tombstone_account,
)
from server.services.native_auth_service import (
    NativeAuthService,
)
from server.services.native_provisioning_service import (
    NativeProvisionedKeys,
    NativeProvisioningRequest,
    NativeProvisioningService,
)
from storage.native_auth import (
    AuthAccount,
    AuthInstallation,
    PasswordCredential,
)
from storage.native_external_work import NativeExternalWork
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User
from tests.unit.server.auth.native_test_types import (
    CreateUser,
    NativeFixture,
    present,
)

PASSWORD = 'Long native test passphrase 739!'


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
    return NativeAuthService(configured), account_id


async def create_account(sessions: SessionFactory, *, admin: bool = False) -> UUID:
    account_id = uuid4()
    email = f'{account_id}@example.test'
    async with sessions() as session, session.begin():
        account = AuthAccount(
            id=account_id, normalized_email=email, display_email=email
        )
        session.add(account)
        await session.flush()
        session.add(
            PasswordCredential(
                account_id=account_id,
                normalized_login_email=email,
                display_email=email,
                password_hash=await hash_password(PASSWORD),
            )
        )
        await create_profile(session, account, email, superadmin=admin)
    return account_id


async def test_last_administrator_cannot_be_disabled_demoted_or_deleted(
    native: NativeFixture, configured: SessionFactory
) -> None:
    _, admin_id = native
    with pytest.raises(NativeAuthError, match='last active superadmin'):
        async with configured() as session, session.begin():
            await set_account_enabled(session, admin_id, False)
    with pytest.raises(NativeAuthError, match='last active superadmin'):
        async with configured() as session, session.begin():
            await set_superadmin(session, admin_id, False)
    with pytest.raises(NativeAuthError, match='last active superadmin'):
        async with configured() as session, session.begin():
            await tombstone_account(session, admin_id)


async def test_concurrent_administrator_disable_preserves_one(
    native: NativeFixture, configured: SessionFactory
) -> None:
    _, first_id = native
    second_id = await create_account(configured, admin=True)

    async def disable(account_id: UUID) -> bool:
        try:
            async with configured() as session, session.begin():
                await set_account_enabled(session, account_id, False)
            return True
        except NativeAuthError as error:
            assert error.status_code == 409
            return False

    results = await asyncio.gather(disable(first_id), disable(second_id))
    assert sorted(results) == [False, True]


async def test_disable_during_provisioning_never_publishes_stale_key(
    native: NativeFixture, configured: SessionFactory
) -> None:
    account_id = await create_account(configured)
    async with configured() as session:
        work_id = await session.scalar(
            select(NativeExternalWork.id).where(
                NativeExternalWork.account_id == account_id
            )
        )
    assert work_id is not None

    async def provider(request: NativeProvisioningRequest) -> NativeProvisionedKeys:
        async with configured() as session, session.begin():
            await set_account_enabled(session, account_id, False)
        return NativeProvisionedKeys(request.member_key)

    await NativeProvisioningService(configured).reconcile(
        provider, only_work_id=work_id
    )
    async with configured() as session:
        member = await session.get(OrgMember, (account_id, account_id))
        work = await session.get(NativeExternalWork, work_id)
        assert member is not None and work is not None
        assert member.llm_api_key.get_secret_value() == ''
        assert work.status == 'cleanup'


async def test_bootstrap_concurrent_and_immutable(
    configured: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    await asyncio.gather(
        *(initialize_auth_installation(session_factory=configured) for _ in range(3))
    )
    async with configured() as session:
        assert await session.scalar(select(func.count()).select_from(AuthAccount)) == 1
        account = await session.scalar(select(AuthAccount))
        user = await session.get(User, present(account).id)
        owner = await session.get(OrgMember, (present(account).id, present(account).id))
        assert present(user).id == present(user).current_org_id == present(owner).org_id
        assert present(user).role_id == await session.scalar(
            select(Role.id).where(Role.name == 'admin')
        )
        assert present(user).email_verified is False
        assert present(user).accepted_tos is None
        assert present(account).provisioning_status == 'pending'
        assert present(owner).llm_api_key.get_secret_value() == ''
        credential = await session.get(PasswordCredential, present(account).id)
        original_hash = present(credential).password_hash
    monkeypatch.delenv('SUPERADMIN_EMAIL')
    monkeypatch.delenv('SUPERADMIN_PASSWORD')
    await initialize_auth_installation(session_factory=configured)
    await verify_auth_installation(session_factory=configured)
    async with configured() as session:
        assert (
            present(await session.get(PasswordCredential, present(account).id))
        ).password_hash == original_hash
    monkeypatch.setattr(auth_config, 'AUTH_MODE', 'keycloak')
    with pytest.raises(RuntimeError, match='changing modes is unsupported'):
        await initialize_auth_installation(session_factory=configured)


async def test_bootstrap_failure_rolls_back_everything(
    configured: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('SUPERADMIN_PASSWORD', 'short')
    with pytest.raises(NativeAuthError):
        await initialize_auth_installation(session_factory=configured)
    async with configured() as session:
        for model in (
            AuthInstallation,
            AuthAccount,
            PasswordCredential,
            User,
            Org,
            OrgMember,
        ):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_native_rejects_legacy_identity(
    configured: SessionFactory, create_user: CreateUser
) -> None:
    create_user(email='legacy@example.test')
    with pytest.raises(RuntimeError, match='identity-empty'):
        await initialize_auth_installation(session_factory=configured)
    async with configured() as session:
        assert await session.get(AuthInstallation, 1) is None


async def test_keycloak_ignores_native_environment_and_preserves_legacy(
    configured: SessionFactory, create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = create_user(email='Old@Example.test')
    monkeypatch.setattr(auth_config, 'AUTH_MODE', 'keycloak')
    monkeypatch.setattr(auth_config, 'ENABLE_KEYCLOAK', True)
    monkeypatch.setenv('OH_WEB_URL', 'invalid')
    monkeypatch.setenv('SUPERADMIN_PASSWORD', 'bad')
    await initialize_auth_installation(session_factory=configured)
    async with configured() as session:
        assert (present(await session.get(User, legacy.id))).email == legacy.email
        assert (present(await session.get(AuthInstallation, 1))).mode == 'keycloak'
        assert await session.scalar(select(func.count()).select_from(AuthAccount)) == 0


async def test_default_org_bootstrap_is_db_only_and_explicit(
    configured: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.routes.org_models import OrgResponse

    monkeypatch.setenv('OPENHANDS_DEFAULT_ORG_ENABLED', '1')
    monkeypatch.setenv('OPENHANDS_DEFAULT_ORG_NAME', 'Our Enterprise')
    await initialize_auth_installation(session_factory=configured)
    async with configured() as session:
        org = await session.scalar(select(Org).where(Org.is_default.is_(True)))
        user = await session.scalar(select(User))
        assert present(org).name == 'Our Enterprise'
        response = OrgResponse.from_org(present(org))
        assert response.contact_name == present(user).email
        assert response.contact_email == present(user).email
        assert present(user).current_org_id == present(org).id
        assert (
            present(await session.get(OrgMember, (present(org).id, present(user).id)))
        ).status == 'pending_llm_provisioning'


async def test_provisioning_failure_retries_with_stable_key_and_never_fakes_keys(
    native: NativeFixture, configured: SessionFactory
) -> None:
    from pydantic import SecretStr

    from server.services.native_provisioning_service import (
        NativeProvisioningService,
    )

    _, admin_id = native
    requests: list[NativeProvisioningRequest] = []

    async def provider(request: NativeProvisioningRequest) -> NativeProvisionedKeys:
        requests.append(request)
        if len(requests) == 1:
            raise RuntimeError('provider unavailable')
        return NativeProvisionedKeys(SecretStr('real-member-key'))

    service = NativeProvisioningService(configured)
    assert await service.reconcile(provider) == (0, 1)
    async with configured() as session:
        assert (
            present(await session.get(AuthAccount, admin_id))
        ).provisioning_status == 'pending'
        assert (
            present(await session.get(OrgMember, (admin_id, admin_id)))
        ).llm_api_key.get_secret_value() == ''
    assert await service.reconcile(provider) == (1, 0)
    assert requests[0].idempotency_key == requests[1].idempotency_key
    async with configured() as session:
        assert (
            present(await session.get(AuthAccount, admin_id))
        ).provisioning_status == 'complete'
        assert (
            present(await session.get(OrgMember, (admin_id, admin_id)))
        ).llm_api_key.get_secret_value() == 'real-member-key'
    assert await service.reconcile(provider) == (0, 0)


async def test_legacy_settings_block_native_even_without_user(
    configured: SessionFactory,
) -> None:
    from storage.user_settings import UserSettings

    async with configured() as session, session.begin():
        session.add(UserSettings(keycloak_user_id='legacy-profile-deleted'))
    with pytest.raises(RuntimeError, match='identity-empty'):
        await initialize_auth_installation(session_factory=configured)
