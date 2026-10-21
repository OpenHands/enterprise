from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime
from typing import Any, Callable

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from integrations.azure_devops.azure_devops_service import SaaSAzureDevOpsService
from integrations.models import Message, SourceType
from integrations.utils import HOST_URL, IS_LOCAL_DEPLOYMENT
from openhands.app_server.integrations.azure_devops.service.base import (
    AzureDevOpsApiError,
)
from openhands.app_server.integrations.azure_devops.service.webhooks import (
    AZURE_DEVOPS_PR_COMMENT_EVENT,
    AZURE_DEVOPS_WORK_ITEM_COMMENT_EVENT,
)
from openhands.app_server.integrations.service_types import (
    AuthenticationError,
    ProviderType,
)
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import Permission, require_permission
from server.auth.constants import (
    AUTOMATION_EVENT_FORWARDING_ENABLED,
    AZURE_DEVOPS_WEBHOOK_SECRET,
)
from storage.azure_devops_webhook import AzureDevOpsWebhook
from storage.azure_devops_webhook_store import AzureDevOpsWebhookStore
from storage.redis import get_redis_client_async

azure_devops_integration_router = APIRouter(prefix='/integration')

_azure_devops_manager = None
_automation_event_service = None
azure_devops_webhook_store = AzureDevOpsWebhookStore()


def azure_devops_webhook_url() -> str:
    return f'{HOST_URL}/integration/azure-devops/events'


class AzureDevOpsWebhookStatus(BaseModel):
    organization: str
    webhook_installed: bool
    pr_webhook_installed: bool
    work_item_webhook_installed: bool
    pr_subscription_id: str | None
    work_item_subscription_id: str | None
    webhook_url: str
    webhook_secret_set: bool
    # False when the user's Azure DevOps permissions hide the org's Service
    # Hooks, so the installed flags above can't be trusted.
    can_view_webhooks: bool = True
    can_manage_webhooks: bool = True
    # From OpenHands' own record, so members who can't view the hooks in Azure
    # still see whether and when they were set up.
    install_recorded: bool = False
    installed_at: datetime | None = None
    last_verified_at: datetime | None = None


class AzureDevOpsWebhookInstallationResult(BaseModel):
    organization: str
    success: bool
    error: str | None
    pr_subscription_id: str | None
    work_item_subscription_id: str | None
    webhook_url: str


class AzureDevOpsOrganizations(BaseModel):
    organizations: list[str]


def get_azure_devops_manager():
    global _azure_devops_manager
    if _azure_devops_manager is None:
        from integrations.azure_devops.azure_devops_manager import AzureDevOpsManager
        from server.auth.token_manager import TokenManager

        _azure_devops_manager = AzureDevOpsManager(TokenManager())
    return _azure_devops_manager


def get_automation_event_service():
    global _automation_event_service
    if _automation_event_service is None:
        from server.auth.token_manager import TokenManager
        from server.services.automation_event_service import AutomationEventService

        _automation_event_service = AutomationEventService(TokenManager())
    return _automation_event_service


def _basic_auth_secret(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, encoded = authorization.partition(' ')
    if scheme.lower() != 'basic' or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded).decode()
    except Exception:
        return None
    _, _, password = decoded.partition(':')
    return password or decoded


def _subscription_id(subscription: dict[str, Any]) -> str | None:
    subscription_id = subscription.get('id')
    return str(subscription_id) if subscription_id else None


def _subscription_consumer_url(subscription: dict[str, Any]) -> str:
    return str((subscription.get('consumerInputs') or {}).get('url') or '')


def _subscription_event_type(subscription: dict[str, Any]) -> str:
    return str(subscription.get('eventType') or '')


def _subscription_is_project_scoped(subscription: dict[str, Any]) -> bool:
    # A projectId in publisherInputs means the hook is scoped to one project,
    # not the org-wide resolver install.
    return bool((subscription.get('publisherInputs') or {}).get('projectId'))


def _subscription_is_enabled(subscription: dict[str, Any] | None) -> bool:
    # A Service Hook can exist but be disabled/suspended and not deliver events.
    return subscription is not None and subscription.get('status') == 'enabled'


