"""Transaction-taking local profile and lifecycle operations.

Acquire the installation lock before any account, membership, or profile lock.
No helper commits or calls an identity, billing, or LLM service.
"""

import secrets
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.native_password import NativeAuthError
from server.constants import (
    ORG_SETTINGS_VERSION,
    get_default_llm_api_key,
    should_use_direct_llm_defaults,
)
from storage.api_key import ApiKey
from storage.native_auth import (
    AccountInvitation,
    AuthAccount,
    AuthChallenge,
    BrowserSession,
    PasswordCredential,
)
from storage.org import Org
from storage.org_default_settings import create_base_org_agent_settings
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User

# Common to bootstrap, grant/revoke, disable/delete, reonboarding and recovery.
NATIVE_LIFECYCLE_LOCK = 724408037958003618


async def lock_native_lifecycle(session: AsyncSession) -> None:
    await session.execute(
        text('SELECT pg_advisory_xact_lock(:key)'), {'key': NATIVE_LIFECYCLE_LOCK}
    )


async def require_active_admin(session: AsyncSession, account_id: UUID) -> User:
    user = await session.scalar(
        select(User)
        .join(AuthAccount, AuthAccount.id == User.id)
        .join(Role, Role.id == User.role_id)
        .where(
            User.id == account_id,
            User.is_disabled.is_(False),
            AuthAccount.state == 'profile_present',
            Role.name == 'admin',
        )
    )
    if user is None:
        raise NativeAuthError('Global user management permission required', 403)
    account = await session.get(AuthAccount, account_id)
    if account is None or not await account_admitted(session, account):
        raise NativeAuthError('Global user management permission required', 403)
    return user


async def email_admitted(
    session: AsyncSession, email: str, auth_method: str = 'password'
) -> bool:
    """Native admission uses the authoritative email without alias rewriting."""
    from storage.user_authorization import UserAuthorizationType
    from storage.user_authorization_store import UserAuthorizationStore

    policy = await UserAuthorizationStore.get_authorization_type(
        email, auth_method, session
    )
    return policy != UserAuthorizationType.BLACKLIST


async def account_admitted(session: AsyncSession, account: AuthAccount) -> bool:
    """Non-browser authority requires an admitted password credential."""
    if (
        account.normalized_email is None
        or await session.get(PasswordCredential, account.id) is None
    ):
        return False
    return await email_admitted(session, account.normalized_email)


async def session_method_admitted(
    session: AsyncSession, account: AuthAccount, browser: BrowserSession
) -> bool:
    if account.normalized_email is None or browser.auth_method != 'password':
        return False
    credential = await session.get(PasswordCredential, account.id)
    if (
        credential is None
        or credential.credential_version != browser.credential_version
    ):
        return False
    return await email_admitted(session, account.normalized_email)


async def _guard_last_admin(session: AsyncSession, account_id: UUID) -> None:
    admin_query = (
        select(AuthAccount)
        .join(AuthAccount, AuthAccount.id == User.id)
        .select_from(User)
        .join(Role, Role.id == User.role_id)
        .where(
            User.is_disabled.is_(False),
            AuthAccount.state == 'profile_present',
            Role.name == 'admin',
        )
    )
    ids = []
    for account in await session.scalars(admin_query):
        if await account_admitted(session, account):
            ids.append(account.id)
    if account_id in ids and len(ids) <= 1:
        raise NativeAuthError('Cannot remove the last active superadmin', 409)


async def revoke_account_security(session: AsyncSession, account_id: UUID) -> None:
    await lock_native_lifecycle(session)
    now = datetime.now(UTC)
    await session.execute(
        update(AuthAccount)
        .where(AuthAccount.id == account_id)
        .values(session_version=AuthAccount.session_version + 1)
    )
    await session.execute(
        update(BrowserSession)
        .where(
            BrowserSession.account_id == account_id, BrowserSession.revoked_at.is_(None)
        )
        .values(revoked_at=now)
    )
    await session.execute(
        update(AuthChallenge)
        .where(
            AuthChallenge.account_id == account_id, AuthChallenge.revoked_at.is_(None)
        )
        .values(revoked_at=now)
    )


