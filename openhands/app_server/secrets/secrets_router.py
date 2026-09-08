"""Secrets router for OpenHands App Server.

This module provides the V1 API routes for secrets under /api/v1/secrets.
"""

import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.asyncio.lock import Lock as RedisLock
from redis.exceptions import LockError as RedisLockError

from openhands.app_server.config_api.config_models import AppMode
from openhands.app_server.errors import AuthError
from openhands.app_server.integrations.provider import (
    PROVIDER_TOKEN_TYPE,
    CustomSecret,
    ProviderType,
)
from openhands.app_server.integrations.utils import validate_provider_token
from openhands.app_server.secrets.secrets_models import (
    CustomSecretCreate,
    CustomSecretPage,
    CustomSecretWithoutValue,
    Secrets,
)
from openhands.app_server.secrets.secrets_store import SecretsStore
from openhands.app_server.settings.settings_models import POSTProviderModel
from openhands.app_server.user_auth import (
    get_provider_tokens,
    get_secrets,
    get_secrets_store,
    get_user_id,
)
from openhands.app_server.utils.dependencies import get_dependencies
from openhands.app_server.utils.models import EditResponse
from openhands.app_server.utils.redis import get_redis_client_async, redis_exceptions
from openhands.sdk.agent.acp_file_credentials import (
    CODEX_AUTH_SECRET_NAME,
    is_valid_codex_auth,
)

_logger = logging.getLogger(__name__)

_INVALID_CODEX_AUTH_DETAIL = (
    'CODEX_AUTH_JSON must contain ChatGPT-mode Codex auth JSON with a non-empty '
    'tokens.refresh_token. For API-key mode, save the key as OPENAI_API_KEY instead.'
)


def _validate_codex_auth_secret(name: str, value: str) -> None:
    if name == CODEX_AUTH_SECRET_NAME and not is_valid_codex_auth(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_CODEX_AUTH_DETAIL,
        )


# Create router with /api/v1/secrets prefix
router = APIRouter(
    prefix='/secrets',
    tags=['Secrets'],
    dependencies=get_dependencies(),
)


# =================================================
# SECTION: Write-lock helpers
# =================================================
#
# The custom-secrets and git-provider endpoints below all follow the same
# load -> mutate -> store shape, which is unsafe under concurrency: two
# requests that read the same baseline can each store their own delta and
# silently drop the other's changes (issue OHE-3052). We serialize the
# critical section per (user, org) so a losing update can't happen.
#
# In OSS mode the lock is a single-process asyncio.Lock keyed on user (and
# the effective org, when the store exposes one). In SaaS mode we acquire a
# Redis distributed lock so multiple worker processes serialize against
# each other; if Redis is unreachable in SaaS we fail the request rather
# than silently re-introduce the race. The Redis TTL guards against a
# crashed worker leaving the lock held forever.

_SECRETS_WRITE_LOCK_KEY_PREFIX = 'app_secrets:write'
# TTL acts as a safety net if a worker crashes mid-operation. The store
# calls themselves are quick, but add headroom for slow DB writes.
_SECRETS_WRITE_LOCK_TTL_SECONDS = 30
# How long to wait for an in-flight peer to finish before giving up.
_SECRETS_WRITE_LOCK_BLOCKING_TIMEOUT_SECONDS = 10

# In-process locks, used in OSS mode. defaultdict is fine here: each lock
# is bound to the event loop that first awaited it, and FastAPI typically
# runs a single loop per process. See _reset_user_secrets_locks for test
# cleanup.
_user_secrets_write_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _secrets_write_lock_key(user_id: str | None, secrets_store: SecretsStore) -> str:
    """Build the per-user (+ per-org) lock key for serializing secret writes."""
    org_id = getattr(secrets_store, 'effective_org_id', None)
    org_part = str(org_id) if org_id else 'default'
    user_part = user_id or '<anonymous>'
    return f'{_SECRETS_WRITE_LOCK_KEY_PREFIX}:{user_part}:{org_part}'


def _is_saas_mode() -> bool:
    """Return True if the app server is running in SaaS mode."""
    try:
        from openhands.app_server.config import get_global_config

        return get_global_config().app_mode == AppMode.SAAS
    except Exception:
        # If config is unavailable (e.g. before lifespan startup) default to
        # OSS semantics — there's only one process to coordinate.
        return False