def _find_subscription(
    subscriptions: list[dict[str, Any]],
    matches: Callable[[dict[str, Any]], bool],
) -> dict[str, Any] | None:
    # Prefer an enabled hook, e.g. when a stale copy survived a reinstall.
    matching = [subscription for subscription in subscriptions if matches(subscription)]
    return next(
        (
            subscription
            for subscription in matching
            if _subscription_is_enabled(subscription)
        ),
        matching[0] if matching else None,
    )


def _azure_devops_api_http_exception(
    action: str, error: AzureDevOpsApiError
) -> HTTPException:
    reason = error.azure_message or 'Azure DevOps did not return an error message.'
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=(
            f'Azure DevOps rejected the request to {action} '
            f'(HTTP {error.status_code}): {reason}'
        ),
    )


def _matches_pr_comment_subscription(
    subscription: dict[str, Any],
    *,
    webhook_url: str,
) -> bool:
    # Org-wide hook = event + consumer url + no projectId (project-scoped hooks
    # at the same url must not be mistaken for the org-wide install).
    return (
        _subscription_event_type(subscription) == AZURE_DEVOPS_PR_COMMENT_EVENT
        and _subscription_consumer_url(subscription) == webhook_url
        and not _subscription_is_project_scoped(subscription)
    )


def _matches_work_item_comment_subscription(
    subscription: dict[str, Any],
    *,
    webhook_url: str,
) -> bool:
    return (
        _subscription_event_type(subscription) == AZURE_DEVOPS_WORK_ITEM_COMMENT_EVENT
        and _subscription_consumer_url(subscription) == webhook_url
        and not _subscription_is_project_scoped(subscription)
    )


# Webhook secret for local dev only; real installs set AZURE_DEVOPS_WEBHOOK_SECRET.
_LOCAL_DEPLOYMENT_WEBHOOK_SECRET = 'localdeploymentwebhooktesttoken'


def _get_webhook_secret() -> str | None:
    return (
        _LOCAL_DEPLOYMENT_WEBHOOK_SECRET
        if IS_LOCAL_DEPLOYMENT
        else AZURE_DEVOPS_WEBHOOK_SECRET
    )


def _ensure_azure_devops_webhook_secret() -> str:
    secret = _get_webhook_secret()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Azure DevOps webhook secret is not configured.',
        )
    return secret


async def _accessible_organizations(service: SaaSAzureDevOpsService) -> list[str]:
    try:
        return await service.get_accessible_organizations()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception('Unable to verify Azure DevOps organization access')
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Unable to verify Azure DevOps organization access.',
        ) from exc


async def _service_for_organization(
    user_id: str, organization: str | None
) -> SaaSAzureDevOpsService:
    service = SaaSAzureDevOpsService(external_auth_id=user_id)
    requested = (organization or '').strip()
    if not requested:
        raise HTTPException(
            status_code=400, detail='Select an Azure DevOps organization.'
        )
    accessible = await _accessible_organizations(service)
    selected = next(
        (name for name in accessible if name.casefold() == requested.casefold()), None
    )
    if selected is None:
        raise HTTPException(
            status_code=403, detail='Azure DevOps organization access denied.'
        )
    # Use the canonical name supplied by Azure, never an arbitrary request URL.
    service.organization = selected
    return service


async def _service_hook_permissions(
    service: SaaSAzureDevOpsService,
) -> tuple[bool, bool, bool]:
    """Return (can_view, can_manage, confirmed) for the org's Service Hooks."""
    try:
        can_view, can_edit = await service.get_service_hook_permissions()
    except Exception as e:
        # Unknown permissions keep the listing-based status and actions.
        logger.warning(f'Could not evaluate Azure DevOps Service Hook permissions: {e}')
        return True, True, False
    # Install and uninstall list the hooks first, so managing needs both.
    return can_view, can_view and can_edit, True


def _is_access_denied(error: Exception) -> bool:
    # Azure answers 401 or 403 when the user can't read the org's Service Hooks.
    return isinstance(error, AuthenticationError) or (
        isinstance(error, AzureDevOpsApiError) and error.status_code == 403
    )


