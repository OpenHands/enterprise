"""Admin API for the service accounts of an organization.

Every route needs ``MANAGE_SERVICE_ACCOUNTS`` (org owner or admin). A route
with ``{sa_id}`` answers 404 unless ``sa_id`` is a service account that is a
member of ``{org_id}``, so an admin can never reach a human through it.
"""

from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from openhands.app_server.secrets.secrets_models import (
    CustomSecretPage,
    CustomSecretScope,
    CustomSecretWithoutValue,
)
from openhands.app_server.secrets.secrets_router import _secrets_write_lock
from openhands.app_server.utils.env_var_validation import validate_env_var_name
from openhands.app_server.utils.http_session import httpx_verify_option
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.app_server.utils.models import EditResponse
from server.auth.authorization import Permission, require_permission
from server.auth.constants import AUTOMATION_SERVICE_URL
from server.auth.org_context import REJECT_X_ORG_ID_PATH_MISMATCH
from server.routes.org_models import (
    LiteLLMIntegrationError,
    OrgNotFoundError,
    ServiceAccountCreate,
    ServiceAccountPage,
    ServiceAccountResponse,
    ServiceAccountSecretUpsert,
    ServiceAccountUpdate,
)
from server.services.admin_user_lifecycle_service import (
    AdminUserLifecycleService,
    LifecycleCleanupError,
)
from server.services.service_account_service import (
    ServiceAccountNameExistsError,
    ServiceAccountService,
)
from storage.lite_llm_manager import is_litellm_enabled
from storage.saas_secrets_store import SaasSecretsStore
from storage.saas_settings_store import ManagedLlmKeyStatus, SaasSettingsStore
from storage.user_data_cleanup import UserCleanupConflict

service_accounts_router = APIRouter(
    prefix='/api/organizations',
    tags=['Service Accounts'],
    dependencies=[REJECT_X_ORG_ID_PATH_MISMATCH],
)

# Injected into every run from the SA's own key (``SaasUserAuth.get_secrets``).
RESERVED_SECRET_NAMES = frozenset({'OPENHANDS_API_KEY'})
_AUTOMATION_CHECK_TIMEOUT_SECONDS = 5
# The credential headers that ``get_api_key_from_header`` accepts, plus the
# session cookie. The Automation Service authenticates the same way.
_FORWARDED_AUTH_HEADERS = (
    'cookie',
    'authorization',
    'x-session-api-key',
    'x-access-token',
)
# The Automation Service caps a page at 100 automations.
_AUTOMATION_PAGE_SIZE = 100


def _log(op: str, caller_user_id: str, org_id: UUID, sa_id: UUID | None) -> None:
    logger.info(
        f'org:service_accounts:{op}',
        extra={
            'caller_user_id': caller_user_id,
            'org_id': str(org_id),
            'service_account_id': str(sa_id) if sa_id else None,
        },
    )


async def _get_or_404(org_id: UUID, sa_id: UUID) -> ServiceAccountResponse:
    """Return the service account, or 404 if it is not a service account of the org."""
    service_account = await ServiceAccountService.get_service_account(org_id, sa_id)
    if service_account is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail='Service account not found'
        )
    return service_account


async def _owns_automation(org_id: UUID, sa_id: UUID, request: Request) -> bool:
    """Ask the Automation Service whether ``sa_id`` owns an automation.

    Automations live in that service's own database. The caller's own
    credentials are forwarded, as in ``instance_admin._org_has_automation``.
    ``GET /v1`` has no owner filter, so this pages through the org's
    automations and compares ``user_id`` here.

    If no Automation Service URL is configured, no automation can exist, so
    the answer is ``False``. Fails closed otherwise: any error reaching the
    service raises 503.
    """
    if not AUTOMATION_SERVICE_URL:
        return False
    headers = {
        name: value
        for name in _FORWARDED_AUTH_HEADERS
        if (value := request.headers.get(name))
    }
    headers['X-Org-Id'] = str(org_id)
    url = f'{AUTOMATION_SERVICE_URL.rstrip("/")}/v1'
    offset = 0
    try:
        async with httpx.AsyncClient(
            verify=httpx_verify_option(), timeout=_AUTOMATION_CHECK_TIMEOUT_SECONDS
        ) as client:
            while True:
                response = await client.get(
                    url,
                    params={'limit': _AUTOMATION_PAGE_SIZE, 'offset': offset},
                    headers=headers,
                )
                response.raise_for_status()
                body = response.json()
                automations = body['automations']
                if any(a['user_id'] == str(sa_id) for a in automations):
                    return True
                offset += len(automations)
                if not automations or offset >= body['total']:
                    return False
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning(
            'org:service_accounts:automation_check_failed',
            extra={'org_id': str(org_id), 'error': str(exc)},
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Could not check the service account automations. Retry.',
        ) from exc