def _reset_user_secrets_locks() -> None:
    """Test-only helper: drop in-process locks bound to dead event loops."""
    _user_secrets_write_locks.clear()


@asynccontextmanager
async def _secrets_write_lock(
    user_id: str | None, secrets_store: SecretsStore
) -> AsyncIterator[None]:
    """Serialize read-modify-write secret mutations for a single user.

    In SaaS mode this acquires a Redis distributed lock so multiple workers
    can't drop each other's writes. In OSS mode it falls back to a single-
    process asyncio.Lock, which is sufficient because there's only one
    process. On SaaS, Redis being unavailable causes a 503 — failing the
    request is safer than letting the race come back.
    """
    lock_key = _secrets_write_lock_key(user_id, secrets_store)
    if _is_saas_mode():
        redis = get_redis_client_async()
        redis_lock: RedisLock | None = None
        try:
            redis_lock = redis.lock(lock_key, timeout=_SECRETS_WRITE_LOCK_TTL_SECONDS)
            acquired = await redis_lock.acquire(
                blocking=True,
                blocking_timeout=_SECRETS_WRITE_LOCK_BLOCKING_TIMEOUT_SECONDS,
            )
        except redis_exceptions.RedisError as exc:
            _logger.warning(
                'secrets_write_lock:redis_unavailable',
                extra={'user_id': user_id, 'error': str(exc)},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail='Could not acquire secrets write lock',
            ) from exc

        if not acquired:
            _logger.warning(
                'secrets_write_lock:lock_timeout',
                extra={'user_id': user_id, 'lock_key': lock_key},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail='Timed out waiting for secrets write lock',
            )

        try:
            yield
        finally:
            try:
                await redis_lock.release()
            except RedisLockError:
                _logger.warning(
                    'secrets_write_lock:release_failed',
                    extra={'user_id': user_id, 'lock_key': lock_key},
                )
        return

    # OSS / single-process path.
    async with _user_secrets_write_locks[lock_key]:
        yield


# =================================================
# SECTION: Helper functions for git providers
# =================================================


def _check_token_type(
    confirmed_token_type: ProviderType | None, token_type: ProviderType
) -> None:
    """Returns error message if token type doesn't match, None otherwise."""
    if not confirmed_token_type or confirmed_token_type != token_type:
        raise AuthError(
            f'Invalid token. Please make sure it is a valid {token_type.value} token.'
        )


async def check_provider_tokens(
    incoming_provider_tokens: POSTProviderModel,
    existing_provider_tokens: PROVIDER_TOKEN_TYPE | None,
) -> None:
    if incoming_provider_tokens.provider_tokens:
        # Determine whether tokens are valid
        for token_type, token_value in incoming_provider_tokens.provider_tokens.items():
            if token_value.token:
                confirmed_token_type = await validate_provider_token(
                    token_value.token, token_value.host
                )  # FE always sends latest host
                _check_token_type(confirmed_token_type, token_type)

            existing_token = (
                existing_provider_tokens.get(token_type, None)
                if existing_provider_tokens
                else None
            )
            if (
                existing_token
                and (existing_token.host != token_value.host)
                and existing_token.token
            ):
                confirmed_token_type = await validate_provider_token(
                    existing_token.token, token_value.host
                )
                # Host has changed, check it against existing token
                _check_token_type(confirmed_token_type, token_type)


# =================================================
# SECTION: Git Provider Token Endpoints
# =================================================


