"""On-demand check that a user's linked OAuth v2 git token still works.

A revoked token is only noticed when something uses it, and normal traffic
may never touch a given provider (``ProviderHandler.get_user`` stops at the
first one that works). This makes one cheap call to the provider's own API
with the stored token and records the verdict in ``oauth_tokens.invalidated_at``.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Literal
from uuid import UUID

from pydantic import SecretStr

from openhands.app_server.integrations.provider import ProviderHandler, ProviderToken
from openhands.app_server.integrations.service_types import (
    AuthenticationError,
    ProviderType,
)
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.oauth_v2_refresh import (
    RefreshTokenRejectedError,
    get_permitted_drift_seconds,
    make_refresh_callback,
)
from storage.auth_provider import AuthProvider
from storage.oauth_token_store import OAuthTokenStore, unwrap_token

# ``unavailable``: the provider could not be asked (timeout, 5xx, rate limit),
# so nothing is recorded.
GitTokenCheckStatus = Literal['valid', 'invalid', 'unavailable']


async def check_git_provider_token(
    user_id: UUID,
    provider: AuthProvider,
    provider_type: ProviderType,
    host: str | None,
) -> GitTokenCheckStatus | None:
    """Check the user's token for ``provider``; ``None`` if none is linked."""
    store = OAuthTokenStore(user_id=user_id, oauth_provider_id=provider.id)
    row = await store.get_raw()
    if row is None:
        return None
    log_extra = {'user_id': str(user_id), 'oauth_provider_id': provider.id}

    try:
        # Refreshes only if the access token has expired, as any use would.
        token = await store.get_valid_access_token(
            permitted_drift_seconds=get_permitted_drift_seconds(provider),
            refresh=make_refresh_callback(provider),
        )
    except RefreshTokenRejectedError:
        stale_token = unwrap_token(row.access_token)
        if stale_token is not None:
            await store.set_invalidated(stale_token, True)
        logger.info('git_token_check_refresh_rejected', extra=log_extra)
        return 'invalid'
    except Exception:
        logger.warning('git_token_check_refresh_failed', extra=log_extra, exc_info=True)
        return 'unavailable'
    if token is None:
        return None

    handler = ProviderHandler(
        provider_tokens=MappingProxyType(
            {provider_type: ProviderToken(token=SecretStr(token), host=host)}
        )
    )
    try:
        await handler.get_service(provider_type).get_user()
    except AuthenticationError:
        await store.set_invalidated(token, True)
        logger.info('git_token_check_rejected', extra=log_extra)
        return 'invalid'
    except Exception:
        logger.warning('git_token_check_failed', extra=log_extra, exc_info=True)
        return 'unavailable'
    await store.set_invalidated(token, False)
    return 'valid'