async def _sync_install_record(
    organization: str,
    *,
    installed: bool | None,
    pr_subscription_id: str | None,
    work_item_subscription_id: str | None,
) -> AzureDevOpsWebhook | None:
    """Bring the install record in line with what the user saw in Azure.

    ``installed`` is None when the user couldn't see the hooks; the record is
    then only read. Failures never break the status response.
    """
    try:
        if installed is None:
            return await azure_devops_webhook_store.get_webhook(organization)
        if installed:
            return await azure_devops_webhook_store.record_verified(
                organization,
                pr_subscription_id=pr_subscription_id,
                work_item_subscription_id=work_item_subscription_id,
            )
        await azure_devops_webhook_store.delete_webhook(organization)
        return None
    except Exception:
        logger.exception('Could not sync the Azure DevOps webhook install record')
        return None


@azure_devops_integration_router.get('/azure-devops/organizations')
async def get_azure_devops_organizations(
    user_id: str = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
) -> AzureDevOpsOrganizations:
    service = SaaSAzureDevOpsService(external_auth_id=user_id)
    return AzureDevOpsOrganizations(
        organizations=await _accessible_organizations(service)
    )


async def verify_azure_devops_signature(
    header_webhook_secret: str | None,
    authorization: str | None,
) -> None:
    expected_secret = _get_webhook_secret()
    provided_secret = header_webhook_secret or _basic_auth_secret(authorization)
    if not expected_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Azure DevOps webhook secret is not configured.',
        )
    if not provided_secret or not secrets.compare_digest(
        provided_secret, expected_secret
    ):
        raise HTTPException(status_code=403, detail="Request signatures didn't match!")


@azure_devops_integration_router.get('/azure-devops/resources')
async def get_azure_devops_resources(
    user_id: str = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    organization: str | None = None,
) -> AzureDevOpsWebhookStatus:
    """Report org-wide resolver hook installation status."""
    try:
        service = await _service_for_organization(user_id, organization)
        webhook_url = azure_devops_webhook_url()

        can_view, can_manage, confirmed = await _service_hook_permissions(service)
        subscriptions: list[dict[str, Any]] = []
        if can_view:
            try:
                subscriptions = await service.list_service_hook_subscriptions()
            except (AuthenticationError, AzureDevOpsApiError) as e:
                if not _is_access_denied(e):
                    raise
                logger.info(f'Azure DevOps Service Hooks not readable by user: {e}')
                can_view = can_manage = False
        pr_subscription = _find_subscription(
            subscriptions,
            lambda subscription: _matches_pr_comment_subscription(
                subscription, webhook_url=webhook_url
            ),
        )
        work_item_subscription = _find_subscription(
            subscriptions,
            lambda subscription: _matches_work_item_comment_subscription(
                subscription, webhook_url=webhook_url
            ),
        )

        # Only report installed when the hooks are enabled (a disabled/suspended
        # hook exists but won't deliver resolver events).
        pr_enabled = _subscription_is_enabled(pr_subscription)
        work_item_enabled = _subscription_is_enabled(work_item_subscription)
        webhook_installed = pr_enabled and work_item_enabled
        pr_subscription_id = (
            _subscription_id(pr_subscription) if pr_subscription else None
        )
        work_item_subscription_id = (
            _subscription_id(work_item_subscription) if work_item_subscription else None
        )

        # Only clear the record when Azure confirmed the user can view the hooks
        # and none exist; disabled or probation hooks may recover, and an
        # unconfirmed empty listing may just be hiding them.
        observed: bool | None = None
        if can_view and webhook_installed:
            observed = True
        elif can_view and confirmed and not (pr_subscription or work_item_subscription):
            observed = False
        record = await _sync_install_record(
            service.organization,
            installed=observed,
            pr_subscription_id=pr_subscription_id,
            work_item_subscription_id=work_item_subscription_id,
        )

        return AzureDevOpsWebhookStatus(
            organization=service.organization,
            webhook_installed=webhook_installed,
            pr_webhook_installed=pr_enabled,
            work_item_webhook_installed=work_item_enabled,
            pr_subscription_id=pr_subscription_id,
            work_item_subscription_id=work_item_subscription_id,
            webhook_url=webhook_url,
            webhook_secret_set=bool(IS_LOCAL_DEPLOYMENT or AZURE_DEVOPS_WEBHOOK_SECRET),
            can_view_webhooks=can_view,
            can_manage_webhooks=can_manage,
            install_recorded=record is not None,
            installed_at=record.installed_at if record else None,
            last_verified_at=record.last_verified_at if record else None,
        )

    except HTTPException:
        raise
    except AzureDevOpsApiError as e:
        logger.warning(f'Error retrieving Azure DevOps resources: {e}')
        raise _azure_devops_api_http_exception('read Service Hooks', e) from e
    except Exception as e:
        logger.exception('Error retrieving Azure DevOps resources', stack_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to retrieve Azure DevOps resources',
        ) from e