@router.post(
    '/git-providers',
    tags=['Git'],
)
async def store_provider_tokens(
    provider_info: POSTProviderModel,
    secrets_store: SecretsStore = Depends(get_secrets_store),
    provider_tokens: PROVIDER_TOKEN_TYPE | None = Depends(get_provider_tokens),
    user_id: str | None = Depends(get_user_id),
) -> EditResponse:
    """Store git provider tokens.

    Saves the git provider tokens (GitHub, GitLab, Bitbucket, etc.) for the authenticated user.

    Returns:
        200: Git providers stored successfully
        401: Invalid token
        500: Error storing git providers
    """
    await check_provider_tokens(provider_info, provider_tokens)

    async with _secrets_write_lock(user_id, secrets_store):
        user_secrets = await secrets_store.load()
        if not user_secrets:
            user_secrets = Secrets()

        if provider_info.provider_tokens:
            existing_providers = [provider for provider in user_secrets.provider_tokens]

            # Merge incoming settings store with the existing one
            for provider, token_value in list(provider_info.provider_tokens.items()):
                if provider in existing_providers and not token_value.token:
                    existing_token = user_secrets.provider_tokens.get(provider)
                    if existing_token and existing_token.token:
                        provider_info.provider_tokens[provider] = existing_token

                provider_info.provider_tokens[provider] = provider_info.provider_tokens[
                    provider
                ].model_copy(update={'host': token_value.host})

        updated_secrets = user_secrets.model_copy(
            update={'provider_tokens': provider_info.provider_tokens}
        )
        await secrets_store.store(updated_secrets)

    # ACTV-02: git provider connected analytics
    from openhands.analytics import get_analytics_service, resolve_analytics_context

    analytics = get_analytics_service()
    if analytics and user_id and provider_info.provider_tokens:
        ctx = await resolve_analytics_context(user_id)
        for provider_type, token_value in provider_info.provider_tokens.items():
            # Only fire for providers with actual token, not host-only updates
            if token_value.token:
                analytics.track_git_provider_connected(
                    ctx=ctx,
                    provider_type=provider_type.value,
                )

    return EditResponse(
        message='Git providers stored',
    )


@router.delete(
    '/git-providers',
    tags=['Git'],
)
async def unset_provider_tokens(
    secrets_store: SecretsStore = Depends(get_secrets_store),
    user_id: str | None = Depends(get_user_id),
) -> EditResponse:
    """Unset (delete) all git provider tokens.

    Removes all git provider tokens for the authenticated user.

    Returns:
        200: Git provider tokens unset successfully
        500: Error unsetting git provider tokens
    """
    async with _secrets_write_lock(user_id, secrets_store):
        user_secrets = await secrets_store.load()
        if user_secrets:
            updated_secrets = user_secrets.model_copy(update={'provider_tokens': {}})
            await secrets_store.store(updated_secrets)

    return EditResponse(message='Unset Git provider tokens')


# =================================================
# SECTION: Custom Secrets Endpoints
# =================================================


@router.get('/search')
async def search_custom_secrets(
    name__contains: Annotated[
        str | None,
        Query(title='Filter by name containing this string'),
    ] = None,
    page_id: Annotated[
        str | None,
        Query(title='Optional next_page_id from the previously returned page'),
    ] = None,
    limit: Annotated[
        int,
        Query(
            title='The max number of results in the page',
            gt=0,
            le=100,
        ),
    ] = 100,
    user_secrets: Secrets | None = Depends(get_secrets),
) -> CustomSecretPage:
    """Search / List custom secrets.

    Retrieves the names and descriptions of custom secrets for the authenticated user.
    Results are paginated and can be filtered by name.

    In SaaS mode, includes the system-generated OPENHANDS_API_KEY which cannot be deleted.

    Returns:
        CustomSecretPage: Paginated list of custom secrets (without values)
    """
    if not user_secrets or not user_secrets.custom_secrets:
        return CustomSecretPage(items=[], next_page_id=None)

    # Build list of all secrets, optionally filtered by name
    all_secrets: list[CustomSecretWithoutValue] = []
    for secret_name, secret_value in sorted(user_secrets.custom_secrets.items()):
        if name__contains and name__contains.lower() not in secret_name.lower():
            continue
        all_secrets.append(
            CustomSecretWithoutValue.model_construct(
                name=secret_name,
                description=secret_value.description,
            )
        )

    # Apply pagination
    start_index = 0
    if page_id:
        # Find the index after the page_id secret
        for i, secret in enumerate(all_secrets):
            if secret.name == page_id:
                start_index = i + 1
                break

    # Get the page of results
    end_index = start_index + limit
    page_items = all_secrets[start_index:end_index]

    # Determine next_page_id
    next_page_id = None
    if end_index < len(all_secrets):
        next_page_id = page_items[-1].name if page_items else None

    return CustomSecretPage(items=page_items, next_page_id=next_page_id)