async def _account(session: AsyncSession, account_id: UUID) -> AuthAccount:
    account = await session.get(AuthAccount, account_id, with_for_update=True)
    if account is None or account.state == 'deleted':
        raise NativeAuthError('Account not found', 404)
    return account


async def mark_self_deleted(session: AsyncSession, account_id: UUID) -> None:
    await lock_native_lifecycle(session)
    account = await _account(session, account_id)
    await _guard_last_admin(session, account_id)
    user = await session.get(User, account_id)
    if user is None or user.is_disabled or account.state != 'profile_present':
        raise NativeAuthError('Account is not eligible for self-deletion', 403)
    account.state = 'reonboardable'
    await revoke_account_security(session, account_id)


async def set_account_enabled(
    session: AsyncSession, account_id: UUID, enabled: bool
) -> None:
    await lock_native_lifecycle(session)
    account = await _account(session, account_id)
    if not enabled:
        await _guard_last_admin(session, account_id)
    user = await session.get(User, account_id, with_for_update=True)
    if user is not None and account.state == 'profile_present':
        user.is_disabled = not enabled
        if enabled:
            from storage.native_external_work import NativeExternalWork

            await session.execute(
                update(NativeExternalWork)
                .where(
                    NativeExternalWork.account_id == account_id,
                    NativeExternalWork.kind == 'provision',
                    NativeExternalWork.status == 'suspended',
                )
                .values(status='pending')
            )
        if not enabled:
            from storage.native_external_work import NativeExternalWork

            members = await session.scalars(
                select(OrgMember).where(OrgMember.user_id == account_id)
            )
            for member in members:
                if (
                    member.native_provisioning_id is None
                    or member.has_custom_llm_api_key
                ):
                    continue
                work = await session.get(
                    NativeExternalWork, member.native_provisioning_id
                )
                if work is not None:
                    work.status = 'cleanup'
                    member.llm_api_key = SecretStr('')
                    member.status = 'pending_llm_provisioning'
                    account.provisioning_status = 'pending'
    elif account.state in ('reonboardable', 'profile_absent_blocked') and user is None:
        account.state = 'reonboardable' if enabled else 'profile_absent_blocked'
    else:
        raise NativeAuthError('Account profile state is inconsistent', 409)
    await revoke_account_security(session, account_id)


