"""Federated accounts exercise the same migrated PostgreSQL lifecycle as email."""

import asyncio
import importlib
import json
from datetime import UTC, datetime, timedelta
from typing import Unpack
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import Request
from sqlalchemy import Engine, delete, func, select, text

from openhands.app_server.user.auth_user_context import AuthUserContext
from openhands.app_server.user.user_models import UserInfo
from server.auth import saml_config, token_manager
from server.auth.native_password import NativeAuthError
from server.auth.native_types import InvitationLink, SessionFactory
from server.auth.saas_user_auth import SaasUserAuth
from server.routes import users_v1
from server.services.native_account_service import (
    mark_self_deleted,
    require_active_admin,
    set_account_enabled,
    set_superadmin,
    tombstone_account,
)
from server.services.native_auth_service import NativeAuthService, NativeLogin
from server.services.native_provisioning_service import (
    NativeProvisionedKeys,
    NativeProvisioningRequest,
    NativeProvisioningService,
)
from server.services.org_invitation_service import OrgInvitationService
from storage import database, user_store
from storage.api_key_store import ApiKeyStore
from storage.native_auth import (
    AccountInvitation,
    AuthAccount,
    BrowserSession,
    ExternalIdentity,
    PasswordCredential,
)
from storage.native_external_work import NativeExternalWork
from storage.org import Org
from storage.org_invitation import OrgInvitation
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User
from storage.user_authorization import UserAuthorization
from tests.unit.server.auth.native_test_types import (
    ConfiguredSamlIdentity,
    FederatedOptions,
    NativeRuntime,
    present,
)
from tests.unit.server.auth.test_native_runtime import PASSWORD

pytest_plugins = ['tests.unit.server.auth.test_native_runtime']

ISSUER = 'https://idp.example.test/entity'
CONNECTION = 'company'
EMAIL = 'Person@Example.test'