@router.post('', status_code=status.HTTP_201_CREATED)
async def create_custom_secret(
    incoming_secret: CustomSecretCreate,
    secrets_store: SecretsStore = Depends(get_secrets_store),
    user_id: str | None = Depends(get_user_id),
) -> EditResponse:
    """Create or update a custom secret.

    Creates a new custom secret, or overwrites it if it already exists.

    Returns:
        201: Secret saved successfully
        500: Error saving secret
    """
    _validate_codex_auth_secret(
        incoming_secret.name, incoming_secret.value.get_secret_value()
    )

    async with _secrets_write_lock(user_id, secrets_store):
        existing_secrets = await secrets_store.load()
        custom_secrets = (
            dict(existing_secrets.custom_secrets) if existing_secrets else {}
        )

        secret_name = incoming_secret.name
        secret_value = incoming_secret.value
        secret_description = incoming_secret.description

        existing_description = (
            custom_secrets[secret_name].description
            if secret_name in custom_secrets
            else ''
        )
        custom_secrets[secret_name] = CustomSecret(
            secret=secret_value,
            description=secret_description
            if secret_description is not None
            else existing_description,
        )

        # Create a new Secrets that preserves provider tokens
        updated_user_secrets = Secrets(
            custom_secrets=custom_secrets,  # type: ignore[arg-type]
            provider_tokens=existing_secrets.provider_tokens
            if existing_secrets
            else {},  # type: ignore[arg-type]
        )

        await secrets_store.store(updated_user_secrets)

    return EditResponse(
        message='Secret created successfully',
    )


@router.put('/{secret_id}')
async def update_custom_secret(
    secret_id: str,
    incoming_secret: CustomSecretWithoutValue,
    secrets_store: SecretsStore = Depends(get_secrets_store),
    user_id: str | None = Depends(get_user_id),
) -> EditResponse:
    """Update a custom secret.

    Updates the name and/or description of an existing custom secret.

    Returns:
        200: Secret updated successfully
        400: Secret name already exists
        404: Secret not found
        500: Error updating secret
    """
    async with _secrets_write_lock(user_id, secrets_store):
        existing_secrets = await secrets_store.load()
        if not existing_secrets or secret_id not in existing_secrets.custom_secrets:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f'Secret with ID {secret_id} not found',
            )

        secret_name = incoming_secret.name
        secret_description = incoming_secret.description

        custom_secrets = dict(existing_secrets.custom_secrets)
        existing_secret = custom_secrets.pop(secret_id)
        _validate_codex_auth_secret(
            secret_name, existing_secret.secret.get_secret_value()
        )

        if secret_name != secret_id and secret_name in custom_secrets:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Secret {secret_name} already exists',
            )

        custom_secrets[secret_name] = CustomSecret(
            secret=existing_secret.secret,
            description=secret_description or '',
        )

        updated_secrets = Secrets(
            custom_secrets=custom_secrets,  # type: ignore[arg-type]
            provider_tokens=existing_secrets.provider_tokens,
        )

        await secrets_store.store(updated_secrets)

    return EditResponse(
        message='Secret updated successfully',
    )


@router.delete('/{secret_id}')
async def delete_custom_secret(
    secret_id: str,
    secrets_store: SecretsStore = Depends(get_secrets_store),
    user_id: str | None = Depends(get_user_id),
) -> EditResponse:
    """Delete a custom secret.

    Removes a custom secret for the authenticated user.

    Returns:
        200: Secret deleted successfully
        404: Secret not found
        500: Error deleting secret
    """
    async with _secrets_write_lock(user_id, secrets_store):
        existing_secrets = await secrets_store.load()
        if existing_secrets:
            # Get existing custom secrets
            custom_secrets = dict(existing_secrets.custom_secrets)

            # Check if the secret to delete exists
            if secret_id not in custom_secrets:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f'Secret with ID {secret_id} not found',
                )

            # Remove the secret
            custom_secrets.pop(secret_id)

            # Create a new Secrets that preserves provider tokens and remaining secrets
            updated_secrets = Secrets(
                custom_secrets=custom_secrets,  # type: ignore[arg-type]
                provider_tokens=existing_secrets.provider_tokens,
            )

            await secrets_store.store(updated_secrets)

    return EditResponse(
        message='Secret deleted successfully',
    )
