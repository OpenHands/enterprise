"""Exercise native authentication against the migrated PostgreSQL schema."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from server.auth import auth_config
from server.auth.bootstrap import initialize_auth_installation, verify_auth_installation
from server.auth.native_password import (
    NativeAuthError,
    hash_password,
    normalize_email,
    validate_password,
    verify_password,
)
from server.auth.native_session import (
    ANONYMOUS_CSRF_COOKIE,
    SESSION_COOKIE,
    digest_token,
)
from server.auth.native_types import InvitationLink, PasswordResetLink, SessionFactory
from server.services.native_account_service import (
    mark_self_deleted,
    set_account_enabled,
    set_superadmin,
    tombstone_account,
)
from server.services.native_auth_service import (
    NativeAuthService,
    NativeLogin,
    safe_return_path,
)
from server.services.native_provisioning_service import (
    NativeProvisionedKeys,
    NativeProvisioningRequest,
)
from storage.native_auth import (
    AccountInvitation,
    AuthAccount,
    AuthInstallation,
    BrowserSession,
    PasswordCredential,
)
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
NEW_PASSWORD = 'A replacement test passphrase 846!'


@pytest.fixture
async def configured(
    async_session_maker: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[SessionFactory]:
    monkeypatch.setattr(auth_config, 'ENABLE_KEYCLOAK', False)
    monkeypatch.setattr(auth_config, 'AUTH_MODE', 'native')
    monkeypatch.setenv('NATIVE_AUTH_APP_ORIGIN', 'https://native.example.test')
    monkeypatch.setenv('SUPERADMIN_EMAIL', 'Admin@Example.test')
    monkeypatch.setenv('SUPERADMIN_PASSWORD', PASSWORD)
    monkeypatch.setenv('OPENHANDS_DEFAULT_ORG_ENABLED', 'false')
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
    auth_config.get_native_auth_settings.cache_clear()


@pytest.fixture
async def native(configured: SessionFactory) -> NativeFixture:
    await initialize_auth_installation(session_factory=configured)
    async with configured() as session:
        installation = await session.get(AuthInstallation, 1)
        account_id = present(present(installation).bootstrap_account_id)
    return NativeAuthService(configured), account_id


def link_token(link: InvitationLink) -> str:
    return link['invite_url'].split('#token=', 1)[1]


def reset_token(link: PasswordResetLink) -> str:
    return link['reset_url'].split('#token=', 1)[1]


async def enroll(
    service: NativeAuthService, admin_id: UUID, email: str = 'person@example.test'
) -> NativeLogin:
    invitation = await service.issue_invitation(admin_id, email)
    result = await service.complete_enrollment(
        link_token(invitation), PASSWORD, client_ip='127.0.0.1'
    )
    assert result is not None
    return result


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        (None, True),
        ('true', True),
        ('TRUE', True),
        ('1', True),
        ('false', False),
        ('FALSE', False),
        ('0', False),
    ],
)
def test_strict_mode(value: str | None, expected: bool) -> None:
    assert auth_config.parse_enable_keycloak(value) is expected


@pytest.mark.parametrize('value', ['', 'yes', 'off', ' true ', '2'])
def test_reject_invalid_mode(value: str) -> None:
    with pytest.raises(ValueError):
        auth_config.parse_enable_keycloak(value)


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
    monkeypatch.setenv('NATIVE_AUTH_APP_ORIGIN', 'invalid')
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


async def test_passwords_are_argon2_and_never_normalized() -> None:
    hashed = await hash_password(PASSWORD)
    assert hashed.startswith('$argon2id$')
    assert await verify_password(hashed, PASSWORD)
    assert not await verify_password(hashed, PASSWORD + ' ')
    assert not await verify_password(None, PASSWORD)
    assert normalize_email(' A+B@Example.TEST ') == 'a+b@example.test'
    for weak in ('passwordpassword', 'Password123456789', '1234567890123456'):
        with pytest.raises(NativeAuthError):
            validate_password(weak)


async def test_sessions_are_opaque_expire_and_revoke(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    result = await service.login(' admin@example.TEST ', PASSWORD, client_ip='1.2.3.4')
    principal = await service.authenticate_session(result.token)
    assert present(principal).account_id == admin_id
    async with configured() as session, session.begin():
        row = await session.get(BrowserSession, present(principal).session_id)
        assert present(row).token_digest == digest_token(result.token, 'session')
        assert result.token not in present(row).token_digest
        absolute = present(row).absolute_expires_at
        present(row).idle_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await service.authenticate_session(result.token) is None
    async with configured() as session:
        assert (
            present(await session.get(BrowserSession, present(principal).session_id))
        ).absolute_expires_at == absolute
    replacement = await service.login(
        'admin@example.test', PASSWORD, client_ip='1.2.3.4'
    )
    await service.revoke_session(replacement.token)
    assert await service.authenticate_session(replacement.token) is None


async def test_login_throttle_and_generic_errors(native: NativeFixture) -> None:
    service, _ = native
    for index in range(10):
        with pytest.raises(NativeAuthError, match='Invalid email or password') as error:
            await service.login('missing@example.test', 'wrong', client_ip=str(index))
        assert error.value.status_code == 401
    with pytest.raises(NativeAuthError) as error:
        await service.login('missing@example.test', PASSWORD, client_ip='different-ip')
    assert error.value.status_code == 429


async def test_enrollment_is_single_use_and_does_not_grant_admin(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    link = await service.issue_invitation(admin_id, 'Invited@Example.test')
    token = link_token(link)
    assert (await service.inspect_invitation(token))['action'] == 'set_password'
    result = await service.complete_enrollment(token, PASSWORD, client_ip='1')
    async with configured() as session:
        user = await session.get(User, present(result).principal.account_id)
        invitation = await session.get(AccountInvitation, UUID(link['invitation_id']))
        assert present(user).role_id is None
        assert present(invitation).accepted_account_id == present(user).id
        assert present(invitation).token_digest == digest_token(token, 'enrollment')
    with pytest.raises(NativeAuthError, match='Invalid or expired'):
        await service.complete_enrollment(token, NEW_PASSWORD, client_ip='1')


async def test_concurrent_invitation_consumption(native: NativeFixture) -> None:
    service, admin_id = native
    link = await service.issue_invitation(admin_id, 'race@example.test')
    results = await asyncio.gather(
        *(
            service.complete_enrollment(link_token(link), PASSWORD, client_ip=str(i))
            for i in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, NativeAuthError) for result in results) == 1


async def test_competing_links_resolve_durable_account_without_password_overwrite(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    first = await service.issue_invitation(admin_id, 'same@example.test')
    second = await service.issue_invitation(admin_id, 'SAME@example.test')
    logged_in = await service.complete_enrollment(
        link_token(first), PASSWORD, client_ip='1'
    )
    assert (await service.inspect_invitation(link_token(second)))['action'] == 'login'
    assert (
        await service.complete_enrollment(
            link_token(second), NEW_PASSWORD, client_ip='1'
        )
        is None
    )
    with pytest.raises(NativeAuthError, match='Sign in as the invited account'):
        await service.accept_membership(admin_id, link_token(second))
    await service.accept_membership(
        present(logged_in).principal.account_id, link_token(second)
    )
    again = await service.login('same@example.test', PASSWORD, client_ip='1')
    assert (
        present(again).principal.account_id == present(logged_in).principal.account_id
    )
    async with configured() as session:
        invitation = await session.get(AccountInvitation, UUID(second['invitation_id']))
        assert (
            present(invitation).reserved_account_id
            != present(invitation).accepted_account_id
        )
        assert present(invitation).accepted_account_id == again.principal.account_id


async def test_creator_demotion_invalidates_invitation(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    other = await enroll(service, admin_id)
    async with configured() as session, session.begin():
        await set_superadmin(session, other.principal.account_id, True)
    link = await service.issue_invitation(admin_id, 'another@example.test')
    async with configured() as session, session.begin():
        await set_superadmin(session, admin_id, False)
    with pytest.raises(NativeAuthError, match='Global user management'):
        await service.inspect_invitation(link_token(link))


async def test_reset_single_use_revokes_sessions_without_reenable(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    admin = await service.login('admin@example.test', PASSWORD, client_ip='1')
    person = await enroll(service, admin_id)
    first = await service.issue_password_reset(
        admin_id, person.principal.account_id, admin.token
    )
    second = await service.issue_password_reset(
        admin_id, person.principal.account_id, admin.token
    )
    async with configured() as session, session.begin():
        # Disable the profile without independently invalidating the reset.
        (
            present(await session.get(User, person.principal.account_id))
        ).is_disabled = True
    await service.complete_password_reset(
        reset_token(first), NEW_PASSWORD, client_ip='1'
    )
    assert await service.authenticate_session(person.token) is None
    with pytest.raises(NativeAuthError, match='Invalid or expired'):
        await service.complete_password_reset(
            reset_token(second), PASSWORD, client_ip='1'
        )
    async with configured() as session:
        user = await session.get(User, person.principal.account_id)
        assert present(user).is_disabled is True
        assert present(user).role_id is None


async def test_reset_requires_recent_same_admin_and_purpose(
    native: NativeFixture,
) -> None:
    service, admin_id = native
    user = await enroll(service, admin_id)
    with pytest.raises(NativeAuthError, match='Sign in again'):
        await service.issue_password_reset(
            admin_id, user.principal.account_id, user.token
        )
    invitation = await service.issue_invitation(admin_id, 'setup@example.test')
    with pytest.raises(NativeAuthError, match='Invalid or expired'):
        await service.complete_password_reset(
            link_token(invitation), NEW_PASSWORD, client_ip='1'
        )


async def test_password_change_rotates_all_sessions(native: NativeFixture) -> None:
    service, _ = native
    first = await service.login('admin@example.test', PASSWORD, client_ip='1')
    second = await service.login('admin@example.test', PASSWORD, client_ip='1')
    changed = await service.change_password(
        first.principal.account_id, first.token, PASSWORD, NEW_PASSWORD, client_ip='1'
    )
    assert await service.authenticate_session(first.token) is None
    assert await service.authenticate_session(second.token) is None
    assert await service.authenticate_session(changed.token) is not None
    assert (
        await service.login('admin@example.test', NEW_PASSWORD, client_ip='1')
    ).principal.account_id == first.principal.account_id


async def test_last_active_admin_guard(
    native: NativeFixture, configured: SessionFactory
) -> None:
    _, admin_id = native
    for operation in (
        lambda s: set_superadmin(s, admin_id, False),
        lambda s: set_account_enabled(s, admin_id, False),
        lambda s: tombstone_account(s, admin_id),
        lambda s: mark_self_deleted(s, admin_id),
    ):
        with pytest.raises(NativeAuthError, match='last active superadmin'):
            async with configured() as session, session.begin():
                await operation(session)


async def test_self_delete_reonboards_same_uuid_with_no_global_role(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    person = await enroll(service, admin_id)
    account_id = person.principal.account_id
    async with configured() as session, session.begin():
        await set_superadmin(session, account_id, True)
        await mark_self_deleted(session, account_id)
        await session.execute(delete(OrgMember).where(OrgMember.user_id == account_id))
        await session.execute(delete(User).where(User.id == account_id))
        await session.execute(delete(Org).where(Org.id == account_id))
    assert await service.get_identity(account_id) is None
    assert await service.authenticate_session(person.token) is None
    invite = await service.issue_invitation(admin_id, 'PERSON@example.test')
    assert (await service.inspect_invitation(link_token(invite)))['action'] == 'login'
    restored = await service.login('person@example.test', PASSWORD, client_ip='1')
    assert restored.principal.account_id == account_id
    async with configured() as session:
        assert (present(await session.get(User, account_id))).role_id is None
    await service.accept_membership(account_id, link_token(invite))


async def test_tombstone_recovery_fails_and_disabling_revokes(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    person = await enroll(service, admin_id)
    async with configured() as session, session.begin():
        await set_account_enabled(session, person.principal.account_id, False)
    assert await service.get_identity(person.principal.account_id) is None
    assert await service.authenticate_session(person.token) is None
    await service.recover_password(person.principal.account_id, NEW_PASSWORD)
    async with configured() as session:
        assert (
            present(await session.get(User, person.principal.account_id))
        ).is_disabled
    async with configured() as session, session.begin():
        await tombstone_account(session, person.principal.account_id)
    with pytest.raises(NativeAuthError, match='Account not found'):
        await service.recover_password(person.principal.account_id, PASSWORD)


async def test_csrf_bound_to_cookie_and_expired_cookie_is_cleared(
    native: NativeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.routes import native_auth

    service, _ = native
    monkeypatch.setattr(native_auth, 'get_native_auth_service', lambda: service)
    csrf_token, anonymous = await service.issue_csrf(None)
    assert await service.validate_csrf(None, anonymous, csrf_token)
    assert not await service.validate_csrf(None, 'other' * 10, csrf_token)
    app = FastAPI()
    app.include_router(native_auth.native_auth_router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='https://native.example.test'
    ) as client:
        client.cookies.set(
            SESSION_COOKIE,
            'expired-session-token' * 3,
            domain='native.example.test',
            path='/',
        )
        response = await client.get('/api/auth/csrf')
        assert response.status_code == 200
        assert not client.cookies.get(SESSION_COOKIE)
        assert response.headers['cache-control'] == 'no-store'
        proof = response.json()['csrf_token']
        assert await service.validate_csrf(
            client.cookies.get(SESSION_COOKIE),
            client.cookies.get(ANONYMOUS_CSRF_COOKIE),
            proof,
        )
        login = await client.post(
            '/api/auth/password/login',
            json={'email': 'admin@example.test', 'password': PASSWORD},
        )
        assert login.status_code == 200
        cookie = login.headers['set-cookie']
        assert (
            'Secure' in cookie
            and 'HttpOnly' in cookie
            and 'SameSite=lax' in cookie
            and 'Domain=' not in cookie
        )
        new_proof, anon = await service.issue_csrf(client.cookies.get(SESSION_COOKIE))
        assert anon is None
        assert await service.validate_csrf(
            client.cookies.get(SESSION_COOKIE), None, new_proof
        )
        assert not await service.validate_csrf(
            client.cookies.get(SESSION_COOKIE), None, proof
        )


@pytest.mark.parametrize(
    'value',
    ['//evil.test', '/\\evil.test', 'https://evil.test', '/%2Fevil.test', '/%0aevil'],
)
def test_redirect_rejects_external_and_control_paths(value: str) -> None:
    assert safe_return_path(value) == '/'


async def test_reissue_invalidates_old_link_and_lists_never_expose_secret(
    native: NativeFixture,
) -> None:
    service, admin_id = native
    first = await service.issue_invitation(admin_id, 'reissued@example.test')
    second = await service.reissue_invitation(admin_id, UUID(first['invitation_id']))
    with pytest.raises(NativeAuthError, match='Invalid or expired'):
        await service.inspect_invitation(link_token(first))
    assert (await service.inspect_invitation(link_token(second)))[
        'action'
    ] == 'set_password'
    metadata = await service.list_invitations(admin_id)
    assert len(metadata['items']) == 2
    assert 'token' not in str(metadata)
    await service.revoke_invitation(admin_id, UUID(second['invitation_id']))
    with pytest.raises(NativeAuthError, match='Invalid or expired'):
        await service.inspect_invitation(link_token(second))


async def test_scoped_invitation_rechecks_and_adds_only_authorized_membership(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    async with configured() as session:
        role_id = await session.scalar(select(Role.id).where(Role.name == 'member'))
    invitation = await service.issue_invitation(
        admin_id, 'scoped@example.test', admin_id, role_id
    )
    result = await service.complete_enrollment(
        link_token(invitation), PASSWORD, client_ip='1'
    )
    async with configured() as session:
        member = await session.get(
            OrgMember, (admin_id, present(result).principal.account_id)
        )
        assert present(member).role_id == role_id
        assert (
            present(await session.get(User, present(result).principal.account_id))
        ).role_id is None
        memberships = (
            await session.scalars(
                select(OrgMember).where(
                    OrgMember.user_id == present(result).principal.account_id
                )
            )
        ).all()
        assert len(memberships) == 2
    with pytest.raises(NativeAuthError, match='Global user management'):
        await service.issue_invitation(
            present(result).principal.account_id, 'other@example.test'
        )


async def test_concurrent_last_admin_disable_preserves_one(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    other = await enroll(service, admin_id)
    async with configured() as session, session.begin():
        await set_superadmin(session, other.principal.account_id, True)

    async def disable(account_id: UUID) -> None:
        async with configured() as session, session.begin():
            await set_account_enabled(session, account_id, False)

    results = await asyncio.gather(
        disable(admin_id), disable(other.principal.account_id), return_exceptions=True
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, NativeAuthError) for result in results) == 1


async def test_concurrent_reset_consumption_revokes_version_once(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    admin = await service.login('admin@example.test', PASSWORD, client_ip='1')
    other = await enroll(service, admin_id)
    reset = await service.issue_password_reset(
        admin_id, other.principal.account_id, admin.token
    )
    results = await asyncio.gather(
        *(
            service.complete_password_reset(
                reset_token(reset), NEW_PASSWORD, client_ip=str(i)
            )
            for i in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, NativeAuthError) for result in results) == 1
    async with configured() as session:
        assert (
            present(await session.get(PasswordCredential, other.principal.account_id))
        ).credential_version == 2


async def test_profileless_disable_and_unexpected_absence_fail_closed(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    person = await enroll(service, admin_id)
    account_id = person.principal.account_id
    async with configured() as session, session.begin():
        await mark_self_deleted(session, account_id)
        await session.execute(delete(OrgMember).where(OrgMember.user_id == account_id))
        await session.execute(delete(User).where(User.id == account_id))
        await session.execute(delete(Org).where(Org.id == account_id))
        await set_account_enabled(session, account_id, False)
    with pytest.raises(NativeAuthError, match='Invalid email or password'):
        await service.login('person@example.test', PASSWORD, client_ip='1')
    accounts = await service.list_accounts(admin_id, account_id=account_id)
    assert accounts['items'][0]['state'] == 'profile_absent_blocked'
    assert not accounts['items'][0]['profile_present']
    async with configured() as session, session.begin():
        await set_account_enabled(session, account_id, True)
        (present(await session.get(AuthAccount, account_id))).state = 'profile_present'
    with pytest.raises(NativeAuthError, match='Invalid email or password'):
        await service.login('person@example.test', PASSWORD, client_ip='1')


async def test_provisioning_failure_retries_with_stable_key_and_never_fakes_keys(
    native: NativeFixture, configured: SessionFactory
) -> None:
    from pydantic import SecretStr

    from server.services.native_provisioning_service import (
        NativeProvisioningService,
    )

    _, admin_id = native
    requests = []

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


async def test_native_routes_reject_keycloak_mode_and_redact_password_validation(
    native: NativeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.routes import native_auth

    service, _ = native
    monkeypatch.setattr(native_auth, 'get_native_auth_service', lambda: service)
    app = FastAPI()
    app.include_router(native_auth.native_auth_router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='https://native.example.test'
    ) as client:
        invalid = await client.post(
            '/api/auth/password/login', json={'email': 3, 'password': PASSWORD}
        )
        assert invalid.status_code == 422
        assert PASSWORD not in invalid.text
        assert 'input' not in invalid.text
        monkeypatch.setattr(auth_config, 'ENABLE_KEYCLOAK', True)
        disabled = await client.get('/api/auth/csrf')
        assert disabled.status_code == 404


async def test_legacy_settings_block_native_even_without_user(
    configured: SessionFactory,
) -> None:
    from storage.user_settings import UserSettings

    async with configured() as session, session.begin():
        session.add(UserSettings(keycloak_user_id='legacy-profile-deleted'))
    with pytest.raises(RuntimeError, match='identity-empty'):
        await initialize_auth_installation(session_factory=configured)


async def test_recent_auth_and_absolute_expiry_cannot_be_extended(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    login = await service.login('admin@example.test', PASSWORD, client_ip='1')
    async with configured() as session, session.begin():
        row = await session.get(BrowserSession, login.principal.session_id)
        present(row).auth_time = datetime.now(UTC) - timedelta(minutes=16)
    with pytest.raises(NativeAuthError, match='Sign in again'):
        await service.issue_password_reset(admin_id, admin_id, login.token)
    async with configured() as session, session.begin():
        row = await session.get(BrowserSession, login.principal.session_id)
        present(row).absolute_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        present(row).idle_expires_at = datetime.now(UTC) + timedelta(minutes=30)
    assert await service.authenticate_session(login.token) is None