@pytest.fixture
def federated(
    native_runtime: NativeRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> NativeRuntime:
    settings = ConfiguredSamlIdentity(connection_id=CONNECTION, issuer=ISSUER)
    monkeypatch.setattr(saml_config, 'get_saml_settings', lambda: settings)
    for module in (token_manager, user_store, users_v1):
        monkeypatch.setattr(module, 'ENABLE_KEYCLOAK', False)
    monkeypatch.setattr(token_manager, 'a_session_maker', async_session_maker)
    return native_runtime


async def sso(
    service: NativeAuthService,
    *,
    subject: str = 'persistent-subject',
    email: str = EMAIL,
    allow_jit: bool = True,
    **kwargs: Unpack[FederatedOptions],
) -> NativeLogin:
    return await service.complete_federated_login(
        connection_id=CONNECTION,
        issuer=ISSUER,
        subject=subject,
        email=email,
        allow_jit=allow_jit,
        **kwargs,
    )


def invitation_token(result: InvitationLink) -> str:
    return result['invite_url'].split('#token=', 1)[1]


async def test_jit_is_passwordless_ordinary_and_reuses_subject(
    federated: NativeRuntime, async_session_maker: SessionFactory
) -> None:
    service, admin, _ = federated
    login = await sso(service, return_path='/conversations/123')
    again = await sso(service)
    assert login.principal.account_id == again.principal.account_id
    assert login.principal.auth_method == 'saml'
    assert login.redirect_to == '/accept-tos?redirect_url=%2Fconversations%2F123'
    assert (present(await service.authenticate_session(login.token))).email == EMAIL
    assert (
        present(await service.get_identity(login.principal.account_id))
    ).email == EMAIL
    async with async_session_maker() as session:
        account = await session.get(AuthAccount, login.principal.account_id)
        assert present(account).normalized_email == EMAIL.lower()
        assert await session.get(PasswordCredential, present(account).id) is None
        user = await session.get(User, present(account).id)
        assert present(user).role_id is None
        assert present(user).accepted_tos is None
        assert present(user).email_verified is False
        browser = await session.get(BrowserSession, login.principal.session_id)
        assert present(browser).external_identity_id is not None
        assert present(browser).auth_method == 'saml'
        assert present(browser).credential_version == 0
        assert (
            await session.scalar(select(func.count()).select_from(ExternalIdentity))
            == 1
        )
    accounts = await service.list_accounts(admin.principal.account_id)
    row = next(
        row for row in accounts['items'] if row['id'] == str(login.principal.account_id)
    )
    assert row['email'] == EMAIL
    assert row['authentication_methods'] == ['saml']


async def test_federated_session_api_key_and_profile(
    federated: NativeRuntime, async_session_maker: SessionFactory
) -> None:
    service, _, _ = federated
    login = await sso(service)
    async with async_session_maker() as session, session.begin():
        (
            present(await session.get(User, login.principal.account_id))
        ).email = 'stale@example.test'
    key = await ApiKeyStore().create_api_key(str(login.principal.account_id))
    request = Request(
        {'type': 'http', 'headers': [(b'authorization', f'Bearer {key}'.encode())]}
    )
    auth = await SaasUserAuth.get_instance(request)
    assert auth.user_id == str(login.principal.account_id)
    assert await auth.get_user_email() == EMAIL
    auth._org_info_loaded = True
    context = AuthUserContext(
        user_auth=auth, _user_info=UserInfo(id=auth.user_id, email='stale@example.test')
    )
    response = await users_v1.get_current_user_saas(context, expose_secrets=False)
    body = json.loads(response.body)
    assert body['email'] == EMAIL
    assert body['has_password'] is False
    assert body['authentication_methods'] == ['saml']
    assert (
        present(await user_store.UserStore.get_user_by_email(EMAIL))
    ).id == login.principal.account_id
    assert await token_manager.TokenManager().get_user_id_from_user_email(EMAIL) == str(
        login.principal.account_id
    )
    async with async_session_maker() as session, session.begin():
        await set_account_enabled(session, login.principal.account_id, False)
    assert await service.authenticate_session(login.token) is None
    assert await service.get_identity(login.principal.account_id) is None


async def test_federated_accounts_provision_llm_without_password(
    federated: NativeRuntime, async_session_maker: SessionFactory
) -> None:
    service, _, _ = federated
    login = await sso(service)
    async with async_session_maker() as session:
        work_id = await session.scalar(
            select(NativeExternalWork.id).where(
                NativeExternalWork.account_id == login.principal.account_id
            )
        )
    seen = []

    async def provision(request: NativeProvisioningRequest) -> NativeProvisionedKeys:
        seen.append((request.account_id, request.email))
        return NativeProvisionedKeys(request.member_key)

    assert await NativeProvisioningService(async_session_maker).reconcile(
        provision, only_work_id=work_id
    ) == (1, 0)
    assert seen == [(login.principal.account_id, EMAIL)]


async def test_invitation_only_admission_creates_federated_membership(
    federated: NativeRuntime, async_session_maker: SessionFactory
) -> None:
    service, admin, _ = federated
    with pytest.raises(NativeAuthError, match='invitation is required'):
        await sso(service, allow_jit=False)
    async with async_session_maker() as session, session.begin():
        org = Org(id=uuid4(), name='Invited team')
        session.add(org)
        role_id = await session.scalar(select(Role.id).where(Role.name == 'member'))
    invitation = await service.issue_invitation(
        admin.principal.account_id, EMAIL, org.id, role_id
    )
    token = invitation_token(invitation)
    with pytest.raises(NativeAuthError, match='invited account'):
        await sso(
            service, email='other@example.test', allow_jit=False, invitation_token=token
        )
    login = await sso(service, allow_jit=False, invitation_token=token)
    async with async_session_maker() as session:
        member = await session.get(OrgMember, (org.id, login.principal.account_id))
        assert present(member).role_id == role_id
        invite = await session.get(AccountInvitation, UUID(invitation['invitation_id']))
        assert present(invite).accepted_account_id == login.principal.account_id
        assert present(invite).consumed_at is not None
        assert await session.get(PasswordCredential, login.principal.account_id) is None


async def test_existing_sso_invitation_and_reset_never_create_password(
    federated: NativeRuntime, async_session_maker: SessionFactory
) -> None:
    service, admin, _ = federated
    login = await sso(service)
    invitation = await service.issue_invitation(admin.principal.account_id, EMAIL)
    token = invitation_token(invitation)
    inspected = await service.inspect_invitation(token)
    assert inspected['action'] == 'login'
    assert inspected['authentication_methods'] == ['saml']
    assert await service.complete_enrollment(token, PASSWORD, client_ip='test') is None
    await service.accept_membership(login.principal.account_id, token)
    with pytest.raises(NativeAuthError, match='no password to reset'):
        await service.issue_password_reset(
            admin.principal.account_id, login.principal.account_id, admin.token
        )
    with pytest.raises(NativeAuthError, match='no password to change'):
        await service.change_password(
            login.principal.account_id,
            login.token,
            PASSWORD,
            PASSWORD,
            client_ip='test',
        )
    with pytest.raises(NativeAuthError):
        await service.recover_password(login.principal.account_id, PASSWORD)
    async with async_session_maker() as session:
        assert await session.get(PasswordCredential, login.principal.account_id) is None
        assert (
            present(await session.get(AuthAccount, login.principal.account_id))
        ).display_email == EMAIL


async def test_password_email_collision_requires_explicit_link(
    federated: NativeRuntime, async_session_maker: SessionFactory
) -> None:
    service, admin, _ = federated
    with pytest.raises(NativeAuthError, match='existing account'):
        await sso(service, email=admin.principal.email)
    async with async_session_maker() as session:
        credential = await session.get(PasswordCredential, admin.principal.account_id)
        old_hash = present(credential).password_hash
    linked = await sso(
        service,
        email=admin.principal.email,
        link_account_id=admin.principal.account_id,
        link_session_id=admin.principal.session_id,
        link_session_token=admin.token,
    )
    assert linked.principal.account_id == admin.principal.account_id
    assert await service.authenticate_session(admin.token) is None
    assert await service.authenticate_session(linked.token) is not None
    assert (await service.profile_metadata(admin.principal.account_id))[
        'authentication_methods'
    ] == ['password', 'saml']
    password_login = await service.login(
        admin.principal.email, PASSWORD, client_ip='test'
    )
    assert password_login.principal.account_id == linked.principal.account_id
    assert (
        await sso(service, email=admin.principal.email)
    ).principal.account_id == linked.principal.account_id
    async with async_session_maker() as session:
        credential = await session.get(PasswordCredential, admin.principal.account_id)
        assert present(credential).password_hash == old_hash


async def test_external_subject_already_owned_cannot_be_linked(
    federated: NativeRuntime,
) -> None:
    service, admin, _ = federated
    await sso(service)
    with pytest.raises(NativeAuthError, match='already assigned'):
        await sso(
            service,
            email=admin.principal.email,
            link_account_id=admin.principal.account_id,
            link_session_id=admin.principal.session_id,
            link_session_token=admin.token,
        )
    assert (await service.profile_metadata(admin.principal.account_id))[
        'authentication_methods'
    ] == ['password']


async def test_sso_only_inviter_and_recipient_accept_org_invitation(
    federated: NativeRuntime,
    async_session_maker: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = federated
    inviter = await sso(service)
    recipient = await sso(service, subject='recipient', email='recipient@example.test')
    monkeypatch.setattr(database, 'a_session_maker', async_session_maker)
    async with async_session_maker() as session, session.begin():
        await set_superadmin(session, inviter.principal.account_id, True)
        org = Org(id=uuid4(), name='Team with SSO owners')
        session.add(org)
        await session.flush()
        role_id = await session.scalar(select(Role.id).where(Role.name == 'member'))
        session.add(
            OrgInvitation(
                token='sso-invitation',
                org_id=org.id,
                email='recipient@example.test',
                role_id=role_id,
                inviter_id=inviter.principal.account_id,
                status='pending',
                expires_at=datetime.now() + timedelta(days=1),
            )
        )
    accepted = await OrgInvitationService._accept_native_invitation(
        'sso-invitation', recipient.principal.account_id
    )
    assert accepted.accepted_by_user_id == recipient.principal.account_id
    async with async_session_maker() as session:
        assert (
            await session.get(OrgMember, (org.id, recipient.principal.account_id))
            is not None
        )


@pytest.mark.parametrize(
    'proof',
    ['missing', 'stale', 'wrong_session', 'wrong_account', 'revoked', 'disabled'],
)
async def test_link_rechecks_original_recent_browser(
    federated: NativeRuntime, async_session_maker: SessionFactory, proof: str
) -> None:
    service, admin, _ = federated
    token: str | None = admin.token
    account_id = admin.principal.account_id
    session_id = admin.principal.session_id
    if proof == 'missing':
        token = None
    elif proof == 'wrong_session':
        session_id = uuid4()
    elif proof == 'wrong_account':
        account_id = uuid4()
    else:
        async with async_session_maker() as session, session.begin():
            browser = await session.get(BrowserSession, session_id)
            if proof == 'stale':
                present(browser).auth_time = datetime.now(UTC) - timedelta(days=1)
            elif proof == 'revoked':
                present(browser).revoked_at = datetime.now(UTC)
            else:
                (present(await session.get(User, account_id))).is_disabled = True
    with pytest.raises(NativeAuthError):
        await sso(
            service,
            email=admin.principal.email,
            link_account_id=account_id,
            link_session_id=session_id,
            link_session_token=token,
        )
    async with async_session_maker() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ExternalIdentity))
            == 0
        )


