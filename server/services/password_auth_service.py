from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import (
    is_instance_super_admin,
    outranks_for_member_management,
)
from server.auth.password_auth import (
    PASSWORD_LINK_TTL_HOURS,
    PasswordAuthError,
    digest_password_token,
    hash_password,
    is_password_auth_enabled,
    new_password_token,
    normalize_email,
    require_password_auth_enabled,
    utc_now,
    verify_password,
)
from server.constants import ROLE_ADMIN, ROLE_OWNER
from storage.database import a_session_maker
from storage.org_invitation import OrgInvitation
from storage.org_invitation_store import OrgInvitationStore
from storage.org_member_store import OrgMemberStore
from storage.password_auth import PasswordAuthAccount, PasswordAuthToken
from storage.role_store import RoleStore
from storage.user import User
from storage.user_store import UserStore


@dataclass(frozen=True)
class PasswordLink:
    token: str
    expires_at: datetime
    purpose: str


@dataclass(frozen=True)
class PasswordTokenInspection:
    email: str
    purpose: str
    status: str
    expires_at: datetime | None


class PasswordAuthService:
    @staticmethod
    async def ensure_account(
        email: str,
        current_org_id: UUID | None = None,
        org_invitation_id: int | None = None,
    ) -> PasswordAuthAccount:
        """Return the password account for an email, creating it when needed.

        ``org_invitation_id`` is recorded only on rows this call creates, so
        later callers can tell an invitation-provisioned account from one that
        belonged to an existing user.
        """
        require_password_auth_enabled()
        normalized = normalize_email(email)
        existing_user = await UserStore.get_user_by_email(normalized)
        if existing_user is None and current_org_id is None:
            raise PasswordAuthError('An organization is required for a new user', 400)

        user_id = existing_user.id if existing_user else uuid4()
        async with a_session_maker() as session, session.begin():
            if existing_user is None:
                session.add(
                    User(
                        id=user_id,
                        current_org_id=current_org_id,
                        email=normalized,
                        email_verified=True,
                    )
                )

            account = await session.get(PasswordAuthAccount, user_id)
            if account is None:
                account = PasswordAuthAccount(
                    user_id=user_id,
                    normalized_email=normalized,
                    created_by_org_invitation_id=(
                        org_invitation_id if existing_user is None else None
                    ),
                )
                session.add(account)
                try:
                    await session.flush()
                except IntegrityError as exc:
                    raise PasswordAuthError(
                        'An account already exists for this email', 409
                    ) from exc
            return account

    @staticmethod
    async def _account_for_user(user_id: UUID) -> PasswordAuthAccount:
        async with a_session_maker() as session:
            account = await session.get(PasswordAuthAccount, user_id)
            if account is None:
                user = await UserStore.get_user_by_id(str(user_id))
                if user is None or not user.email:
                    raise PasswordAuthError('Account is unavailable', 404)
                account = PasswordAuthAccount(
                    user_id=user.id,
                    normalized_email=normalize_email(user.email),
                )
                session.add(account)
                try:
                    await session.commit()
                except IntegrityError as exc:
                    raise PasswordAuthError(
                        'An account already exists for this email', 409
                    ) from exc
            return account

    @staticmethod
    async def get_password_statuses(user_ids: list[UUID]) -> dict[UUID, bool]:
        if not user_ids:
            return {}
        async with a_session_maker() as session:
            rows = await session.execute(
                select(
                    PasswordAuthAccount.user_id,
                    PasswordAuthAccount.password_hash,
                ).where(PasswordAuthAccount.user_id.in_(user_ids))
            )
            return {
                user_id: password_hash is not None for user_id, password_hash in rows
            }

    @staticmethod
    async def issue_link(
        *,
        user_id: UUID,
        creator_user_id: UUID,
        purpose: str,
        org_invitation_id: int | None = None,
    ) -> PasswordLink:
        require_password_auth_enabled()
        account = await PasswordAuthService._account_for_user(user_id)
        if purpose == 'reset' and account.password_hash is None:
            purpose = 'setup'
        raw_token = new_password_token()
        now = utc_now()
        expires_at = now + timedelta(hours=PASSWORD_LINK_TTL_HOURS)
        async with a_session_maker() as session, session.begin():
            await session.execute(
                update(PasswordAuthToken)
                .where(
                    PasswordAuthToken.user_id == user_id,
                    PasswordAuthToken.consumed_at.is_(None),
                    PasswordAuthToken.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            session.add(
                PasswordAuthToken(
                    user_id=user_id,
                    org_invitation_id=org_invitation_id,
                    purpose=purpose,
                    token_digest=digest_password_token(raw_token),
                    created_by_user_id=creator_user_id,
                    expires_at=expires_at,
                )
            )
        return PasswordLink(raw_token, expires_at, purpose)

    @staticmethod
    async def issue_setup_link_for_invitation(
        invitation: OrgInvitation, creator_user_id: UUID
    ) -> PasswordLink | None:
        """Setup link for an invitation, or None when the invitee already exists.

        A setup link sets a password without proving who is holding it, so it
        may only ever reach an account this invitation itself provisioned.
        Everyone else - SSO users, superadmins, members of another org - gets
        the normal invitation URL and accepts it signed in as themselves.
        """
        account = await PasswordAuthService.ensure_account(
            invitation.email, invitation.org_id, org_invitation_id=invitation.id
        )
        if (
            account.password_hash is not None
            or account.created_by_org_invitation_id != invitation.id
        ):
            return None
        return await PasswordAuthService.issue_link(
            user_id=account.user_id,
            creator_user_id=creator_user_id,
            purpose='setup',
            org_invitation_id=invitation.id,
        )

    @staticmethod
    async def _authorize_password_reset(
        org_id: UUID, target_user_id: UUID, requester_user_id: UUID
    ) -> None:
        if requester_user_id == target_user_id:
            raise PasswordAuthError(
                'Ask another administrator to reset your password', 403
            )

        if await is_instance_super_admin(requester_user_id):
            return
        if await is_instance_super_admin(target_user_id):
            raise PasswordAuthError(
                'Organization admins cannot reset a superadmin password', 403
            )

        requester_member = await OrgMemberStore.get_org_member(
            org_id, requester_user_id
        )
        target_member = await OrgMemberStore.get_org_member(org_id, target_user_id)
        if requester_member is None or target_member is None:
            raise PasswordAuthError('Organization membership is required', 403)
        requester_role = await RoleStore.get_role_by_id(requester_member.role_id)
        if requester_role is None or requester_role.name not in (
            ROLE_OWNER,
            ROLE_ADMIN,
        ):
            raise PasswordAuthError('Administrator permission is required', 403)

        # A password is instance-wide, so holding the reset link is as good as
        # being that user in every org they belong to. Require the same rank
        # rule member removal uses, and apply it to the target's role in each
        # of their orgs rather than only this one.
        for membership in await OrgMemberStore.get_user_orgs(target_user_id):
            target_role = await RoleStore.get_role_by_id(membership.role_id)
            if target_role is None or not outranks_for_member_management(
                requester_role.name, target_role.name
            ):
                raise PasswordAuthError(
                    'You do not have permission to reset this password', 403
                )

    @staticmethod
    async def issue_reset_link(
        org_id: UUID, target_user_id: UUID, requester_user_id: UUID
    ) -> PasswordLink:
        require_password_auth_enabled()
        await PasswordAuthService._authorize_password_reset(
            org_id, target_user_id, requester_user_id
        )
        return await PasswordAuthService.issue_link(
            user_id=target_user_id,
            creator_user_id=requester_user_id,
            purpose='reset',
        )

    @staticmethod
    def _token_status(row: PasswordAuthToken, invitation: OrgInvitation | None) -> str:
        if row.consumed_at is not None:
            return 'used'
        if row.revoked_at is not None:
            return 'invalid'
        if row.expires_at <= utc_now():
            return 'expired'
        if row.org_invitation_id is not None:
            # The link outlives the invitation it was issued for, so a revoked
            # or expired invitation has to invalidate it too.
            if invitation is None or invitation.status != OrgInvitation.STATUS_PENDING:
                return 'invalid'
            if OrgInvitationStore.is_token_expired(invitation):
                return 'expired'
        return 'valid'

    @staticmethod
    async def inspect_token(token: str) -> PasswordTokenInspection:
        require_password_auth_enabled()
        if not token or len(token) > 256:
            return PasswordTokenInspection('', '', 'invalid', None)
        async with a_session_maker() as session:
            row = await session.scalar(
                select(PasswordAuthToken).where(
                    PasswordAuthToken.token_digest == digest_password_token(token)
                )
            )
            if row is None:
                return PasswordTokenInspection('', '', 'invalid', None)
            account = await session.get(PasswordAuthAccount, row.user_id)
            email = account.normalized_email if account else ''
            invitation = (
                await session.get(OrgInvitation, row.org_invitation_id)
                if row.org_invitation_id is not None
                else None
            )
            status = PasswordAuthService._token_status(row, invitation)
            return PasswordTokenInspection(email, row.purpose, status, row.expires_at)

    @staticmethod
    def _raise_for_token_status(status: str) -> None:
        if status == 'used':
            raise PasswordAuthError(
                'This password link has already been used', 400, 'used'
            )
        if status == 'expired':
            raise PasswordAuthError('This password link has expired', 400, 'expired')
        if status != 'valid':
            raise PasswordAuthError('This password link is invalid', 400, 'invalid')

    @staticmethod
    async def complete_token(token: str, password: str) -> tuple[UUID, int]:
        require_password_auth_enabled()
        # Reject bad tokens before paying for an Argon2 hash; the locked
        # re-check below is what actually guards against races.
        PasswordAuthService._raise_for_token_status(
            (await PasswordAuthService.inspect_token(token)).status
        )
        password_hash = await hash_password(password)
        now = utc_now()
        invitation_token: str | None = None
        async with a_session_maker() as session, session.begin():
            row = await session.scalar(
                select(PasswordAuthToken)
                .where(PasswordAuthToken.token_digest == digest_password_token(token))
                .with_for_update()
            )
            if row is None:
                raise PasswordAuthError('This password link is invalid', 400, 'invalid')
            account = await session.get(
                PasswordAuthAccount, row.user_id, with_for_update=True
            )
            if account is None:
                raise PasswordAuthError('Account is unavailable', 404)

            invitation = (
                await session.get(
                    OrgInvitation, row.org_invitation_id, with_for_update=True
                )
                if row.org_invitation_id is not None
                else None
            )
            PasswordAuthService._raise_for_token_status(
                PasswordAuthService._token_status(row, invitation)
            )
            if invitation is not None:
                if normalize_email(invitation.email) != account.normalized_email:
                    raise PasswordAuthError(
                        'This password link is no longer valid', 400, 'invalid'
                    )
                invitation_token = invitation.token

            account.password_hash = password_hash
            account.session_version += 1
            account.updated_at = now
            row.consumed_at = now
            user_id = account.user_id
            session_version = account.session_version

        if invitation_token is not None:
            await PasswordAuthService._accept_invitation_after_setup(
                invitation_token, user_id
            )
        await UserStore.record_login(str(user_id))
        return user_id, session_version

    @staticmethod
    async def _accept_invitation_after_setup(
        invitation_token: str, user_id: UUID
    ) -> None:
        """Join the org through the normal acceptance path.

        Runs after the password transaction commits because acceptance calls
        LiteLLM, which must not happen while the token and account rows are
        locked. A failure leaves the password set and the invitation pending,
        so the user can still accept it from the invitation link.
        """
        from server.routes.org_invitation_models import UserAlreadyMemberError
        from server.services.org_invitation_service import OrgInvitationService

        try:
            await OrgInvitationService.accept_invitation(invitation_token, user_id)
        except UserAlreadyMemberError:
            pass
        except Exception:
            logger.exception(
                'Failed to accept invitation after password setup',
                extra={'user_id': str(user_id)},
                stack_info=True,
            )

    @staticmethod
    async def discard_unclaimed_invitation_account(invitation_id: int) -> None:
        """Drop the placeholder account an invitation created, if still unused.

        Called when an invitation stops being pending without being accepted,
        so a revoked or expired invite doesn't leave a user row behind in the
        instance user list. Anything the person has actually used - a password,
        a login, a membership, another pending invite - keeps the row.
        """
        if not is_password_auth_enabled():
            return
        async with a_session_maker() as session, session.begin():
            account = await session.scalar(
                select(PasswordAuthAccount).where(
                    PasswordAuthAccount.created_by_org_invitation_id == invitation_id,
                    PasswordAuthAccount.password_hash.is_(None),
                )
            )
            if account is None:
                return
            user = await session.get(User, account.user_id)
            if user is None or user.last_login_at is not None:
                return
            if await OrgMemberStore.get_user_orgs(account.user_id):
                return
            other_pending = await session.scalar(
                select(OrgInvitation.id).where(
                    OrgInvitation.email == account.normalized_email,
                    OrgInvitation.status == OrgInvitation.STATUS_PENDING,
                    OrgInvitation.id != invitation_id,
                )
            )
            if other_pending is not None:
                return
            await session.delete(account)
            await session.delete(user)

    @staticmethod
    async def login(email: str, password: str) -> tuple[UUID, int]:
        require_password_auth_enabled()
        try:
            normalized = normalize_email(email)
        except PasswordAuthError:
            normalized = ''
        async with a_session_maker() as session:
            account = await session.scalar(
                select(PasswordAuthAccount).where(
                    PasswordAuthAccount.normalized_email == normalized
                )
            )
        valid = await verify_password(
            account.password_hash if account else None, password
        )
        if not valid or account is None:
            raise PasswordAuthError(
                'Invalid email or password', 401, 'invalid_credentials'
            )
        await UserStore.record_login(str(account.user_id))
        return account.user_id, account.session_version

    @staticmethod
    async def validate_session(user_id: UUID, session_version: int) -> bool:
        async with a_session_maker() as session:
            account = await session.get(PasswordAuthAccount, user_id)
            return bool(
                account
                and account.password_hash
                and account.session_version == session_version
            )