async def _delete_subscriptions_best_effort(
    service: SaaSAzureDevOpsService, subscription_ids: list[str], *, reason: str
) -> None:
    for subscription_id in subscription_ids:
        try:
            await service.delete_service_hook_subscription(subscription_id)
        except Exception as e:
            logger.warning(
                f'[Azure DevOps] Could not delete {reason} Service Hook '
                f'{subscription_id}: {e}'
            )


@azure_devops_integration_router.post('/azure-devops/reinstall-webhook')
async def reinstall_azure_devops_webhook(
    user_id: str = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    organization: str | None = None,
) -> AzureDevOpsWebhookInstallationResult:
    """Install or reinstall the org-wide Azure DevOps resolver Service Hooks."""
    service = await _service_for_organization(user_id, organization)
    webhook_secret = _ensure_azure_devops_webhook_secret()
    webhook_url = azure_devops_webhook_url()

    try:
        subscriptions = await service.list_service_hook_subscriptions()
        existing_subscription_ids = [
            subscription_id
            for subscription in subscriptions
            if (subscription_id := _subscription_id(subscription))
            and (
                _matches_pr_comment_subscription(subscription, webhook_url=webhook_url)
                or _matches_work_item_comment_subscription(
                    subscription, webhook_url=webhook_url
                )
            )
        ]

        # Create the new hooks before removing the old ones, so a refused
        # create leaves the existing installation working.
        created_subscription_ids: list[str] = []
        try:
            pr_subscription = await service.create_pr_comment_service_hook(
                webhook_url=webhook_url,
                webhook_secret=webhook_secret,
            )
            pr_subscription_id = _subscription_id(pr_subscription)
            if pr_subscription_id:
                created_subscription_ids.append(pr_subscription_id)
            work_item_subscription = (
                await service.create_work_item_comment_service_hook(
                    webhook_url=webhook_url,
                    webhook_secret=webhook_secret,
                )
            )
            work_item_subscription_id = _subscription_id(work_item_subscription)
        except Exception:
            await _delete_subscriptions_best_effort(
                service, created_subscription_ids, reason='rollback'
            )
            raise

        await _delete_subscriptions_best_effort(
            service,
            [
                subscription_id
                for subscription_id in existing_subscription_ids
                if subscription_id
                not in (pr_subscription_id, work_item_subscription_id)
            ],
            reason='replaced',
        )

        try:
            await azure_devops_webhook_store.record_install(
                service.organization,
                user_id=user_id,
                pr_subscription_id=pr_subscription_id,
                work_item_subscription_id=work_item_subscription_id,
            )
        except Exception:
            logger.exception('Could not record the Azure DevOps webhook install')

        logger.info(
            '[Azure DevOps] Resolver hooks installed',
            extra={
                'user_id': user_id,
                'organization': service.organization,
                'pr_subscription_id': pr_subscription_id,
                'work_item_subscription_id': work_item_subscription_id,
            },
        )

        return AzureDevOpsWebhookInstallationResult(
            organization=service.organization,
            success=True,
            error=None,
            pr_subscription_id=pr_subscription_id,
            work_item_subscription_id=work_item_subscription_id,
            webhook_url=webhook_url,
        )

    except HTTPException:
        raise
    except AzureDevOpsApiError as e:
        logger.warning(f'Error installing Azure DevOps resolver hooks: {e}')
        raise _azure_devops_api_http_exception('install Service Hooks', e) from e
    except Exception as e:
        logger.exception(
            'Error installing Azure DevOps resolver hooks', stack_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to install Azure DevOps resolver hooks',
        ) from e


