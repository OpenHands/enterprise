"""Permissioned, versioned organization budget operations."""

from collections.abc import Awaitable, Callable
from typing import TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from server.auth.authorization import Permission, require_permission
from server.routes.budget_control_models import (
    BudgetControlState,
    BudgetHandoffRequest,
    BudgetOperationResponse,
    BudgetPreviewResponse,
)
from server.services.budget_adoption_plan import (
    BudgetAdoptionRequest,
    BudgetAdoptionUnsupported,
)
from server.services.budget_adoption_service import BudgetOperationNotFound
from server.services.budget_notification_service import (
    BudgetNotificationService,
    BudgetNotificationState,
    BudgetNotificationUpdate,
)
from server.services.managed_budget_service import (
    ManagedBudgetService,
    ManagedBudgetUpdate,
)
from server.services.org_budget_service import (
    OrgBudgetService,
    OrgBudgetServiceInjector,
)
from storage.budget_control import (
    BudgetControlConflict,
    BudgetWriteDenied,
    budget_engine,
)

budget_control_router = APIRouter(prefix='/{org_id}/budgets')
_service_injector = OrgBudgetServiceInjector()
_admin = Depends(require_permission(Permission.EDIT_ORG_SETTINGS))
T = TypeVar('T')


async def get_budget_controller(
    service: OrgBudgetService = Depends(_service_injector.depends),
) -> ManagedBudgetService:
    return service._controller()


_controller = Depends(get_budget_controller)


async def get_budget_notifications(
    service: OrgBudgetService = Depends(_service_injector.depends),
) -> BudgetNotificationService:
    return BudgetNotificationService(budget_engine(service.db_session))


_notifications = Depends(get_budget_notifications)


async def _call(action: Callable[[], Awaitable[T]]) -> T:
    try:
        return await action()
    except HTTPException:
        raise
    except BudgetOperationNotFound as error:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, 'Budget operation not found'
        ) from error
    except (BudgetControlConflict, BudgetWriteDenied) as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {'code': 'budget_control_conflict', 'message': str(error)},
        ) from error
    except BudgetAdoptionUnsupported as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {'code': 'budget_control_unsupported', 'message': str(error)},
        ) from error
    except Exception as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                'code': 'budget_control_unavailable',
                'message': 'Budget state could not be verified. Refresh status before retrying.',
            },
        ) from error


def _operation(result: dict, response: Response) -> BudgetOperationResponse:
    if result['status'] == 'pending':
        response.status_code = status.HTTP_202_ACCEPTED
    response.headers['Cache-Control'] = 'no-store'
    return BudgetOperationResponse.from_result(result)


@budget_control_router.get('/notifications', response_model=BudgetNotificationState)
async def get_budget_notification_preferences(
    org_id: UUID,
    response: Response,
    user_id: str = _admin,
    service: BudgetNotificationService = _notifications,
) -> BudgetNotificationState:
    result = await _call(lambda: service.get(org_id, user_id))
    response.headers['Cache-Control'] = 'no-store'
    return result


@budget_control_router.put('/notifications', response_model=BudgetNotificationState)
async def update_budget_notification_preferences(
    org_id: UUID,
    request: BudgetNotificationUpdate,
    response: Response,
    user_id: str = _admin,
    service: BudgetNotificationService = _notifications,
) -> BudgetNotificationState:
    result = await _call(lambda: service.update(org_id, user_id, request))
    response.headers['Cache-Control'] = 'no-store'
    return result


@budget_control_router.get('/adoption/preview', response_model=BudgetPreviewResponse)
async def preview_budget_adoption(
    org_id: UUID,
    response: Response,
    user_id: str = _admin,
    controller: ManagedBudgetService = _controller,
) -> BudgetPreviewResponse:
    result = await _call(lambda: controller.preview(org_id))
    response.headers['Cache-Control'] = 'no-store'
    return BudgetPreviewResponse.from_preview(result)


@budget_control_router.post('/adoption', response_model=BudgetOperationResponse)
async def confirm_budget_adoption(
    org_id: UUID,
    request: BudgetAdoptionRequest,
    response: Response,
    user_id: str = _admin,
    controller: ManagedBudgetService = _controller,
) -> BudgetOperationResponse:
    return _operation(
        await _call(lambda: controller.confirm(org_id, user_id, request)), response
    )


@budget_control_router.patch('/policy', response_model=BudgetOperationResponse)
async def update_managed_budget_policy(
    org_id: UUID,
    request: ManagedBudgetUpdate,
    response: Response,
    user_id: str = _admin,
    controller: ManagedBudgetService = _controller,
) -> BudgetOperationResponse:
    return _operation(
        await _call(lambda: controller.update(org_id, user_id, request)), response
    )


@budget_control_router.get(
    '/operations/{operation_id}', response_model=BudgetOperationResponse
)
async def get_budget_operation(
    org_id: UUID,
    operation_id: UUID,
    response: Response,
    user_id: str = _admin,
    controller: ManagedBudgetService = _controller,
) -> BudgetOperationResponse:
    return _operation(
        await _call(lambda: controller.get_operation(org_id, operation_id)), response
    )


@budget_control_router.post(
    '/operations/{operation_id}/retry', response_model=BudgetOperationResponse
)
async def retry_budget_operation(
    org_id: UUID,
    operation_id: UUID,
    response: Response,
    user_id: str = _admin,
    controller: ManagedBudgetService = _controller,
) -> BudgetOperationResponse:
    result = await _call(lambda: controller.retry(org_id, operation_id))
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Budget operation not found')
    return _operation(result, response)


@budget_control_router.post('/handoff', response_model=BudgetControlState)
async def hand_off_budget_control(
    org_id: UUID,
    request: BudgetHandoffRequest,
    response: Response,
    user_id: str = _admin,
    controller: ManagedBudgetService = _controller,
) -> BudgetControlState:
    result = await _call(
        lambda: controller.hand_off(
            org_id, user_id, expected_generation=request.expected_generation
        )
    )
    response.headers['Cache-Control'] = 'no-store'
    return BudgetControlState.model_validate(result)
