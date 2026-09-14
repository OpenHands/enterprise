"""Account enrollment and scoped organization invitations."""

from datetime import timedelta
from functools import lru_cache
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.auth_config import get_native_auth_settings
from server.auth.authorization import Permission, has_permission
from server.auth.native_password import NativeAuthError, hash_password, normalize_email
from server.auth.native_session import digest_token, new_token
from server.auth.native_types import (
    InvitationInspection,
    InvitationLink,
    InvitationMetadata,
    InvitationOrganizationPage,
    InvitationPage,
    SessionFactory,
)
from server.services.native_account_service import (
    add_membership,
    create_profile,
    lock_native_lifecycle,
    require_active_admin,
)
from server.services.native_auth_service import (
    NativeAuthService,
    NativeLogin,
    _now,
    _valid_token,
)
from storage.account_invitation import AccountInvitation
from storage.native_auth import AuthAccount, PasswordCredential
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User


class NativeEnrollmentService:
    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self.auth = NativeAuthService(session_factory)
        self.sessions = self.auth.sessions

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
            'invite_url': f'{get_native_auth_settings().web_url}/account-setup#token={token}',
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
                'authentication_methods': await self.auth.authentication_methods(
                    session, existing
                )
                if existing
                else [],
            }

    async def complete_enrollment(
        self, token: str, password: str, *, client_ip: str
    ) -> NativeLogin | None:
        await self.auth.throttle('enrollment', client_ip)
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
            return await self.auth._new_session(session, account, credential, user)

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

    async def cleanup_expired_state(self) -> None:
        """Retain expired invitation metadata briefly without retaining raw tokens."""
        cutoff = _now() - timedelta(days=7)
        async with self.sessions() as session, session.begin():
            await session.execute(
                delete(AccountInvitation).where(AccountInvitation.expires_at < cutoff)
            )


@lru_cache(maxsize=1)
def get_native_enrollment_service() -> NativeEnrollmentService:
    return NativeEnrollmentService()