@azure_devops_integration_router.post('/azure-devops/uninstall-webhook')
async def uninstall_azure_devops_webhook(
    user_id: str = Depends(require_permission(Permission.MANAGE_INTEGRATIONS)),
    organization: str | None = None,
) -> AzureDevOpsWebhookInstallationResult:
    """Delete the org-wide Azure DevOps resolver Service Hooks."""
    service = await _service_for_organization(user_id, organization)
    webhook_url = azure_devops_webhook_url()

    try:
        subscriptions = await service.list_service_hook_subscriptions()
        deleted_pr_subscription_id: str | None = None
        deleted_work_item_subscription_id: str | None = None

        for subscription in subscriptions:
            subscription_id = _subscription_id(subscription)
            if not subscription_id:
                continue
            if _matches_pr_comment_subscription(subscription, webhook_url=webhook_url):
                await service.delete_service_hook_subscription(subscription_id)
                deleted_pr_subscription_id = subscription_id
            elif _matches_work_item_comment_subscription(
                subscription, webhook_url=webhook_url
            ):
                await service.delete_service_hook_subscription(subscription_id)
                deleted_work_item_subscription_id = subscription_id

        try:
            await azure_devops_webhook_store.delete_webhook(service.organization)
        except Exception:
            logger.exception('Could not delete the Azure DevOps webhook install record')

        logger.info(
            '[Azure DevOps] Resolver hooks uninstalled',
            extra={
                'user_id': user_id,
                'organization': service.organization,
                'pr_subscription_id': deleted_pr_subscription_id,
                'work_item_subscription_id': deleted_work_item_subscription_id,
            },
        )

        return AzureDevOpsWebhookInstallationResult(
            organization=service.organization,
            success=True,
            error=None,
            pr_subscription_id=deleted_pr_subscription_id,
            work_item_subscription_id=deleted_work_item_subscription_id,
            webhook_url=webhook_url,
        )

    except HTTPException:
        raise
    except AzureDevOpsApiError as e:
        logger.warning(f'Error uninstalling Azure DevOps resolver hooks: {e}')
        raise _azure_devops_api_http_exception('uninstall Service Hooks', e) from e
    except Exception as e:
        logger.exception(
            'Error uninstalling Azure DevOps resolver hooks', stack_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to uninstall Azure DevOps resolver hooks',
        ) from e


@azure_devops_integration_router.post('/azure-devops/events')
async def azure_devops_events(
    request: Request,
    background_tasks: BackgroundTasks,
    x_openhands_webhook_secret: str | None = Header(None),
    authorization: str | None = Header(None),
):
    try:
        await verify_azure_devops_signature(
            header_webhook_secret=x_openhands_webhook_secret,
            authorization=authorization,
        )

        payload_data = await request.json()
        # Dedup on the stable event id; notificationId changes on ADO retries,
        # so including it would let a retried delivery start a duplicate job.
        dedup_key = payload_data.get('id')
        if dedup_key:
            dedup_key = f'azure_devops_msg:{dedup_key}'
        else:
            dedup_json = json.dumps(payload_data, sort_keys=True)
            dedup_hash = hashlib.sha256(dedup_json.encode()).hexdigest()
            dedup_key = f'azure_devops_msg:{dedup_hash}'

        redis = get_redis_client_async()
        created = await redis.set(dedup_key, 1, nx=True, ex=60)
        if not created:
            logger.info('azure_devops_is_duplicate')
            return JSONResponse(
                status_code=200,
                content={'message': 'Duplicate Azure DevOps event ignored.'},
            )

        if AUTOMATION_EVENT_FORWARDING_ENABLED:
            background_tasks.add_task(
                get_automation_event_service().forward_event,
                provider=ProviderType.AZURE_DEVOPS,
                payload=payload_data,
                installation_id=str(payload_data.get('subscriptionId') or ''),
                event_type=payload_data.get('eventType'),
            )

        message = Message(
            source=SourceType.AZURE_DEVOPS,
            message={
                'payload': payload_data,
                'event_key': payload_data.get('eventType'),
            },
        )
        # Process in the background so we return 200 fast; conversation/sandbox
        # startup can exceed Service Hooks' delivery timeout -> retries. Mirrors
        # the Jira DC / Bitbucket DC routes. Signature is verified above.
        background_tasks.add_task(get_azure_devops_manager().receive_message, message)

        return JSONResponse(
            status_code=200,
            content={'message': 'Azure DevOps events endpoint reached successfully.'},
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception('Error processing Azure DevOps event', stack_info=True)
        return JSONResponse(status_code=400, content={'error': 'Invalid payload.'})
