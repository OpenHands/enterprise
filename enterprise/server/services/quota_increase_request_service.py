"""Quota increase request service: create, verify, approve, reject."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from server.services.daily_conversation_quota_service import (
    DailyConversationQuotaService,
)
from server.services.free_email_domains import is_free_email_domain
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from storage.quota_increase_request import QuotaIncreaseRequest
from storage.user import User

MAX_MULTIPLIER = 10
VERIFICATION_TOKEN_TTL = timedelta(hours=1)

TOKEN_PURPOSE = 'quota_increase_verification'


class QuotaIncreaseRequestService:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def create_request(
        self,
        user_id: str,
        org_id: UUID,
        work_email: str,
        requested_limit: int,
        reason: str | None = None,
    ) -> QuotaIncreaseRequest:
        """Create a new quota increase request and persist the work email.

        Validates that the email is not a free domain, the requested limit
        is at least the current effective limit and at most 10x the base
        (org/deployment) default, and there is no conflicting pending
        request. Capping against the base default rather than the current
        effective limit prevents approved increases from compounding into
        unbounded self-service escalation.

        ``org_id`` is the request's effective org (resolved via
        ``EFFECTIVE_ORG_ID``), so the cap follows the org the caller is
        actually working in rather than their last-selected one.
        """
        work_email = work_email.strip().lower()

        if is_free_email_domain(work_email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Please use a work email address, not a free email provider.',
            )

        user = await self.db_session.scalar(
            select(User).where(User.id == UUID(user_id))
        )
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail='User not found'
            )

        quota_service = DailyConversationQuotaService(self.db_session)
        base_default = await quota_service.get_default_limit(org_id)
        if base_default is None or base_default <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Quota increases are only available when a daily limit is configured.',
            )

        effective_limit = (
            user.daily_conversation_limit
            if user.daily_conversation_limit is not None
            else base_default
        )

        max_allowed = base_default * MAX_MULTIPLIER
        if requested_limit < effective_limit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Requested limit ({requested_limit}) must be at least the current limit ({effective_limit}).',
            )
        if requested_limit > max_allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Requested limit ({requested_limit}) cannot exceed 10x the base limit ({max_allowed}).',
            )

        now = datetime.now(UTC)
        existing = await self.db_session.scalar(
            select(QuotaIncreaseRequest).where(
                QuotaIncreaseRequest.user_id == UUID(user_id),
                QuotaIncreaseRequest.status == QuotaIncreaseRequest.STATUS_PENDING,
            )
        )
        if existing is not None:
            # A pending request whose verification token has expired can no
            # longer be completed; expire it so the user is not permanently
            # locked out (e.g. the verification email never arrived).
            if (
                existing.created_at is not None
                and now - existing.created_at >= VERIFICATION_TOKEN_TTL
            ):
                existing.status = QuotaIncreaseRequest.STATUS_EXPIRED
                existing.updated_at = now
            else:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail='You already have a pending quota increase request. '
                    'Check your work email for the verification link.',
                )

        request = QuotaIncreaseRequest(
            user_id=UUID(user_id),
            work_email=work_email,
            baseline_limit=effective_limit,
            requested_limit=requested_limit,
            reason=reason,
            status=QuotaIncreaseRequest.STATUS_PENDING,
            created_at=now,
            updated_at=now,
        )
        self.db_session.add(request)

        user.work_email = work_email
        await self.db_session.commit()
        await self.db_session.refresh(request)
        return request

    async def approve_request(
        self,
        request_id: int,
        approved_by_user_id: str | None = None,
    ) -> QuotaIncreaseRequest:
        """Approve a pending request and apply the requested limit.

        Idempotent: re-approving an already-approved request returns it
        without re-applying the limit.
        """
        request = await self.db_session.scalar(
            select(QuotaIncreaseRequest).where(QuotaIncreaseRequest.id == request_id)
        )
        if request is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Quota increase request not found',
            )

        if request.status == QuotaIncreaseRequest.STATUS_APPROVED:
            return request

        if request.status != QuotaIncreaseRequest.STATUS_PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'Request is already {request.status}',
            )

        now = datetime.now(UTC)
        request.status = QuotaIncreaseRequest.STATUS_APPROVED
        request.approved_at = now
        request.updated_at = now
        if approved_by_user_id:
            request.approved_by_user_id = UUID(approved_by_user_id)

        user = await self.db_session.scalar(
            select(User).where(User.id == request.user_id)
        )
        if user is not None:
            user.daily_conversation_limit = request.requested_limit
            user.work_email_verified_at = now

        await self.db_session.commit()
        await self.db_session.refresh(request)
        return request

    async def reject_request(self, request_id: int) -> QuotaIncreaseRequest:
        """Reject a pending request without applying any limit change.

        Idempotent for already-rejected requests. Approved requests cannot
        be rejected (the limit was already applied).
        """
        request = await self.db_session.scalar(
            select(QuotaIncreaseRequest).where(QuotaIncreaseRequest.id == request_id)
        )
        if request is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Quota increase request not found',
            )

        if request.status == QuotaIncreaseRequest.STATUS_REJECTED:
            return request

        if request.status != QuotaIncreaseRequest.STATUS_PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'Request is already {request.status}',
            )

        request.status = QuotaIncreaseRequest.STATUS_REJECTED
        request.updated_at = datetime.now(UTC)
        await self.db_session.commit()
        await self.db_session.refresh(request)
        return request

    async def list_pending_requests(self) -> list[QuotaIncreaseRequest]:
        result = await self.db_session.execute(
            select(QuotaIncreaseRequest)
            .where(QuotaIncreaseRequest.status == QuotaIncreaseRequest.STATUS_PENDING)
            .order_by(QuotaIncreaseRequest.created_at)
        )
        return list(result.scalars().all())

    async def get_latest_for_user(self, user_id: str) -> QuotaIncreaseRequest | None:
        return await self.db_session.scalar(
            select(QuotaIncreaseRequest)
            .where(QuotaIncreaseRequest.user_id == UUID(user_id))
            .order_by(QuotaIncreaseRequest.created_at.desc())
        )

    @staticmethod
    def create_verification_token(
        request_id: int,
        user_id: str,
        work_email: str,
    ) -> str:
        """Create a signed JWS token for email verification."""
        from storage.encrypt_utils import get_jwt_service

        payload = {
            'purpose': TOKEN_PURPOSE,
            'request_id': request_id,
            'user_id': user_id,
            'email': work_email,
        }
        return get_jwt_service().create_jws_token(
            payload, expires_in=VERIFICATION_TOKEN_TTL
        )

    @staticmethod
    def verify_token(token: str) -> dict:
        """Verify and decode a verification token."""
        from storage.encrypt_utils import get_jwt_service

        try:
            payload = get_jwt_service().verify_jws_token(token)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Invalid or expired verification token',
            ) from exc

        if payload.get('purpose') != TOKEN_PURPOSE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Invalid verification token',
            )
        return payload
