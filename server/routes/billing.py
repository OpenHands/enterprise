# billing.py - Handles all billing-related operations including credit management and Stripe integration
import asyncio
import math
import typing
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select, update

from integrations import stripe_service
from openhands.analytics import get_analytics_service
from openhands.app_server.user_auth import get_user_id
from server.auth.org_context import EFFECTIVE_ORG_ID
from server.constants import STRIPE_API_KEY
from server.logger import logger
from server.services.feature_flag_service import feature_flag_service
from server.utils.url_utils import get_web_url
from storage.billing_session import BillingSession
from storage.budget_control import (
    BudgetControlConflict,
    BudgetControlSession,
    budget_control_session,
    budget_engine,
)
from storage.database import a_session_maker
from storage.lite_llm_manager import LiteLlmManager
from storage.org import Org
from storage.subscription_access import SubscriptionAccess
from storage.user_store import UserStore

stripe.api_key = STRIPE_API_KEY
billing_router = APIRouter(prefix='/api/billing', tags=['Billing'])


async def validate_billing_enabled() -> None:
    """Validate that the ENABLE_BILLING feature flag is enabled.

    Resolution goes through the feature flag service's default-flag pattern:
    DB row first, the registered env-var default as fallback, and the same
    fallback if evaluation errors.
    """
    if not await feature_flag_service.resolve('ENABLE_BILLING'):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                'Billing is disabled in this environment. '
                'Enable the ENABLE_BILLING feature flag (or the '
                'ENABLE_BILLING environment variable) to enable billing.'
            ),
        )


class BillingSessionType(Enum):
    DIRECT_PAYMENT = 'DIRECT_PAYMENT'
    MONTHLY_SUBSCRIPTION = 'MONTHLY_SUBSCRIPTION'


class GetCreditsResponse(BaseModel):
    credits: Decimal | None = None


class SubscriptionAccessResponse(BaseModel):
    start_at: datetime
    end_at: datetime
    created_at: datetime
    cancelled_at: datetime | None = None
    stripe_subscription_id: str | None = None


class CreateCheckoutSessionRequest(BaseModel):
    amount: int


class CreateBillingSessionResponse(BaseModel):
    redirect_url: str


class GetSessionStatusResponse(BaseModel):
    status: str
    customer_email: str


class LiteLlmUserInfo(typing.TypedDict, total=False):
    max_budget: float | None
    spend: float | None


def calculate_credits(user_info: LiteLlmUserInfo) -> float | None:
    max_budget = user_info.get('max_budget')
    if max_budget is None:
        return None
    spend = user_info.get('spend') or 0.0
    return max(max_budget - spend, 0.0)


# Endpoint to retrieve the current organization's credit balance
@billing_router.get('/credits')
async def get_credits(
    user_id: str = Depends(get_user_id),
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
) -> GetCreditsResponse:
    if not stripe_service.STRIPE_API_KEY:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Billing is not configured',
        )
    user_team_info = await LiteLlmManager.get_user_team_info(
        user_id, str(effective_org_id)
    )
    budget_info = LiteLlmManager.get_budget_from_team_info(
        user_team_info, user_id, str(effective_org_id)
    )
    if budget_info is None:
        return GetCreditsResponse(credits=Decimal('0.00'))
    max_budget, spend = budget_info
    credits = calculate_credits({'max_budget': max_budget, 'spend': spend})
    if credits is None:
        return GetCreditsResponse(credits=Decimal('0.00'))
    return GetCreditsResponse(credits=Decimal('{:.2f}'.format(credits)))


# Endpoint to retrieve user's current subscription access
@billing_router.get('/subscription-access')
async def get_subscription_access(
    user_id: str = Depends(get_user_id),
) -> SubscriptionAccessResponse | None:
    """Get details of the currently valid subscription for the user."""
    async with a_session_maker() as session:
        now = datetime.now(UTC)
        result = await session.execute(
            select(SubscriptionAccess).where(
                SubscriptionAccess.status == 'ACTIVE',
                SubscriptionAccess.user_id == user_id,
                SubscriptionAccess.start_at <= now,
                SubscriptionAccess.end_at >= now,
            )
        )
        subscription_access = result.scalar_one_or_none()
        if not subscription_access:
            return None
        return SubscriptionAccessResponse(
            start_at=subscription_access.start_at,
            end_at=subscription_access.end_at,
            created_at=subscription_access.created_at,
            cancelled_at=subscription_access.cancelled_at,
            stripe_subscription_id=subscription_access.stripe_subscription_id,
        )


