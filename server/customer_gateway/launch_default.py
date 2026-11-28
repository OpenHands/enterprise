"""Admin-approved default alias for a gateway org's launch that selected no model.

Selection only names a catalog alias; ``service.bind_start_request`` binds it to the
relay and refuses the launch if the sandbox lost that org's gateway meanwhile."""

from __future__ import annotations

from uuid import UUID

from openhands.sdk import LLM
from server.customer_gateway.config import get_settings
from server.customer_gateway.profile import parse_profile
from server.customer_gateway.service import (
    INTERACTIVE_TRIGGERS,
    CustomerGatewayLaunchError,
)
from storage.customer_gateway_store import CustomerGatewayDenied, CustomerGatewayStore


async def select_launch_default_llm(
    llm: LLM, credential_org_id: UUID | None, user_id: str | None, trigger: str | None
) -> LLM | None:
    """``llm`` with the org's default alias, or None when none is configured or the
    gateway does not apply. Raises ``CustomerGatewayLaunchError`` when it is not granted."""
    settings = get_settings()
    if settings is None or credential_org_id is None or not user_id:
        return None
    gw = await CustomerGatewayStore.get_enabled_org_gateway(credential_org_id)
    if gw is None or trigger not in INTERACTIVE_TRIGGERS:
        return None
    alias = parse_profile(gw.profile, allow_http=settings.allow_http).default_alias
    if alias is None:
        return None
    try:
        granted = await CustomerGatewayStore.current_allowed_models(
            credential_org_id, UUID(user_id)
        )
    except CustomerGatewayDenied as exc:
        raise CustomerGatewayLaunchError(
            f'customer gateway access denied: {exc.reason}'
        ) from exc
    if alias not in granted:
        raise CustomerGatewayLaunchError(
            f'The customer gateway default model {alias!r} is not granted to you. '
            'Select a granted model, or ask an administrator for access.'
        )
    return llm.model_copy(update={'model': alias, 'base_url': None, 'api_key': None})
