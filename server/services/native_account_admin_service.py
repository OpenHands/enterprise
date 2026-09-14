"""Credential-free account metadata for account administration."""

from functools import lru_cache
from uuid import UUID

from sqlalchemy import func, select

from server.auth.native_types import (
    InvitationMetadata,
    NativeAccountMetadata,
    NativeAccountPage,
    SessionFactory,
)
from server.services.native_account_service import require_active_admin
from server.services.native_auth_service import NativeAuthService, _now
from server.services.native_enrollment_service import NativeEnrollmentService
from storage.account_invitation import AccountInvitation
from storage.native_auth import AuthAccount
from storage.user import User


class NativeAccountAdminService:
    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self.auth = NativeAuthService(session_factory)
        self.sessions = self.auth.sessions

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
                pending: list[InvitationMetadata] = []
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
                    pending = [
                        NativeEnrollmentService._invitation_metadata(invite)
                        for invite in invites
                    ]
                items.append(
                    {
                        'id': str(account.id),
                        'email': account.display_email,
                        'authentication_methods': await self.auth.authentication_methods(
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


@lru_cache(maxsize=1)
def get_native_account_admin_service() -> NativeAccountAdminService:
    return NativeAccountAdminService()