async def test_same_subject_email_change_and_new_subject_collision_do_not_merge(
    federated: NativeRuntime, async_session_maker: SessionFactory
) -> None:
    service, _, _ = federated
    login = await sso(service)
    with pytest.raises(NativeAuthError, match='does not match'):
        await sso(service, email='renamed@example.test')
    with pytest.raises(NativeAuthError, match='existing account'):
        await sso(service, subject='second-subject')
    async with async_session_maker() as session:
        account = await session.get(AuthAccount, login.principal.account_id)
        assert present(account).display_email == EMAIL
        assert (
            await session.scalar(select(func.count()).select_from(ExternalIdentity))
            == 1
        )


async def test_concurrent_jit_and_password_enrollment_have_one_account(
    federated: NativeRuntime, async_session_maker: SessionFactory
) -> None:
    service, admin, _ = federated
    invitation = await service.issue_invitation(admin.principal.account_id, EMAIL)
    results = await asyncio.gather(
        sso(service),
        service.complete_enrollment(
            invitation_token(invitation), PASSWORD, client_ip='test'
        ),
        return_exceptions=True,
    )
    assert any(
        result is not None and not isinstance(result, Exception) for result in results
    )
    async with async_session_maker() as session:
        ids = list(
            await session.scalars(
                select(AuthAccount.id).where(
                    AuthAccount.normalized_email == EMAIL.lower()
                )
            )
        )
        assert len(ids) == 1
        credential = await session.get(PasswordCredential, ids[0])
        identities = list(
            await session.scalars(
                select(ExternalIdentity).where(ExternalIdentity.account_id == ids[0])
            )
        )
        assert (credential is None and len(identities) == 1) or (
            credential is not None and not identities
        )


