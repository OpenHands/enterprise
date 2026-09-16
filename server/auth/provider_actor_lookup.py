"""Provider actor lookup is distinct from a browser account-linking proof."""

from typing import Protocol

from openhands.app_server.integrations.service_types import ProviderType
from server.auth.token_manager import TokenManager
from server.logger import logger

USER_ID_CACHE_PREFIX = 'automation:idp_to_kc_user'
USER_ID_CACHE_TTL_SECONDS = 86400


class ActorLookupContext(Protocol):
    token_manager: TokenManager

    async def _get_cached_value(self, cache_key: str) -> str | None: ...
    async def _set_cached_value(self, cache_key: str, value: str, ttl: int) -> None: ...


class ProviderActorLookup(Protocol):
    async def for_automation(
        self,
        provider: ProviderType,
        provider_user_id: str | int,
        context: ActorLookupContext,
    ) -> str | None: ...


class KeycloakProviderActorLookup:
    async def for_automation(
        self,
        provider: ProviderType,
        provider_user_id: str | int,
        context: ActorLookupContext,
    ) -> str | None:
        cache_key = f'{USER_ID_CACHE_PREFIX}:{provider.value}:{provider_user_id}'

        # Check cache first
        cached = await context._get_cached_value(cache_key)
        if cached is not None:
            if cached == 'none':
                logger.debug(
                    f'[AutomationEventService] Cache hit (negative): '
                    f'{provider.value} user {provider_user_id} not in Keycloak'
                )
                return None
            logger.debug(
                f'[AutomationEventService] Cache hit: '
                f'{provider.value} user {provider_user_id} -> Keycloak {cached}'
            )
            return cached

        # Cache miss - query Keycloak
        try:
            keycloak_id = await context.token_manager.get_user_id_from_idp_user_id(
                str(provider_user_id), provider
            )

            # Cache the result (including negative results)
            if keycloak_id:
                await context._set_cached_value(
                    cache_key, keycloak_id, USER_ID_CACHE_TTL_SECONDS
                )
            else:
                # Cache negative result to prevent repeated Keycloak queries
                await context._set_cached_value(
                    cache_key, 'none', USER_ID_CACHE_TTL_SECONDS
                )

            return keycloak_id
        except Exception as e:
            # Log at warning level to surface programmer errors and API issues
            logger.warning(
                f'[AutomationEventService] Failed to get keycloak ID for '
                f'{provider.value} user {provider_user_id}: {e}'
            )
            return None


class OpenHandsProviderActorLookup:
    async def for_automation(
        self,
        provider: ProviderType,
        provider_user_id: str | int,
        context: ActorLookupContext,
    ) -> str | None:
        # Direct Git is outside this installation's supported capabilities.
        # Never consult a stale Keycloak actor cache in an OpenHands install.
        return None