# Endpoint to check if a user has entered a payment method into stripe
@billing_router.post('/has-payment-method')
async def has_payment_method(
    user_id: str = Depends(get_user_id),
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
) -> bool:
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return await stripe_service.has_payment_method_by_user_id(
        user_id, org_id=effective_org_id
    )


# Endpoint to create a new setup intent in stripe
@billing_router.post('/create-customer-setup-session')
async def create_customer_setup_session(
    request: Request,
    user_id: str = Depends(get_user_id),
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
) -> CreateBillingSessionResponse:
    await validate_billing_enabled()
    customer_info = await stripe_service.find_or_create_customer_by_user_id(
        user_id, org_id=effective_org_id
    )
    if not customer_info:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Could not find or create customer for user',
        )
    base_url = get_web_url(request)
    checkout_session = await stripe.checkout.Session.create_async(
        customer=customer_info['customer_id'],
        mode='setup',
        payment_method_types=['card'],
        success_url=f'{base_url}?setup=success',
        cancel_url=f'{base_url}',
    )
    return CreateBillingSessionResponse(redirect_url=checkout_session.url)  # type: ignore[arg-type]


# Endpoint to create a new Stripe checkout session for credit purchase
@billing_router.post('/create-checkout-session')
async def create_checkout_session(
    body: CreateCheckoutSessionRequest,
    request: Request,
    user_id: str = Depends(get_user_id),
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
) -> CreateBillingSessionResponse:
    await validate_billing_enabled()
    base_url = get_web_url(request)
    customer_info = await stripe_service.find_or_create_customer_by_user_id(
        user_id, org_id=effective_org_id
    )
    if not customer_info:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Could not find or create customer for user',
        )
    checkout_session = await stripe.checkout.Session.create_async(
        customer=customer_info['customer_id'],
        line_items=[
            {
                'price_data': {
                    'unit_amount': body.amount * 100,
                    'currency': 'usd',
                    'product_data': {
                        'name': 'OpenHands Credits',
                        'tax_code': 'txcd_10000000',
                    },
                    'tax_behavior': 'exclusive',
                },
                'quantity': 1,
            },
        ],
        mode='payment',
        payment_method_types=['card'],
        saved_payment_method_options={
            'payment_method_save': 'enabled',
        },
        success_url=f'{base_url}/api/billing/success?session_id={{CHECKOUT_SESSION_ID}}',
        cancel_url=f'{base_url}/api/billing/cancel?session_id={{CHECKOUT_SESSION_ID}}',
    )
    logger.info(
        'created_stripe_checkout_session',
        extra={
            'stripe_customer_id': customer_info['customer_id'],
            'user_id': user_id,
            'org_id': customer_info['org_id'],
            'amount': body.amount,
            'checkout_session_id': checkout_session.id,
        },
    )
    async with a_session_maker() as session:
        billing_session = BillingSession(
            id=checkout_session.id,
            user_id=user_id,
            org_id=customer_info['org_id'],
            price=body.amount,
            price_code='NA',
        )
        session.add(billing_session)
        await session.commit()

    return CreateBillingSessionResponse(redirect_url=checkout_session.url)  # type: ignore[arg-type]


@billing_router.get('/success')
async def success_callback(session_id: str, request: Request):
    # We can't use the auth cookie because of SameSite=strict
    async with a_session_maker() as session:
        billing_session = await session.get(BillingSession, session_id)
        if billing_session is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST)
        org_id = billing_session.org_id
        if org_id is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail='Checkout organization is missing; payment requires reconciliation',
            )
        engine = budget_engine(session)

    try:
        async with budget_control_session(engine, org_id) as control:
            await _deliver_checkout_credit(control, session_id)
    except BudgetControlConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return RedirectResponse(
        f'{get_web_url(request)}/settings/billing?checkout=success', status_code=302
    )


