from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from server.auth.authorization import get_user_super_role
from server.auth.password_auth import (
    PASSWORD_LINK_TTL_HOURS,
    PasswordAuthError,
    digest_password_token,
    hash_password,
    new_password_token,
    normalize_email,
    require_password_auth_enabled,
    utc_now,
    verify_password_or_dummy,
)
from storage.database import a_session_maker
from storage.org_invitation import OrgInvitation
from storage.org_member import OrgMember
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
        email: str, current_org_id: UUID | None = None
    ) -> PasswordAuthAccount:
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
        account = await PasswordAuthService.ensure_account(
            invitation.email, invitation.org_id
        )
        if account.password_hash is not None:
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

        requester_super_role = await get_user_super_role(str(requester_user_id))
        requester_is_superadmin = bool(
            requester_super_role and requester_super_role.name == 'admin'
        )
        target_super_role = await get_user_super_role(str(target_user_id))
        target_is_superadmin = bool(
            target_super_role and target_super_role.name == 'admin'
        )

        if requester_is_superadmin:
            return
        if target_is_superadmin:
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
        if requester_role is None or requester_role.name not in ('owner', 'admin'):
            raise PasswordAuthError('Administrator permission is required', 403)

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
            if row.consumed_at is not None:
                status = 'used'
            elif row.revoked_at is not None:
                status = 'invalid'
            elif row.expires_at <= utc_now():
                status = 'expired'
            else:
                status = 'valid'
            return PasswordTokenInspection(email, row.purpose, status, row.expires_at)

    @staticmethod
    async def complete_token(token: str, password: str) -> tuple[UUID, int]:
        require_password_auth_enabled()
        password_hash = await hash_password(password)
        now = utc_now()
        async with a_session_maker() as session, session.begin():
            row = await session.scalar(
                select(PasswordAuthToken)
                .where(PasswordAuthToken.token_digest == digest_password_token(token))
                .with_for_update()
            )
            if row is None:
                raise PasswordAuthError('This password link is invalid', 400, 'invalid')
            if row.consumed_at is not None:
                raise PasswordAuthError(
                    'This password link has already been used', 400, 'used'
                )
            if row.revoked_at is not None:
                raise PasswordAuthError(
                    'This password link is no longer valid', 400, 'invalid'
                )
            if row.expires_at <= now:
                raise PasswordAuthError(
                    'This password link has expired', 400, 'expired'
                )
            account = await session.get(
                PasswordAuthAccount, row.user_id, with_for_update=True
            )
            if account is None:
                raise PasswordAuthError('Account is unavailable', 404)

            if row.org_invitation_id is not None:
                invitation = await session.get(
                    OrgInvitation, row.org_invitation_id, with_for_update=True
                )
                if (
                    invitation is None
                    or invitation.status != OrgInvitation.STATUS_PENDING
                    or normalize_email(invitation.email) != account.normalized_email
                ):
                    raise PasswordAuthError(
                        'This password link is no longer valid', 400, 'invalid'
                    )
                member = await session.get(
                    OrgMember, (invitation.org_id, account.user_id)
                )
                if member is None:
                    session.add(
                        OrgMember(
                            org_id=invitation.org_id,
                            user_id=account.user_id,
                            role_id=invitation.role_id,
                            llm_api_key='',
                            status='active',
                            agent_settings_diff={},
                            conversation_settings_diff={},
                        )
                    )
                invitation.status = OrgInvitation.STATUS_ACCEPTED
                invitation.accepted_at = now.replace(tzinfo=None)
                invitation.accepted_by_user_id = account.user_id

            account.password_hash = password_hash
            account.session_version += 1
            account.updated_at = now
            row.consumed_at = now
            user_id = account.user_id
            session_version = account.session_version

        await UserStore.record_login(str(user_id))
        return user_id, session_version

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
        valid = await verify_password_or_dummy(
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
