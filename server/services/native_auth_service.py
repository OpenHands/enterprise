"""Local identity, opaque sessions, enrollment and password recovery."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from hmac import compare_digest
from urllib.parse import quote, unquote
from uuid import UUID, uuid4

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.auth_config import get_native_auth_settings
from server.auth.authorization import Permission, has_permission
from server.auth.native_password import (
    HASHER,
    NativeAuthError,
    hash_password,
    normalize_email,
    verify_password,
)
from server.auth.native_session import csrf_for_token, digest_token, new_token
from server.auth.native_types import (
    InvitationInspection,
    InvitationLink,
    InvitationMetadata,
    InvitationOrganizationPage,
    InvitationPage,
    NativeAccountMetadata,
    NativeAccountPage,
    NativeProfileMetadata,
    PasswordResetLink,
    SessionFactory,
)
from server.constants import DEPLOYMENT_MODE
from server.services.native_account_service import (
    add_membership,
    create_profile,
    lock_native_lifecycle,
    require_active_admin,
    revoke_account_security,
)
from storage.database import a_session_maker
from storage.native_auth import (
    AccountInvitation,
    AuthAccount,
    AuthChallenge,
    AuthThrottle,
    BrowserSession,
    ExternalIdentity,
    PasswordCredential,
)
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User

SessionRow = tuple[BrowserSession, AuthAccount, PasswordCredential | None, User]


@dataclass(frozen=True)
class NativePrincipal:
    account_id: UUID
    email: str
    session_id: UUID | None = None
    auth_time: datetime | None = None
    auth_method: str | None = None


@dataclass(frozen=True)
class NativeLogin:
    principal: NativePrincipal
    token: str = field(repr=False)
    redirect_to: str = '/'


def _now() -> datetime:
    return datetime.now(UTC)


def _valid_token(token: str | None) -> bool:
    return bool(token and 32 <= len(token) <= 128 and token.isascii())


def safe_return_path(path: str | None) -> str:
    if not path or len(path) > 2048:
        return '/'
    decoded = unquote(path)
    if (
        not decoded.startswith('/')
        or decoded.startswith('//')
        or '\\' in decoded
        or any(ord(c) < 32 for c in decoded)
    ):
        return '/'
    return path


async def _redirect(
    session: AsyncSession, user: User, return_path: str | None = None
) -> str:
    path = safe_return_path(return_path)
    if user.accepted_tos is None:
        return f'/accept-tos?redirect_url={quote(path, safe="")}'
    if user.onboarding_completed is False:
        first_owner = await session.scalar(
            select(User.id)
            .join(OrgMember, OrgMember.user_id == User.id)
            .join(Role, Role.id == OrgMember.role_id)
            .where(
                OrgMember.org_id == user.current_org_id,
                Role.name == 'owner',
                User.accepted_tos.is_not(None),
            )
            .order_by(User.accepted_tos)
            .limit(1)
        )
        if DEPLOYMENT_MODE == 'cloud' or (
            DEPLOYMENT_MODE == 'self_hosted' and first_owner == user.id
        ):
            return f'/onboarding?returnTo={quote(path, safe="")}'
    return path


class NativeAuthService:
    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self.sessions = session_factory or a_session_maker

    async def throttle(
        self, namespace: str, client_ip: str, email: str | None = None
    ) -> None:
        """Commit limits before credential verification, including failed attempts."""
        now = _now()
        cutoff = now - timedelta(seconds=300)
        keys = [(f'{namespace}:global', 1000), (f'{namespace}:ip:{client_ip}', 50)]
        if email is not None:
            keys.append((f'{namespace}:account:{email}', 10))
        blocked = False
        try:
            async with self.sessions() as session, session.begin():
                # Stale counters do not accumulate, even without a running cron job.
                await session.execute(
                    delete(AuthThrottle).where(AuthThrottle.window_start < cutoff)
                )
                for key, limit in keys:
                    stmt = insert(AuthThrottle).values(
                        key_digest=digest_token(key, 'throttle'),
                        window_start=now,
                        attempts=1,
                    )
                    counter_stmt = stmt.on_conflict_do_update(
                        index_elements=[AuthThrottle.key_digest],
                        set_={
                            'attempts': case(
                                (AuthThrottle.window_start < cutoff, 1),
                                else_=AuthThrottle.attempts + 1,
                            ),
                            'window_start': case(
                                (AuthThrottle.window_start < cutoff, now),
                                else_=AuthThrottle.window_start,
                            ),
                        },
                    ).returning(AuthThrottle.attempts)
                    attempts = await session.scalar(counter_stmt)
                    blocked = blocked or bool(attempts and attempts > limit)
        except SQLAlchemyError as exc:
            raise NativeAuthError(
                'Authentication temporarily unavailable', 503
            ) from exc
        if blocked:
            raise NativeAuthError(
                'Too many authentication attempts; try again later', 429
            )

    async def get_identity(self, account_id: UUID) -> NativePrincipal | None:
        from server.services.native_account_service import account_admitted

        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(AuthAccount, User)
                    .join(User, User.id == AuthAccount.id)
                    .where(AuthAccount.id == account_id)
                )
            ).first()
            if row is None or row[0].state != 'profile_present' or row[1].is_disabled:
                return None
            if row[0].display_email is None or not await account_admitted(
                session, row[0]
            ):
                return None
            return NativePrincipal(account_id=account_id, email=row[0].display_email)

    async def _session(
        self, session: AsyncSession, token: str, *, renew: bool = False
    ) -> SessionRow | None:
        from server.services.native_account_service import session_method_admitted

        if not _valid_token(token):
            return None
        row = (
            await session.execute(
                select(BrowserSession, AuthAccount, PasswordCredential, User)
                .join(AuthAccount, AuthAccount.id == BrowserSession.account_id)
                .outerjoin(
                    PasswordCredential, PasswordCredential.account_id == AuthAccount.id
                )
                .join(User, User.id == AuthAccount.id)
                .where(BrowserSession.token_digest == digest_token(token, 'session'))
                .with_for_update(of=BrowserSession)
            )
        ).first()
        if row is None:
            return None
        browser, account, credential, user = row
        now = _now()
        if (
            browser.revoked_at is not None
            or browser.idle_expires_at <= now
            or browser.absolute_expires_at <= now
            or account.state != 'profile_present'
            or user.is_disabled
            or browser.session_version != account.session_version
            or account.normalized_email is None
            or account.display_email is None
            or not await session_method_admitted(session, account, browser)
        ):
            return None
        if renew:
            browser.idle_expires_at = min(
                now + timedelta(seconds=get_native_auth_settings().idle_seconds),
                browser.absolute_expires_at,
            )
        return browser, account, credential, user

    async def authenticate_session(self, token: str) -> NativePrincipal | None:
        async with self.sessions() as session, session.begin():
            row = await self._session(session, token, renew=True)
            if row is None:
                return None
            browser, account, _, _ = row
            if account.display_email is None:
                return None
            return NativePrincipal(
                account.id,
                account.display_email,
                browser.id,
                browser.auth_time,
                browser.auth_method,
            )

    async def revoke_session(self, token: str) -> None:
        if not _valid_token(token):
            return
        async with self.sessions() as session, session.begin():
            browser = await session.scalar(
                select(BrowserSession)
                .where(BrowserSession.token_digest == digest_token(token, 'session'))
                .with_for_update()
            )
            if browser is not None:
                browser.revoked_at = _now()

    async def _new_session(
        self,
        session: AsyncSession,
        account: AuthAccount,
        credential: PasswordCredential | None,
        user: User,
        return_path: str | None = None,
        *,
        auth_method: str = 'password',
        external_identity: ExternalIdentity | None = None,
        auth_time: datetime | None = None,
        session_expiry_bound: datetime | None = None,
    ) -> NativeLogin:
        if account.display_email is None:
            raise NativeAuthError('Account metadata is unavailable', 409)
        now, token = _now(), new_token()
        config = get_native_auth_settings()
        authenticated_at = auth_time or now
        if authenticated_at.tzinfo is None or authenticated_at > now + timedelta(
            seconds=60
        ):
            raise NativeAuthError('Invalid authentication time', 400)
        absolute_expiry = now + timedelta(seconds=config.absolute_seconds)
        if session_expiry_bound is not None:
            if session_expiry_bound.tzinfo is None or session_expiry_bound <= now:
                raise NativeAuthError('Authentication has expired', 401)
            absolute_expiry = min(absolute_expiry, session_expiry_bound)
        browser = BrowserSession(
            id=uuid4(),
            token_digest=digest_token(token, 'session'),
            account_id=account.id,
            session_version=account.session_version,
            credential_version=credential.credential_version if credential else 0,
            auth_method=auth_method,
            external_identity_id=external_identity.id if external_identity else None,
            auth_time=authenticated_at,
            idle_expires_at=min(
                now + timedelta(seconds=config.idle_seconds), absolute_expiry
            ),
            absolute_expires_at=absolute_expiry,
        )
        session.add(browser)
        user.first_login_at = user.first_login_at or now.replace(tzinfo=None)
        user.last_login_at = now.replace(tzinfo=None)
        await session.flush()
        return NativeLogin(
            NativePrincipal(
                account.id,
                account.display_email,
                browser.id,
                authenticated_at,
                auth_method,
            ),
            token,
            await _redirect(session, user, return_path),
        )

    async def login(
        self,
        email: str,
        password: str,
        *,
        client_ip: str,
        return_path: str | None = None,
    ) -> NativeLogin:
        try:
            normalized = normalize_email(email)
        except NativeAuthError:
            normalized = 'invalid-email'
        await self.throttle('login', client_ip, normalized)
        async with self.sessions() as session:
            credential = await session.scalar(
                select(PasswordCredential).where(
                    PasswordCredential.normalized_login_email == normalized
                )
            )
            known_hash = credential.password_hash if credential else None
        verified = await verify_password(known_hash, password)
        if not verified or known_hash is None:
            raise NativeAuthError('Invalid email or password', 401)
        replacement = (
            await hash_password(password)
            if HASHER.check_needs_rehash(known_hash)
            else None
        )
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            credential = await session.scalar(
                select(PasswordCredential)
                .where(PasswordCredential.normalized_login_email == normalized)
                .with_for_update()
            )
            if credential is None or credential.password_hash != known_hash:
                raise NativeAuthError('Invalid email or password', 401)
            from server.services.native_account_service import email_admitted

            account = await session.get(
                AuthAccount, credential.account_id, with_for_update=True
            )
            user = await session.get(User, credential.account_id)
            if account is None or account.state not in (
                'profile_present',
                'reonboardable',
            ):
                raise NativeAuthError('Invalid email or password', 401)
            if (
                account.normalized_email is None
                or account.display_email is None
                or not await email_admitted(session, account.normalized_email)
            ):
                raise NativeAuthError('Invalid email or password', 401)
            if account.state == 'reonboardable' and user is None:
                user = await create_profile(session, account, account.display_email)
            elif account.state != 'profile_present' or user is None or user.is_disabled:
                raise NativeAuthError('Invalid email or password', 401)
            if replacement:
                credential.password_hash = replacement
            return await self._new_session(
                session, account, credential, user, return_path
            )

    async def complete_federated_login(
        self,
        *,
        connection_id: str,
        issuer: str,
        subject: str,
        email: str,
        return_path: str | None = None,
        invitation_token: str | None = None,
        link_account_id: UUID | None = None,
        link_session_id: UUID | None = None,
        link_session_token: str | None = None,
        allow_jit: bool = False,
        auth_method: str = 'saml',
        auth_time: datetime | None = None,
        session_expiry_bound: datetime | None = None,
    ) -> NativeLogin:
        """Complete a trusted adapter's verified assertion in one transaction.

        The protocol adapter validates the response and consumes browser-bound
        state before calling. Email is admission/contact data, never account
        ownership proof. Linking instead requires the exact recent browser
        session that initiated the flow, rechecked under the lifecycle lock.
        """
        from server.services.native_account_service import (
            email_admitted,
            external_identity_configured,
        )

        if auth_method not in ('saml', 'oidc') or any(
            not value or len(value.encode('utf-8')) > limit
            for value, limit in ((connection_id, 128), (issuer, 512), (subject, 512))
        ):
            raise NativeAuthError('Invalid external identity', 400)
        if not external_identity_configured(
            ExternalIdentity(
                connection_id=connection_id,
                issuer=issuer,
                subject=subject,
                auth_method=auth_method,
            )
        ):
            raise NativeAuthError(
                'SSO connection is unavailable', 403, code='unavailable'
            )
        normalized = normalize_email(email)
        linking = any(
            item is not None
            for item in (link_account_id, link_session_id, link_session_token)
        )
        if linking and (
            link_account_id is None
            or link_session_id is None
            or link_session_token is None
            or invitation_token is not None
        ):
            raise NativeAuthError('Invalid identity linking request', 400)
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            identity = await session.scalar(
                select(ExternalIdentity)
                .where(
                    ExternalIdentity.auth_method == auth_method,
                    ExternalIdentity.connection_id == connection_id,
                    ExternalIdentity.issuer == issuer,
                    ExternalIdentity.subject == subject,
                )
                .with_for_update()
            )
            invitation = (
                await self._invitation(session, invitation_token)
                if invitation_token is not None
                else None
            )
            account: AuthAccount | None
            user: User | None
            if linking:
                assert link_account_id is not None and link_session_token is not None
                try:
                    row = await self._recent(
                        session, link_session_token, link_account_id
                    )
                except NativeAuthError as exc:
                    raise NativeAuthError(
                        'Sign in again to link this identity',
                        exc.status_code,
                        code='recent_auth_required',
                    ) from None
                if row[0].id != link_session_id:
                    raise NativeAuthError(
                        'Sign in again to link this identity',
                        401,
                        code='recent_auth_required',
                    )
                account, user = row[1], row[3]
                if identity is not None and identity.account_id != account.id:
                    raise NativeAuthError(
                        'External identity is already assigned', 409, code='unavailable'
                    )
            elif identity is not None:
                account = await session.get(AuthAccount, identity.account_id)
                user = await session.get(User, identity.account_id)
                if account is not None and account.state == 'deleted':
                    raise NativeAuthError(
                        'This SSO identity belongs to a deleted account; contact your administrator',
                        403,
                        code='unavailable',
                    )
                if account is None or account.state not in (
                    'profile_present',
                    'reonboardable',
                ):
                    raise NativeAuthError('Account is unavailable', 403)
            else:
                existing = await session.scalar(
                    select(AuthAccount.id).where(
                        AuthAccount.normalized_email == normalized,
                        AuthAccount.state != 'deleted',
                    )
                )
                if existing is not None:
                    raise NativeAuthError(
                        'Sign in to your existing account and link SSO in account settings',
                        409,
                        code='account_link_required',
                    )
                if invitation is None and not allow_jit:
                    raise NativeAuthError(
                        'An account invitation is required',
                        403,
                        code='invitation_required',
                    )
                if invitation is not None and invitation.normalized_email != normalized:
                    raise NativeAuthError('Sign in as the invited account', 403)
                account = AuthAccount(
                    id=invitation.reserved_account_id if invitation else uuid4(),
                    normalized_email=normalized,
                    display_email=email.strip(),
                )
                session.add(account)
                await session.flush()
                user = None
            if account.normalized_email != normalized:
                raise NativeAuthError(
                    'SSO email does not match this account; contact your administrator',
                    409,
                    code='email_mismatch',
                )
            if (
                account.normalized_email is None
                or account.display_email is None
                or not await email_admitted(
                    session, account.normalized_email, auth_method
                )
                or not await email_admitted(session, normalized, auth_method)
            ):
                raise NativeAuthError('Account admission is denied', 403)
            if user is None and account.state in ('profile_present', 'reonboardable'):
                # Only a new account or explicitly self-deleted profile can be
                # provisioned; an unexplained missing profile stays unavailable.
                if identity is not None and account.state != 'reonboardable':
                    raise NativeAuthError('Account is unavailable', 403)
                user = await create_profile(
                    session, account, account.display_email, auth_method=auth_method
                )
            elif account.state != 'profile_present' or user is None or user.is_disabled:
                raise NativeAuthError('Account is unavailable', 403)
            if identity is None:
                identity = ExternalIdentity(
                    id=uuid4(),
                    account_id=account.id,
                    connection_id=connection_id,
                    issuer=issuer,
                    subject=subject,
                    auth_method=auth_method,
                )
                session.add(identity)
                await session.flush()
            if invitation is not None:
                if account.normalized_email != invitation.normalized_email:
                    raise NativeAuthError('Sign in as the invited account', 403)
                await self._accept(session, invitation, user)
            if linking:
                # The completed identity change invalidates old browser proof,
                # outstanding challenges and concurrent linking transactions.
                await revoke_account_security(session, account.id)
                await session.flush()
                await session.refresh(account)
            return await self._new_session(
                session,
                account,
                None,
                user,
                return_path,
                auth_method=auth_method,
                external_identity=identity,
                auth_time=auth_time,
                session_expiry_bound=session_expiry_bound,
            )

    async def issue_csrf(self, session_token: str | None) -> tuple[str, str | None]:
        if session_token and await self.authenticate_session(session_token) is not None:
            return csrf_for_token(session_token), None
        token = new_token()
        async with self.sessions() as session, session.begin():
            await session.execute(
                delete(AuthChallenge).where(
                    AuthChallenge.expires_at < _now(),
                    AuthChallenge.purpose == 'anonymous_csrf',
                )
            )
            session.add(
                AuthChallenge(
                    token_digest=digest_token(token, 'anonymous_csrf'),
                    purpose='anonymous_csrf',
                    expires_at=_now() + timedelta(minutes=10),
                )
            )
        return csrf_for_token(token), token

    async def validate_csrf(
        self,
        session_token: str | None,
        anonymous_token: str | None,
        csrf_token: str | None,
    ) -> bool:
        if not csrf_token or len(csrf_token) != 64:
            return False
        if session_token:
            return await self.authenticate_session(
                session_token
            ) is not None and compare_digest(csrf_for_token(session_token), csrf_token)
        if not _valid_token(anonymous_token):
            return False
        assert anonymous_token is not None
        if not compare_digest(csrf_for_token(anonymous_token), csrf_token):
            return False
        async with self.sessions() as session:
            challenge = await session.scalar(
                select(AuthChallenge).where(
                    AuthChallenge.token_digest
                    == digest_token(anonymous_token, 'anonymous_csrf'),
                    AuthChallenge.purpose == 'anonymous_csrf',
                )
            )
            return bool(
                challenge
                and challenge.expires_at > _now()
                and challenge.revoked_at is None
                and challenge.consumed_at is None
            )

    async def _scope(
        self,
        session: AsyncSession,
        creator_id: UUID,
        org_id: UUID | None,
        role_id: int | None,
    ) -> Org | None:
        creator = await require_active_admin(session, creator_id)
        if (org_id is None) != (role_id is None):
            raise NativeAuthError(
                'Organization and membership role must be supplied together'
            )
        if org_id is None:
            return None
        org = await session.get(Org, org_id)
        target_role = await session.get(Role, role_id)
        creator_role = await session.scalar(
            select(Role)
            .join(OrgMember, OrgMember.role_id == Role.id)
            .where(OrgMember.user_id == creator_id, OrgMember.org_id == org_id)
        )
        is_org_inviter = bool(creator_role and creator_role.name in ('owner', 'admin'))
        is_super_inviter = False
        # Match organization invitation authority: a global admin can seed a
        # team without joining it. Personal workspaces retain org-scoped rules.
        if not is_org_inviter and await session.get(AuthAccount, org_id) is None:
            super_role = await session.get(Role, creator.role_id)
            is_super_inviter = bool(
                super_role
                and has_permission(
                    super_role,
                    Permission.INVITE_USER_TO_ORGANIZATION,
                    is_super=True,
                )
            )
        if (
            org is None
            or target_role is None
            or target_role.name not in ('owner', 'admin', 'member')
            or not (is_org_inviter or is_super_inviter)
            or (
                target_role.name == 'owner'
                and not (creator_role and creator_role.name == 'owner')
                and not is_super_inviter
            )
        ):
            raise NativeAuthError(
                'Not authorized to grant this organization membership', 403
            )
        return org

    async def list_invitation_organizations(
        self, creator_id: UUID, *, offset: int = 0, limit: int = 100
    ) -> InvitationOrganizationPage:
        """Minimal team choices for global account administration."""
        async with self.sessions() as session:
            creator = await require_active_admin(session, creator_id)
            super_role = await session.get(Role, creator.role_id)
            if not super_role or not has_permission(
                super_role, Permission.INVITE_USER_TO_ORGANIZATION, is_super=True
            ):
                raise NativeAuthError(
                    'Organization invitation permission required', 403
                )
            # Native personal organization IDs are the durable account IDs,
            # including accounts whose application profiles were removed.
            eligible = ~select(AuthAccount.id).where(AuthAccount.id == Org.id).exists()
            total = await session.scalar(
                select(func.count()).select_from(Org).where(eligible)
            )
            rows = await session.execute(
                select(Org.id, Org.name)
                .where(eligible)
                .order_by(func.lower(Org.name), Org.id)
                .offset(offset)
                .limit(limit)
            )
            return {
                'items': [{'id': str(row.id), 'name': row.name} for row in rows],
                'total': total or 0,
            }

    async def issue_invitation(
        self,
        creator_id: UUID,
        email: str,
        org_id: UUID | None = None,
        org_role_id: int | None = None,
    ) -> InvitationLink:
        normalized = normalize_email(email)
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            await self._scope(session, creator_id, org_id, org_role_id)
            existing = await session.scalar(
                select(AuthAccount).where(
                    AuthAccount.normalized_email == normalized,
                    AuthAccount.state != 'deleted',
                )
            )
            token = new_token()
            invitation = AccountInvitation(
                id=uuid4(),
                token_digest=digest_token(token, 'enrollment'),
                reserved_account_id=existing.id if existing else uuid4(),
                normalized_email=normalized,
                display_email=email.strip(),
                creator_account_id=creator_id,
                org_id=org_id,
                org_role_id=org_role_id,
                expires_at=_now()
                + timedelta(seconds=get_native_auth_settings().invitation_seconds),
            )
            session.add(invitation)
            await session.flush()
            return self._invitation_link(invitation, token)

    def _invitation_link(
        self, invitation: AccountInvitation, token: str
    ) -> InvitationLink:
        return {
            'invitation_id': str(invitation.id),
            'invite_url': f'{get_native_auth_settings().app_origin}/account-setup#token={token}',
            'expires_at': invitation.expires_at,
        }

    async def _invitation(self, session: AsyncSession, token: str) -> AccountInvitation:
        if not _valid_token(token):
            raise NativeAuthError('Invalid or expired setup link')
        invitation = await session.scalar(
            select(AccountInvitation)
            .where(AccountInvitation.token_digest == digest_token(token, 'enrollment'))
            .with_for_update()
        )
        if (
            invitation is None
            or invitation.expires_at <= _now()
            or invitation.consumed_at is not None
            or invitation.revoked_at is not None
        ):
            raise NativeAuthError('Invalid or expired setup link')
        reserved_account = await session.get(
            AuthAccount, invitation.reserved_account_id
        )
        if reserved_account is not None and reserved_account.state == 'deleted':
            raise NativeAuthError('Invalid or expired setup link')
        await self._scope(
            session,
            invitation.creator_account_id,
            invitation.org_id,
            invitation.org_role_id,
        )
        return invitation

    async def inspect_invitation(self, token: str) -> InvitationInspection:
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            invitation = await self._invitation(session, token)
            existing = await session.scalar(
                select(AuthAccount.id).where(
                    AuthAccount.normalized_email == invitation.normalized_email,
                    AuthAccount.state != 'deleted',
                )
            )
            org = (
                await session.get(Org, invitation.org_id) if invitation.org_id else None
            )
            return {
                'email': invitation.display_email,
                'org_id': invitation.org_id,
                'org_name': org.name if org else None,
                'org_role_id': invitation.org_role_id,
                'expires_at': invitation.expires_at,
                'action': 'login' if existing else 'set_password',
                'authentication_methods': await self.authentication_methods(
                    session, existing
                )
                if existing
                else [],
            }

    async def complete_enrollment(
        self, token: str, password: str, *, client_ip: str
    ) -> NativeLogin | None:
        await self.throttle('enrollment', client_ip)
        inspected = await self.inspect_invitation(token)
        if inspected['action'] == 'login':
            return None
        password_hash = await hash_password(password)
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            invitation = await self._invitation(session, token)
            if await session.scalar(
                select(AuthAccount.id).where(
                    AuthAccount.normalized_email == invitation.normalized_email,
                    AuthAccount.state != 'deleted',
                )
            ):
                return None
            account = AuthAccount(
                id=invitation.reserved_account_id,
                normalized_email=invitation.normalized_email,
                display_email=invitation.display_email,
            )
            session.add(account)
            await session.flush()
            credential = PasswordCredential(
                account_id=account.id,
                normalized_login_email=invitation.normalized_email,
                display_email=invitation.display_email,
                password_hash=password_hash,
            )
            session.add(credential)
            user = await create_profile(session, account, invitation.display_email)
            await self._accept(session, invitation, user)
            await session.flush()
            return await self._new_session(session, account, credential, user)

    async def _accept(
        self, session: AsyncSession, invitation: AccountInvitation, user: User
    ) -> None:
        if invitation.org_id is not None:
            org = await session.get(Org, invitation.org_id)
            if org is None or invitation.org_role_id is None:
                raise NativeAuthError('Invalid organization membership', 400)
            await add_membership(session, user, org, invitation.org_role_id)
            user.current_org_id = org.id
        invitation.accepted_account_id = user.id
        invitation.consumed_at = _now()

    async def accept_membership(self, account_id: UUID, token: str) -> None:
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            invitation = await self._invitation(session, token)
            account = await session.get(AuthAccount, account_id)
            user = await session.get(User, account_id)
            if (
                account is None
                or account.normalized_email != invitation.normalized_email
                or account.state != 'profile_present'
                or user is None
                or user.is_disabled
            ):
                raise NativeAuthError('Sign in as the invited account', 403)
            await self._accept(session, invitation, user)

    async def revoke_invitation(self, creator_id: UUID, invitation_id: UUID) -> None:
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            await require_active_admin(session, creator_id)
            invitation = await session.get(
                AccountInvitation, invitation_id, with_for_update=True
            )
            if invitation is not None and invitation.consumed_at is None:
                invitation.revoked_at = _now()

    async def reissue_invitation(
        self, creator_id: UUID, invitation_id: UUID
    ) -> InvitationLink:
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            previous = await session.get(
                AccountInvitation, invitation_id, with_for_update=True
            )
            if previous is None or previous.consumed_at is not None:
                raise NativeAuthError('Unused invitation not found', 404)
            await self._scope(
                session, creator_id, previous.org_id, previous.org_role_id
            )
            previous.revoked_at = _now()
            token = new_token()
            invitation = AccountInvitation(
                id=uuid4(),
                token_digest=digest_token(token, 'enrollment'),
                reserved_account_id=previous.reserved_account_id,
                normalized_email=previous.normalized_email,
                display_email=previous.display_email,
                creator_account_id=creator_id,
                org_id=previous.org_id,
                org_role_id=previous.org_role_id,
                expires_at=_now()
                + timedelta(seconds=get_native_auth_settings().invitation_seconds),
            )
            session.add(invitation)
            await session.flush()
            return self._invitation_link(invitation, token)

    async def _recent(
        self, session: AsyncSession, token: str, account_id: UUID
    ) -> SessionRow:
        row = await self._session(session, token)
        if (
            row is None
            or row[1].id != account_id
            or row[0].auth_time
            <= _now()
            - timedelta(seconds=get_native_auth_settings().recent_auth_seconds)
        ):
            raise NativeAuthError('Sign in again to perform this action', 401)
        return row

    async def issue_password_reset(
        self, creator_id: UUID, account_id: UUID, session_token: str
    ) -> PasswordResetLink:
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            await self._recent(session, session_token, creator_id)
            await require_active_admin(session, creator_id)
            credential = await session.get(PasswordCredential, account_id)
            account = await session.get(AuthAccount, account_id)
            if account is None or account.state == 'deleted':
                raise NativeAuthError('Account not found', 404)
            if credential is None:
                raise NativeAuthError('This account has no password to reset', 409)
            token = new_token()
            challenge = AuthChallenge(
                token_digest=digest_token(token, 'password_reset'),
                purpose='password_reset',
                account_id=account_id,
                credential_version=credential.credential_version,
                creator_account_id=creator_id,
                expires_at=_now()
                + timedelta(seconds=get_native_auth_settings().reset_seconds),
            )
            session.add(challenge)
            return {
                'reset_url': f'{get_native_auth_settings().app_origin}/password-reset#token={token}',
                'expires_at': challenge.expires_at,
            }

    async def complete_password_reset(
        self, token: str, new_password: str, *, client_ip: str
    ) -> None:
        await self.throttle('password_reset', client_ip)
        if not _valid_token(token):
            raise NativeAuthError('Invalid or expired password reset link')
        # Validate before Argon2 work; recheck everything under lock after hashing.
        async with self.sessions() as session:
            exists = await session.scalar(
                select(AuthChallenge.id).where(
                    AuthChallenge.token_digest == digest_token(token, 'password_reset'),
                    AuthChallenge.purpose == 'password_reset',
                    AuthChallenge.expires_at > _now(),
                    AuthChallenge.consumed_at.is_(None),
                    AuthChallenge.revoked_at.is_(None),
                )
            )
            if exists is None:
                raise NativeAuthError('Invalid or expired password reset link')
        password_hash = await hash_password(new_password)
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            challenge = await session.scalar(
                select(AuthChallenge)
                .where(
                    AuthChallenge.token_digest == digest_token(token, 'password_reset'),
                    AuthChallenge.purpose == 'password_reset',
                )
                .with_for_update()
            )
            if (
                challenge is None
                or challenge.expires_at <= _now()
                or challenge.consumed_at is not None
                or challenge.revoked_at is not None
            ):
                raise NativeAuthError('Invalid or expired password reset link')
            if challenge.creator_account_id is not None:
                await require_active_admin(session, challenge.creator_account_id)
            account = await session.get(AuthAccount, challenge.account_id)
            credential = await session.get(
                PasswordCredential, challenge.account_id, with_for_update=True
            )
            if (
                account is None
                or account.state == 'deleted'
                or credential is None
                or credential.credential_version != challenge.credential_version
            ):
                raise NativeAuthError('Invalid or expired password reset link')
            challenge.consumed_at = _now()
            await self._replace_password(session, credential, password_hash)

    async def _replace_password(
        self, session: AsyncSession, credential: PasswordCredential, password_hash: str
    ) -> None:
        credential.password_hash = password_hash
        credential.credential_version += 1
        credential.changed_at = _now()
        await revoke_account_security(session, credential.account_id)

    async def change_password(
        self,
        account_id: UUID,
        session_token: str,
        current_password: str,
        new_password: str,
        *,
        client_ip: str,
    ) -> NativeLogin:
        await self.throttle('password_change', client_ip, str(account_id))
        async with self.sessions() as session, session.begin():
            row = await self._recent(session, session_token, account_id)
            if row[2] is None:
                raise NativeAuthError('This account has no password to change', 409)
            known_hash = row[2].password_hash
        if not await verify_password(known_hash, current_password):
            raise NativeAuthError('Invalid current password', 401)
        password_hash = await hash_password(new_password)
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            _, account, credential, user = await self._recent(
                session, session_token, account_id
            )
            if credential is None or credential.password_hash != known_hash:
                raise NativeAuthError('Sign in again to perform this action', 401)
            await self._replace_password(session, credential, password_hash)
            await session.flush()
            await session.refresh(account)
            return await self._new_session(session, account, credential, user)

    async def recover_password(self, account_id: UUID, new_password: str) -> None:
        password_hash = await hash_password(new_password)
        async with self.sessions() as session, session.begin():
            await lock_native_lifecycle(session)
            account = await session.get(AuthAccount, account_id, with_for_update=True)
            credential = await session.get(
                PasswordCredential, account_id, with_for_update=True
            )
            if account is None or account.state == 'deleted':
                raise NativeAuthError('Account not found', 404)
            if credential is None:
                raise NativeAuthError('This account has no password to recover', 409)
            await self._replace_password(session, credential, password_hash)

    @staticmethod
    async def authentication_methods(
        session: AsyncSession, account_id: UUID
    ) -> list[str]:
        methods = set(
            await session.scalars(
                select(ExternalIdentity.auth_method).where(
                    ExternalIdentity.account_id == account_id
                )
            )
        )
        if await session.get(PasswordCredential, account_id) is not None:
            methods.add('password')
        return sorted(methods)

    async def profile_metadata(self, account_id: UUID) -> NativeProfileMetadata:
        """Public local identity metadata, with no credentials or IdP subjects."""
        async with self.sessions() as session:
            account = await session.get(AuthAccount, account_id)
            if account is None or account.state != 'profile_present':
                raise NativeAuthError('Account is unavailable', 401)
            methods = await self.authentication_methods(session, account_id)
            return {
                'email': account.display_email,
                'has_password': 'password' in methods,
                'authentication_methods': methods,
            }

    @staticmethod
    def _invitation_metadata(invitation: AccountInvitation) -> InvitationMetadata:
        return {
            'id': str(invitation.id),
            'email': invitation.display_email,
            'org_id': invitation.org_id,
            'org_role_id': invitation.org_role_id,
            'created_at': invitation.created_at,
            'expires_at': invitation.expires_at,
            'consumed_at': invitation.consumed_at,
            'revoked_at': invitation.revoked_at,
            'accepted_account_id': invitation.accepted_account_id,
        }

    async def list_invitations(
        self, creator_id: UUID, *, offset: int = 0, limit: int = 50
    ) -> InvitationPage:
        async with self.sessions() as session:
            await require_active_admin(session, creator_id)
            total = await session.scalar(
                select(func.count()).select_from(AccountInvitation)
            )
            rows = (
                await session.scalars(
                    select(AccountInvitation)
                    .order_by(AccountInvitation.created_at.desc(), AccountInvitation.id)
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
            return {
                'items': [self._invitation_metadata(row) for row in rows],
                'total': total or 0,
            }

    async def list_accounts(
        self,
        creator_id: UUID,
        *,
        account_id: UUID | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> NativeAccountPage:
        async with self.sessions() as session:
            await require_active_admin(session, creator_id)
            query = select(AuthAccount, User).outerjoin(User, User.id == AuthAccount.id)
            if account_id:
                query = query.where(AuthAccount.id == account_id)
            total = await session.scalar(
                select(func.count()).select_from(query.subquery())
            )
            rows = (
                await session.execute(
                    query.order_by(AuthAccount.created_at.desc(), AuthAccount.id)
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
            items: list[NativeAccountMetadata] = []
            for account, user in rows:
                pending = []
                if account.normalized_email is not None:
                    invites = (
                        await session.scalars(
                            select(AccountInvitation).where(
                                AccountInvitation.normalized_email
                                == account.normalized_email,
                                AccountInvitation.consumed_at.is_(None),
                                AccountInvitation.revoked_at.is_(None),
                                AccountInvitation.expires_at > _now(),
                            )
                        )
                    ).all()
                    pending = [self._invitation_metadata(invite) for invite in invites]
                items.append(
                    {
                        'id': str(account.id),
                        'email': account.display_email,
                        'authentication_methods': await self.authentication_methods(
                            session, account.id
                        ),
                        'state': account.state,
                        'profile_present': user is not None,
                        'is_disabled': user.is_disabled
                        if user
                        else account.state == 'profile_absent_blocked',
                        'role_id': user.role_id if user else None,
                        'created_at': account.created_at,
                        'pending_invitations': pending,
                    }
                )
            return {'items': items, 'total': total or 0}

    async def cleanup_expired_state(self) -> None:
        """Keep expired secrets briefly for incident review; no raw tokens exist."""
        cutoff = _now() - timedelta(days=7)
        async with self.sessions() as session, session.begin():
            from storage.native_git import GitOAuthState

            await session.execute(
                delete(GitOAuthState).where(GitOAuthState.expires_at < _now())
            )
            await session.execute(
                delete(BrowserSession).where(
                    or_(
                        BrowserSession.absolute_expires_at < cutoff,
                        BrowserSession.revoked_at < cutoff,
                    )
                )
            )
            await session.execute(
                delete(AuthChallenge).where(AuthChallenge.expires_at < cutoff)
            )
            await session.execute(
                delete(AccountInvitation).where(AccountInvitation.expires_at < cutoff)
            )
            await session.execute(
                delete(AuthThrottle).where(
                    AuthThrottle.window_start < _now() - timedelta(minutes=5)
                )
            )


@lru_cache(maxsize=1)
def get_native_auth_service() -> NativeAuthService:
    return NativeAuthService()