async def test_federated_last_admin_and_disabled_connection(
    federated: NativeRuntime,
    async_session_maker: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, admin, _ = federated
    login = await sso(service)
    async with async_session_maker() as session, session.begin():
        await set_superadmin(session, login.principal.account_id, True)
        await require_active_admin(session, login.principal.account_id)
    monkeypatch.setattr(saml_config, 'get_saml_settings', lambda: None)
    assert await service.authenticate_session(login.token) is None
    assert await service.get_identity(login.principal.account_id) is None
    with pytest.raises(NativeAuthError, match='last active superadmin'):
        async with async_session_maker() as session, session.begin():
            await set_superadmin(session, admin.principal.account_id, False)
    monkeypatch.setattr(
        saml_config,
        'get_saml_settings',
        lambda: ConfiguredSamlIdentity(connection_id=CONNECTION, issuer=ISSUER),
    )
    async with async_session_maker() as session, session.begin():
        await set_superadmin(session, admin.principal.account_id, False)
    with pytest.raises(NativeAuthError, match='last active superadmin'):
        async with async_session_maker() as session, session.begin():
            await set_account_enabled(session, login.principal.account_id, False)


async def test_session_retains_exact_identity_when_connection_replaced(
    federated: NativeRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> None:
    service, admin, _ = federated
    login = await sso(service)
    monkeypatch.setattr(
        saml_config,
        'get_saml_settings',
        lambda: ConfiguredSamlIdentity(connection_id='replacement', issuer=ISSUER),
    )
    assert await service.authenticate_session(login.token) is None
    assert await service.get_identity(login.principal.account_id) is None
    assert await service.authenticate_session(admin.token) is not None
    # A second identity makes the account eligible through the replacement
    # connection; it must not make the old connection's browser proof valid.
    async with async_session_maker() as session, session.begin():
        session.add(
            ExternalIdentity(
                account_id=login.principal.account_id,
                auth_method='saml',
                connection_id='replacement',
                issuer=ISSUER,
                subject='replacement-subject',
            )
        )
    assert await service.get_identity(login.principal.account_id) is not None
    assert await service.authenticate_session(login.token) is None
    replacement = await service.complete_federated_login(
        connection_id='replacement',
        issuer=ISSUER,
        subject='replacement-subject',
        email=EMAIL,
    )
    assert await service.authenticate_session(replacement.token) is not None


async def test_admission_rules_apply_to_sso_and_existing_sessions(
    federated: NativeRuntime, async_session_maker: SessionFactory
) -> None:
    service, _, _ = federated
    login = await sso(service)
    async with async_session_maker() as session, session.begin():
        session.add(
            UserAuthorization(
                email_pattern='%@example.test', provider_type='saml', type='blacklist'
            )
        )
    assert await service.authenticate_session(login.token) is None
    assert await service.get_identity(login.principal.account_id) is None
    with pytest.raises(NativeAuthError, match='admission is denied'):
        await sso(service)
    with pytest.raises(NativeAuthError, match='admission is denied'):
        await sso(service, subject='blocked-new-subject', email='blocked@example.test')


async def test_terminal_subject_cannot_revive_or_claim_new_invitation(
    federated: NativeRuntime, async_session_maker: SessionFactory
) -> None:
    service, admin, _ = federated
    login = await sso(service)
    old_invite = await service.issue_invitation(admin.principal.account_id, EMAIL)
    async with async_session_maker() as session, session.begin():
        await tombstone_account(session, login.principal.account_id)
    assert await service.authenticate_session(login.token) is None
    with pytest.raises(NativeAuthError, match='Invalid or expired'):
        await service.inspect_invitation(invitation_token(old_invite))
    invitation = await service.issue_invitation(admin.principal.account_id, EMAIL)
    with pytest.raises(NativeAuthError, match='deleted account'):
        await sso(service, invitation_token=invitation_token(invitation))
    replacement = await sso(
        service,
        subject='replacement-subject',
        invitation_token=invitation_token(invitation),
    )
    assert replacement.principal.account_id != login.principal.account_id
    async with async_session_maker() as session:
        assert (
            present(await session.get(AuthAccount, login.principal.account_id))
        ).display_email == EMAIL
        assert (
            await session.scalar(select(func.count()).select_from(ExternalIdentity))
            == 2
        )


async def test_self_deleted_sso_reonboards_same_uuid_without_global_role(
    federated: NativeRuntime, async_session_maker: SessionFactory
) -> None:
    service, _, _ = federated
    login = await sso(service)
    account_id = login.principal.account_id
    async with async_session_maker() as session, session.begin():
        await set_superadmin(session, account_id, True)
        await mark_self_deleted(session, account_id)
        await session.execute(delete(OrgMember).where(OrgMember.user_id == account_id))
        await session.execute(delete(User).where(User.id == account_id))
        await session.execute(delete(Org).where(Org.id == account_id))
    assert await service.get_identity(account_id) is None
    again = await sso(service)
    assert again.principal.account_id == account_id
    async with async_session_maker() as session:
        assert (present(await session.get(User, account_id))).role_id is None
        assert await session.get(PasswordCredential, account_id) is None


async def test_federation_upgrade_preserves_authoritative_account_email_and_roles(
    federated: NativeRuntime, async_session_maker: SessionFactory, engine: Engine
) -> None:
    _, admin, _ = federated
    account_id = admin.principal.account_id
    async with async_session_maker() as session, session.begin():
        credential = await session.get(PasswordCredential, account_id)
        old_hash = present(credential).password_hash
        old_version = present(credential).credential_version
        user = await session.get(User, account_id)
        role_id = present(user).role_id
        present(user).email = 'disposable-profile@example.test'
    migration = importlib.import_module(
        'migrations.versions.164_native_federated_identity'
    )
    tombstone = uuid4()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            connection.execute(
                text(
                    'INSERT INTO auth_account (id, state, session_version, provisioning_status, created_at, updated_at) '
                    "VALUES (:id, 'deleted', 2, 'complete', now(), now())"
                ),
                {'id': tombstone},
            )
            migration.upgrade()
    async with async_session_maker() as session:
        account = await session.get(AuthAccount, account_id)
        credential = await session.get(PasswordCredential, account_id)
        assert present(account).display_email == admin.principal.email
        assert present(account).normalized_email == admin.principal.email.lower()
        assert present(credential).password_hash == old_hash
        assert present(credential).credential_version == old_version
        assert (present(await session.get(User, account_id))).role_id == role_id
        assert (
            present(await session.get(AuthAccount, tombstone))
        ).normalized_email is None


async def test_federation_downgrade_refuses_existing_identities(
    federated: NativeRuntime, engine: Engine
) -> None:
    service, _, _ = federated
    await sso(service)
    migration = importlib.import_module(
        'migrations.versions.164_native_federated_identity'
    )
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match='external identities exist'):
                migration.downgrade()