@service_accounts_router.post(
    '/{org_id}/service-accounts',
    status_code=status.HTTP_201_CREATED,
    response_model=ServiceAccountResponse,
)
async def create_service_account(
    org_id: UUID,
    body: ServiceAccountCreate,
    user_id: str = Depends(require_permission(Permission.MANAGE_SERVICE_ACCOUNTS)),
) -> ServiceAccountResponse:
    """Create a service account in the org. 409 if the name is taken."""
    try:
        user = await ServiceAccountService.create_service_account(org_id, body.name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OrgNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ServiceAccountNameExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LiteLLMIntegrationError as exc:
        logger.exception('LiteLLM integration failed for a service account')
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to create LiteLLM integration',
        ) from exc
    _log('create', user_id, org_id, user.id)
    return await _get_or_404(org_id, user.id)


@service_accounts_router.get(
    '/{org_id}/service-accounts', response_model=ServiceAccountPage
)
async def list_service_accounts(
    org_id: UUID,
    page_id: Annotated[
        str | None, Query(title='Optional page offset for pagination')
    ] = None,
    limit: Annotated[
        int, Query(title='The max number of results in the page', gt=0, le=100)
    ] = 10,
    user_id: str = Depends(require_permission(Permission.MANAGE_SERVICE_ACCOUNTS)),
) -> ServiceAccountPage:
    """List the org's service accounts by name."""
    try:
        offset = int(page_id) if page_id else 0
        if offset < 0:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail='Invalid page_id format'
        ) from exc
    # One extra row tells whether another page exists.
    rows = await ServiceAccountService.list_service_accounts(
        org_id, offset=offset, limit=limit + 1
    )
    has_more = len(rows) > limit
    return ServiceAccountPage(
        items=rows[:limit], next_page_id=str(offset + limit) if has_more else None
    )


@service_accounts_router.get(
    '/{org_id}/service-accounts/{sa_id}', response_model=ServiceAccountResponse
)
async def get_service_account(
    org_id: UUID,
    sa_id: UUID,
    user_id: str = Depends(require_permission(Permission.MANAGE_SERVICE_ACCOUNTS)),
) -> ServiceAccountResponse:
    """Get one service account of the org."""
    return await _get_or_404(org_id, sa_id)


@service_accounts_router.patch(
    '/{org_id}/service-accounts/{sa_id}', response_model=ServiceAccountResponse
)
async def update_service_account(
    org_id: UUID,
    sa_id: UUID,
    body: ServiceAccountUpdate,
    user_id: str = Depends(require_permission(Permission.MANAGE_SERVICE_ACCOUNTS)),
) -> ServiceAccountResponse:
    """Rename a service account. 409 if the name is taken."""
    try:
        renamed = await ServiceAccountService.rename_service_account(
            org_id, sa_id, body.name
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ServiceAccountNameExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if renamed is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail='Service account not found'
        )
    _log('update', user_id, org_id, sa_id)
    return renamed


@service_accounts_router.post(
    '/{org_id}/service-accounts/{sa_id}/disable',
    response_model=ServiceAccountResponse,
)
async def disable_service_account(
    org_id: UUID,
    sa_id: UUID,
    user_id: str = Depends(require_permission(Permission.MANAGE_SERVICE_ACCOUNTS)),
) -> ServiceAccountResponse:
    """Disable a service account and revoke its keys and tokens.

    Uses ``AdminUserLifecycleService``, which also deletes its LiteLLM keys.
    Retry after a 503.
    """
    await _get_or_404(org_id, sa_id)
    try:
        await AdminUserLifecycleService().disable_user(str(sa_id))
    except LifecycleCleanupError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    _log('disable', user_id, org_id, sa_id)
    return await _get_or_404(org_id, sa_id)


@service_accounts_router.post(
    '/{org_id}/service-accounts/{sa_id}/enable',
    response_model=ServiceAccountResponse,
)
async def enable_service_account(
    org_id: UUID,
    sa_id: UUID,
    user_id: str = Depends(require_permission(Permission.MANAGE_SERVICE_ACCOUNTS)),
) -> ServiceAccountResponse:
    """Enable a service account and make sure it has a valid managed LLM key.

    Disabling deleted its LiteLLM keys, so a disabled account gets a forced
    rotation. An account that is already enabled keeps a valid key, because
    in-flight sandboxes hold it (enterprise#439); a missing or stale key is
    still minted. Retry after a 503: enabling is idempotent, and the retry
    restores the key if the rotation failed.
    """
    was_disabled = (await _get_or_404(org_id, sa_id)).is_disabled
    try:
        await AdminUserLifecycleService().enable_user(str(sa_id))
    except (LifecycleCleanupError, UserCleanupConflict) as exc:
        code = (
            status.HTTP_409_CONFLICT
            if isinstance(exc, UserCleanupConflict)
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(code, str(exc)) from exc
    if await is_litellm_enabled():
        try:
            store = await SaasSettingsStore.get_instance(
                str(sa_id), effective_org_id=org_id
            )
            if was_disabled:
                rotation = await store.rotate_managed_llm_key()
            else:
                # Rotates only if the key is missing or no longer valid.
                await store.resolve_valid_managed_llm_key()
                rotation = None
        except Exception as exc:
            logger.exception(
                'org:service_accounts:rotate_failed',
                extra={'org_id': str(org_id), 'service_account_id': str(sa_id)},
            )
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail='Service account enabled, but its LLM key was not rotated. '
                'Retry enable.',
            ) from exc
        if rotation is not None and rotation.status != ManagedLlmKeyStatus.ROTATED:
            # BYOK or non-managed LLM config: there is no managed key to mint.
            logger.warning(
                'org:service_accounts:rotation_skipped',
                extra={
                    'org_id': str(org_id),
                    'service_account_id': str(sa_id),
                    'status': rotation.status,
                },
            )
    _log('enable', user_id, org_id, sa_id)
    return await _get_or_404(org_id, sa_id)


