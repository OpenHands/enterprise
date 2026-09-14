"""Deliver a paid checkout once, preserving its target across uncertain responses."""

import asyncio
import math
from datetime import UTC, datetime
from decimal import Decimal

import stripe
from fastapi import HTTPException, status

from openhands.analytics import get_analytics_service
from server.constants import STRIPE_API_KEY
from server.logger import logger
from storage.billing_session import BillingSession
from storage.budget_control import BudgetControlConflict, BudgetControlSession
from storage.lite_llm_manager import LiteLlmManager
from storage.org import Org
from storage.user_store import UserStore


async def deliver_checkout_credit(control: BudgetControlSession, session_id: str):
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
        stripe.checkout.Session.retrieve, session_id, api_key=STRIPE_API_KEY
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
    user = await UserStore.get_user_by_id(
        billing_session.user_id, allow_migration=False
    )
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