async def tombstone_account(session: AsyncSession, account_id: UUID) -> None:
    await lock_native_lifecycle(session)
    account = await session.get(AuthAccount, account_id, with_for_update=True)
    if account is None:
        raise NativeAuthError('Account not found', 404)
    if account.state == 'deleted':
        return
    await _guard_last_admin(session, account_id)
    account.state = 'deleted'
    await revoke_account_security(session, account_id)
    if account.normalized_email is not None:
        # Earlier setup links may reserve another UUID for this same email.
        # Terminal deletion invalidates every pre-deletion admission link.
        await session.execute(
            update(AccountInvitation)
            .where(
                AccountInvitation.normalized_email == account.normalized_email,
                AccountInvitation.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
    await session.execute(
        delete(PasswordCredential).where(PasswordCredential.account_id == account_id)
    )
    # API key user ids are historical strings, not foreign keys to User.
    await session.execute(delete(ApiKey).where(ApiKey.user_id == str(account_id)))


async def set_superadmin(
    session: AsyncSession, account_id: UUID, enabled: bool
) -> None:
    await lock_native_lifecycle(session)
    account = await _account(session, account_id)
    user = await session.get(User, account_id, with_for_update=True)
    if account.state != 'profile_present' or user is None:
        raise NativeAuthError('Account has no active profile', 409)
    if not enabled:
        await _guard_last_admin(session, account_id)
    role = await session.scalar(select(Role).where(Role.name == 'admin'))
    if role is None:
        raise NativeAuthError('Required admin role is missing', 503)
    user.role_id = role.id if enabled else None


async def add_membership(
    session: AsyncSession, user: User, org: Org, role_id: int
) -> None:
    if await session.get(OrgMember, (org.id, user.id)) is not None:
        return  # A setup link never overwrites an established membership role.
    managed = not should_use_direct_llm_defaults()
    member = OrgMember(
        org_id=org.id,
        user_id=user.id,
        role_id=role_id,
        llm_api_key=get_default_llm_api_key() or '',
        status='pending_llm_provisioning' if managed else 'active',
        managed_llm_key_ownership_version=0 if managed else 1,
    )
    session.add(member)
    if managed:
        from storage.native_external_work import NativeExternalWork

        work_id = uuid4()
        member.native_provisioning_id = work_id
        session.add(
            NativeExternalWork(
                id=work_id,
                account_id=user.id,
                org_id=org.id,
                kind='provision',
                payload={'member_key': 'sk-' + secrets.token_hex(32)},
            )
        )
        account = await session.get(AuthAccount, user.id)
        if account is not None:
            account.provisioning_status = 'pending'
    await session.flush()


async def create_profile(
    session: AsyncSession,
    account: AuthAccount,
    email: str,
    *,
    superadmin: bool = False,
    auth_method: str = 'password',
) -> User:
    """Create minimal, usable settings without implicit first-user promotion."""
    from storage.default_org_service import get_default_org_config
    from storage.native_external_work import NativeExternalWork

    if not await email_admitted(session, email, auth_method):
        raise NativeAuthError('Account admission is denied', 403)
    cleanup = await session.scalar(
        select(NativeExternalWork.id)
        .where(
            NativeExternalWork.kind.in_(('delete_user', 'delete_team')),
            (NativeExternalWork.account_id == account.id)
            | (NativeExternalWork.org_id == account.id),
            NativeExternalWork.status != 'complete',
        )
        .limit(1)
    )
    if cleanup is not None:
        raise NativeAuthError(
            'Account cleanup is pending; try signing in again shortly', 503
        )

    owner = await session.scalar(select(Role).where(Role.name == 'owner'))
    admin = (
        await session.scalar(select(Role).where(Role.name == 'admin'))
        if superadmin
        else None
    )
    if owner is None or (superadmin and admin is None):
        raise NativeAuthError('Required application roles are missing', 503)
    if (
        await session.get(User, account.id) is not None
        or await session.get(Org, account.id) is not None
    ):
        raise NativeAuthError('Account profile already exists', 409)
    org = Org(
        id=account.id,
        name=f'user_{account.id}_org',
        contact_name=email,
        contact_email=email,
        org_version=ORG_SETTINGS_VERSION,
        v1_enabled=True,
        agent_settings=create_base_org_agent_settings(),
        llm_api_key=get_default_llm_api_key(),
    )
    session.add(org)
    await session.flush()
    user = User(
        id=account.id,
        current_org_id=org.id,
        role_id=admin.id if admin else None,
        email=email,
        email_verified=False,
        is_disabled=False,
        language='en',
        enable_sound_notifications=False,
        user_consents_to_analytics=False,
    )
    session.add(user)
    await session.flush()
    await add_membership(session, user, org, owner.id)
    account.state = 'profile_present'
    account.provisioning_status = (
        'pending' if not should_use_direct_llm_defaults() else 'complete'
    )
    config = get_default_org_config()
    if config.enabled:
        default_org = await session.scalar(select(Org).where(Org.is_default.is_(True)))
        if default_org is None and superadmin:
            default_org = Org(
                id=uuid4(),
                name=config.org_name,
                contact_name=email,
                contact_email=email,
                is_default=True,
                org_version=ORG_SETTINGS_VERSION,
                v1_enabled=True,
                agent_settings=create_base_org_agent_settings(),
                llm_api_key=get_default_llm_api_key(),
            )
            session.add(default_org)
            await session.flush()
            await add_membership(session, user, default_org, owner.id)
            user.current_org_id = default_org.id
        elif default_org is not None and config.auto_add_users:
            member_role = await session.scalar(
                select(Role).where(Role.name == 'member')
            )
            if member_role is None:
                raise NativeAuthError('Required member role is missing', 503)
            await add_membership(session, user, default_org, member_role.id)
            user.current_org_id = default_org.id
    return user