@service_accounts_router.delete(
    '/{org_id}/service-accounts/{sa_id}', status_code=status.HTTP_204_NO_CONTENT
)
async def delete_service_account(
    org_id: UUID,
    sa_id: UUID,
    request: Request,
    user_id: str = Depends(require_permission(Permission.MANAGE_SERVICE_ACCOUNTS)),
) -> Response:
    """Delete a service account that owns no automation and no sandbox.

    409 while it owns an automation or a sandbox. 503 if the Automation
    Service cannot be reached; retry.
    """
    await _get_or_404(org_id, sa_id)
    if await _owns_automation(org_id, sa_id, request):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail='Service account owns automations. Delete or transfer them first.',
        )
    try:
        deleted = await AdminUserLifecycleService().delete_service_account(str(sa_id))
    except UserCleanupConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except LifecycleCleanupError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail='Service account not found'
        ) from exc
    if deleted is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail='Service account not found'
        )
    _log('delete', user_id, org_id, sa_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Secrets ---
#
# A run with the service account's org-bound key loads its personal secrets
# plus the org-shared ones (``SaasSecretsStore.load``). Only the personal
# secrets are managed here. No route returns a value.


def _validate_secret_name(name: str) -> None:
    try:
        validate_env_var_name(name, field_name='secret name')
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if name in RESERVED_SECRET_NAMES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f'{name} is a reserved secret name'
        )


@service_accounts_router.get(
    '/{org_id}/service-accounts/{sa_id}/secrets', response_model=CustomSecretPage
)
async def list_service_account_secrets(
    org_id: UUID,
    sa_id: UUID,
    user_id: str = Depends(require_permission(Permission.MANAGE_SERVICE_ACCOUNTS)),
) -> CustomSecretPage:
    """List the personal secrets of a service account: names and descriptions only."""
    await _get_or_404(org_id, sa_id)
    store = await SaasSecretsStore.get_instance(str(sa_id), effective_org_id=org_id)
    items = [
        CustomSecretWithoutValue.model_construct(
            name=name, description=description, scope=CustomSecretScope.PERSONAL
        )
        for name, description in sorted(await store.list_personal())
    ]
    return CustomSecretPage(items=items, next_page_id=None)


@service_accounts_router.put(
    '/{org_id}/service-accounts/{sa_id}/secrets/{secret_name}',
    response_model=EditResponse,
)
async def put_service_account_secret(
    org_id: UUID,
    sa_id: UUID,
    secret_name: str,
    body: ServiceAccountSecretUpsert,
    user_id: str = Depends(require_permission(Permission.MANAGE_SERVICE_ACCOUNTS)),
) -> EditResponse:
    """Create or replace ONE personal secret. The value is write-only.

    Omitting ``description`` keeps the existing one.
    """
    _validate_secret_name(secret_name)
    await _get_or_404(org_id, sa_id)
    store = await SaasSecretsStore.get_instance(str(sa_id), effective_org_id=org_id)
    # Same lock key as ``/api/v1/secrets`` writes for this user and org.
    async with _secrets_write_lock(str(sa_id), store):
        await store.upsert_personal(
            secret_name, body.value.get_secret_value(), body.description
        )
    _log('secret_put', user_id, org_id, sa_id)
    return EditResponse(message='Secret saved successfully')


@service_accounts_router.delete(
    '/{org_id}/service-accounts/{sa_id}/secrets/{secret_name}',
    response_model=EditResponse,
)
async def delete_service_account_secret(
    org_id: UUID,
    sa_id: UUID,
    secret_name: str,
    user_id: str = Depends(require_permission(Permission.MANAGE_SERVICE_ACCOUNTS)),
) -> EditResponse:
    """Delete ONE personal secret. 404 if it does not exist."""
    await _get_or_404(org_id, sa_id)
    store = await SaasSecretsStore.get_instance(str(sa_id), effective_org_id=org_id)
    async with _secrets_write_lock(str(sa_id), store):
        deleted = await store.delete_personal(secret_name)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Secret not found')
    _log('secret_delete', user_id, org_id, sa_id)
    return EditResponse(message='Secret deleted successfully')