async def _deliver_checkout_credit(control: BudgetControlSession, session_id: str):
    session = control.session
    org_id = control.org_id
    billing_session = await session.get(BillingSession, session_id)
    if billing_session is None or billing_session.org_id != org_id:
        raise BudgetControlConflict('Checkout organization changed')
    if billing_session.status == 'completed':
        return
    if billing_session.status != 'in_progress':
        raise HTTPException(status.HTTP_400_BAD_REQUEST)
    if await control.pending_operation() is not None:
        raise BudgetControlConflict(
            'Finish the pending budget operation before delivering credit'
        )
    pending = await control.pending_credit()
    if pending is not None and pending.id != session_id:
        raise BudgetControlConflict('Finish the earlier credit delivery first')

    stripe_session = await asyncio.to_thread(
        stripe.checkout.Session.retrieve, session_id
    )
    if stripe_session.status != 'complete' or stripe_session.payment_status != 'paid':
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail='Payment is not complete'
        )
    amount_subtotal = stripe_session.amount_subtotal
    if (
        not isinstance(amount_subtotal, int)
        or amount_subtotal <= 0
        or Decimal(amount_subtotal) != billing_session.price * 100
        or stripe_session.currency != 'usd'
        or stripe_session.mode != 'payment'
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail='Payment amount requires reconciliation'
        )
    add_credits = amount_subtotal / 100
    org = await session.get(Org, org_id)
    if org is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail='Checkout organization not found'
        )
    user = await UserStore.get_user_by_id(billing_session.user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='User not found')

    if billing_session.credit_target is None:
        user_team_info = await LiteLlmManager.get_user_team_info(
            billing_session.user_id, str(org_id)
        )
        budget_info = LiteLlmManager.get_budget_from_team_info(
            user_team_info, billing_session.user_id, str(org_id)
        )
        if budget_info is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail='Credit balance is temporarily unavailable',
            )
        max_budget, spend = budget_info
        budget_baseline = max(spend, max_budget if max_budget is not None else spend)
        target = budget_baseline + add_credits
        if (
            not math.isfinite(target)
            or not math.isfinite(spend)
            or (max_budget is not None and not math.isfinite(max_budget))
        ):
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, detail='Credit balance is invalid'
            )
        billing_session.credit_target = target
        billing_session.credit_budget_before = max_budget
        billing_session.updated_at = datetime.now(UTC)
        # Retry the absolute target after an uncertain response, never add twice.
        await session.commit()

    await LiteLlmManager.update_team_and_users_budget(
        str(org_id), billing_session.credit_target, billing_session_id=session_id
    )
    org.byor_export_enabled = True
    billing_session.status = 'completed'
    billing_session.updated_at = datetime.now(UTC)
    await session.commit()
    logger.info(
        'stripe_checkout_success',
        extra={'checkout_session_id': session_id, 'org_id': str(org_id)},
    )

    try:
        analytics = get_analytics_service()
        if analytics and user:
            from openhands.analytics.analytics_context import AnalyticsContext

            ctx = AnalyticsContext(
                user_id=billing_session.user_id,
                consented=user.user_consents_to_analytics is True,
                org_id=str(org_id),
                user=user,
            )
            analytics.track_credit_purchased(
                ctx=ctx,
                amount_usd=add_credits,
                credit_balance_before=billing_session.credit_budget_before,
                credit_balance_after=billing_session.credit_target,
            )
    except Exception:
        logger.exception('analytics:credit_purchased:failed', stack_info=True)


@billing_router.get('/cancel')
async def cancel_callback(session_id: str, request: Request):
    async with a_session_maker() as session:
        record = await session.get(BillingSession, session_id)
        org_id = record.org_id if record is not None else None
        engine = budget_engine(session)
    if org_id is not None:
        try:
            async with budget_control_session(engine, org_id) as control:
                await control.session.execute(
                    update(BillingSession)
                    .where(
                        BillingSession.id == session_id,
                        BillingSession.org_id == org_id,
                        BillingSession.status == 'in_progress',
                        BillingSession.credit_target.is_(None),
                    )
                    .values(status='cancelled', updated_at=datetime.now(UTC))
                )
                await control.session.commit()
        except BudgetControlConflict:
            # A cancel redirect cannot interrupt fulfillment already under way.
            pass

    return RedirectResponse(
        f'{get_web_url(request)}/settings/billing?checkout=cancel', status_code=302
    )
