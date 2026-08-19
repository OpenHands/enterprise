"""Quota status and increase request API for the settings page."""

import os

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from server.services.daily_conversation_quota_service import (
    DailyConversationQuotaService,
)
from server.services.quota_increase_request_service import (
    QuotaIncreaseRequestService,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from storage.database import a_session_maker

from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.dependencies import get_dependencies
from openhands.app_server.utils.logger import openhands_logger as logger

quota_router = APIRouter(
    prefix='/api/quota', tags=['Quota'], dependencies=get_dependencies()
)
quota_admin_router = APIRouter(
    prefix='/api/admin/quota', tags=['Admin'], dependencies=get_dependencies()
)


class QuotaStatusResponse(BaseModel):
    daily_limit: int | None
    used_today: int
    remaining: int | None
    reset_at: str
    work_email: str | None
    work_email_verified: bool
    latest_request_status: str | None
    latest_request_requested_limit: int | None


class CreateQuotaIncreaseRequest(BaseModel):
    work_email: str
    requested_limit: int
    reason: str | None = None


class QuotaIncreaseRequestResponse(BaseModel):
    id: int
    status: str
    work_email: str
    baseline_limit: int
    requested_limit: int
    reason: str | None
    created_at: str


class VerifyQuotaIncreaseResponse(BaseModel):
    status: str
    daily_limit: int


@quota_router.get('/status', response_model=QuotaStatusResponse)
async def get_quota_status(user_id: str = Depends(get_user_id)) -> QuotaStatusResponse:
    """Return the authenticated user's daily conversation quota status."""
    async with a_session_maker() as session:
        quota_service = DailyConversationQuotaService(session)
        status_result = await quota_service.get_status(user_id)

        request_service = QuotaIncreaseRequestService(session)
        latest = await request_service.get_latest_for_user(user_id)

    return QuotaStatusResponse(
        daily_limit=status_result.daily_limit,
        used_today=status_result.used_today,
        remaining=status_result.remaining,
        reset_at=status_result.reset_at,
        work_email=None,  # populated below
        work_email_verified=False,
        latest_request_status=latest.status if latest else None,
        latest_request_requested_limit=latest.requested_limit if latest else None,
    )


@quota_router.post(
    '/increase-request',
    response_model=QuotaIncreaseRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_quota_increase_request(
    body: CreateQuotaIncreaseRequest,
    user_id: str = Depends(get_user_id),
) -> QuotaIncreaseRequestResponse:
    """Create a quota increase request and send a verification email."""
    async with a_session_maker() as session:
        service = QuotaIncreaseRequestService(session)
        request = await service.create_request(
            user_id=user_id,
            work_email=body.work_email,
            requested_limit=body.requested_limit,
            reason=body.reason,
        )

        # Send verification email
        token = service.create_verification_token(
            request_id=request.id,
            user_id=user_id,
            work_email=request.work_email,
        )
        _send_verification_email(request.work_email, token)

        # PostHog: identify work email and capture event
        _capture_quota_event(user_id, request.work_email, request.requested_limit)

    return QuotaIncreaseRequestResponse(
        id=request.id,
        status=request.status,
        work_email=request.work_email,
        baseline_limit=request.baseline_limit,
        requested_limit=request.requested_limit,
        reason=request.reason,
        created_at=request.created_at.isoformat() if request.created_at else '',
    )


@quota_router.get('/verify', response_model=VerifyQuotaIncreaseResponse)
async def verify_quota_increase(token: str) -> VerifyQuotaIncreaseResponse:
    """Verify a quota increase request via signed email token.

    This endpoint is unauthenticated (the token itself authenticates the
    request). On success, the requested limit is applied immediately.
    """
    payload = QuotaIncreaseRequestService.verify_token(token)
    request_id = payload['request_id']

    async with a_session_maker() as session:
        service = QuotaIncreaseRequestService(session)
        request = await service.approve_request(
            request_id=request_id,
            approved_by_user_id=None,  # self-service via email
        )

    return VerifyQuotaIncreaseResponse(
        status=request.status,
        daily_limit=request.requested_limit,
    )


@quota_admin_router.get('/requests', response_model=list[QuotaIncreaseRequestResponse])
async def list_pending_quota_requests(
    user_id: str = Depends(get_user_id),
) -> list[QuotaIncreaseRequestResponse]:
    """List all pending quota increase requests (admin only)."""
    await _require_admin(user_id)
    async with a_session_maker() as session:
        service = QuotaIncreaseRequestService(session)
        requests = await service.list_pending_requests()

    return [
        QuotaIncreaseRequestResponse(
            id=r.id,
            status=r.status,
            work_email=r.work_email,
            baseline_limit=r.baseline_limit,
            requested_limit=r.requested_limit,
            reason=r.reason,
            created_at=r.created_at.isoformat() if r.created_at else '',
        )
        for r in requests
    ]


@quota_admin_router.post(
    '/requests/{request_id}/approve', response_model=QuotaIncreaseRequestResponse
)
async def approve_quota_request(
    request_id: int,
    user_id: str = Depends(get_user_id),
) -> QuotaIncreaseRequestResponse:
    """Approve a quota increase request (admin only)."""
    await _require_admin(user_id)
    async with a_session_maker() as session:
        service = QuotaIncreaseRequestService(session)
        request = await service.approve_request(
            request_id=request_id,
            approved_by_user_id=user_id,
        )

    return QuotaIncreaseRequestResponse(
        id=request.id,
        status=request.status,
        work_email=request.work_email,
        baseline_limit=request.baseline_limit,
        requested_limit=request.requested_limit,
        reason=request.reason,
        created_at=request.created_at.isoformat() if request.created_at else '',
    )


def _send_verification_email(email: str, token: str) -> None:
    """Send a verification email with the signed link.

    Uses the existing Resend EmailService. Logs and swallows errors so
    the request is still persisted even if email delivery fails.
    """
    try:
        from server.services.email_service import EmailService

        if not EmailService.is_configured():
            logger.warning(
                'Email service not configured; skipping quota verification email'
            )
            return

        web_host = os.environ.get('WEB_HOST', 'https://app.all-hands.dev').strip().rstrip('/')
        if not web_host.startswith(('http://', 'https://')):
            web_host = f'https://{web_host}'
        verify_url = f'{web_host}/api/quota/verify?token={token}'

        import resend

        resend.api_key = os.environ.get('RESEND_API_KEY')
        from_email = os.environ.get(
            'RESEND_FROM_EMAIL', 'OpenHands <no-reply@openhands.dev>'
        )
        resend.Emails.send(
            {
                'from': from_email,
                'to': [email],
                'subject': 'Verify your OpenHands quota increase request',
                'html': f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                    <p>Hi,</p>
                    <p>You requested an increase to your daily conversation quota on OpenHands.</p>
                    <p>Click the button below to verify your work email and activate your new limit:</p>
                    <p style="margin: 30px 0;">
                        <a href="{verify_url}"
                           style="background-color: #c9b974; color: #0D0F11; padding: 8px 16px;
                                  text-decoration: none; border-radius: 8px; display: inline-block;
                                  font-size: 14px; font-weight: 600;">
                            Verify Email & Activate Quota
                        </a>
                    </p>
                    <p style="color: #666; font-size: 14px;">
                        Or copy and paste this link into your browser:<br>
                        <a href="{verify_url}" style="color: #c9b974; font-weight: 600;">{verify_url}</a>
                    </p>
                    <p style="color: #666; font-size: 14px;">
                        This link expires in 1 hour.
                    </p>
                    <p style="color: #666; font-size: 14px;">
                        If you didn't request this, you can safely ignore this email.
                    </p>
                    <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
                    <p style="color: #999; font-size: 12px;">
                        Best,<br>The OpenHands Team
                    </p>
                </div>
                """,
            }
        )
        logger.info('Quota verification email sent', extra={'email': email})
    except Exception:
        logger.exception(
            'Failed to send quota verification email', extra={'email': email}
        )


def _capture_quota_event(user_id: str, work_email: str, requested_limit: int) -> None:
    """Capture a PostHog event and set work_email as a person property."""
    try:
        from openhands.analytics import get_analytics_service, resolve_analytics_context

        analytics = get_analytics_service()
        if analytics is None:
            return

        ctx = resolve_analytics_context(user_id)
        # Set work_email as a person property
        analytics.set_person_properties(ctx, {'work_email': work_email})
        # Capture the request event
        analytics.capture(
            ctx,
            event='quota increase requested',
            properties={
                'work_email': work_email,
                'requested_limit': requested_limit,
            },
        )
    except Exception:
        logger.exception('Failed to capture quota increase analytics event')


async def _require_admin(user_id: str) -> None:
    """Check that the user is an admin (has superadmin role)."""
    from server.auth.authorization import get_user_super_role

    role = await get_user_super_role(user_id)
    if role is None or role.name != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Admin access required',
        )


class OrgQuotaUpdateRequest(BaseModel):
    daily_conversation_limit: int | None


class OrgQuotaResponse(BaseModel):
    org_id: str
    org_name: str
    daily_conversation_limit: int | None


@quota_admin_router.put(
    '/orgs/{org_id}/quota', response_model=OrgQuotaResponse
)
async def set_org_quota(
    org_id: str,
    body: OrgQuotaUpdateRequest,
    user_id: str = Depends(get_user_id),
) -> OrgQuotaResponse:
    """Set or clear an org-level daily conversation limit override.

    Admin only. Set to -1 to exempt the org entirely (unlimited).
    Set to NULL to inherit the deployment default.
    Set to a positive integer for an org-specific limit.
    """
    await _require_admin(user_id)
    from uuid import UUID

    from storage.org import Org

    async with a_session_maker() as session:
        org = await session.scalar(
            select(Org).where(Org.id == UUID(org_id))
        )
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Organization not found',
            )
        org.daily_conversation_limit = body.daily_conversation_limit
        await session.commit()

        return OrgQuotaResponse(
            org_id=str(org.id),
            org_name=org.name,
            daily_conversation_limit=org.daily_conversation_limit,
        )
