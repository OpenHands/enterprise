"""API routes for managing the LLM model catalog (admin only).

The mount path was renamed from ``/api/admin/verified-models`` to
``/api/admin/model-catalog``. The old path is preserved as a deprecated
alias for one release so existing operator scripts and the runbook keep
working; see OpenHands/enterprise#350 for the full rollout plan (this PR
covers only the endpoint path — the DB table, ORM classes, and Python
package rename follow in separate PRs).
"""

import logging
from typing import Annotated, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from openhands.app_server.config_api.default_llm_model_service import (
    _VERIFIED_MODEL_SET,
    DefaultLLMModelService,
)
from openhands.app_server.config_api.llm_model_service import (
    LLMModelService,
    LLMModelServiceInjector,
)
from openhands.app_server.services.db_session import get_db_session
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.utils.llm import ModelsResponse, get_supported_llm_models
from server.email_validation import get_admin_user_id
from server.verified_models.verified_model_models import (
    VerifiedModel,
    VerifiedModelCreate,
    VerifiedModelPage,
    VerifiedModelUpdate,
)
from server.verified_models.verified_model_service import (
    LiteLLMSyncError,
    VerifiedModelService,
    verified_model_store_dependency,
)

_logger = logging.getLogger(__name__)


def _litellm_sync_error_response(exc: LiteLLMSyncError) -> HTTPException:
    """Map a LiteLLM propagation failure to a surfaced 502.

    The verified-model DB mutation is already committed before propagation
    runs, so this does not roll it back. Surfacing the failure (instead of
    silently acknowledging the mutation) lets the operator retry by re-saving
    the model or reconcile LiteLLM out-of-band, so a billing/access change
    cannot stay silently divergent.
    """
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=str(exc),
    )


def _register_routes(router: APIRouter) -> None:
    """Register the model-catalog CRUD handlers on ``router``.

    Shared between ``api_router`` (the new ``/api/admin/model-catalog``
    mount) and ``legacy_api_router`` (the deprecated alias at
    ``/api/admin/verified-models``), so the deprecation window keeps both
    paths serving the same handlers. Drop ``legacy_api_router`` in Phase 3
    of OpenHands/enterprise#350 to remove the old path.
    """

    @router.get('')
    async def list_models(
        provider: str | None = None,
        page_id: Annotated[
            str | None,
            Query(title='Optional next_page_id from the previously returned page'),
        ] = None,
        limit: Annotated[
            int, Query(title='The max number of results in the page', gt=0, le=100)
        ] = 100,
        user_id: str = Depends(get_admin_user_id),
        model_service: VerifiedModelService = Depends(verified_model_store_dependency),
    ) -> VerifiedModelPage:
        """List all models in the catalog, optionally filtered by provider."""
        return await model_service.search_verified_models(
            provider=provider,
            enabled_only=False,  # Admin sees all rows including disabled
            page_id=page_id,
            limit=limit,
        )

    @router.post('', status_code=201)
    async def create_model(
        data: VerifiedModelCreate,
        user_id: str = Depends(get_admin_user_id),
        model_service: VerifiedModelService = Depends(verified_model_store_dependency),
    ) -> VerifiedModel:
        """Create a new model in the catalog."""
        try:
            return await model_service.create_verified_model(
                model_name=data.model_name,
                provider=data.provider,
                is_enabled=data.is_enabled,
                is_verified=data.is_verified,
                is_free=data.is_free,
                is_default=data.is_default,
            )
        except ValueError as ex:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(ex),
            ) from ex
        except LiteLLMSyncError as ex:
            raise _litellm_sync_error_response(ex) from ex

    @router.put('/{provider}/{model_name:path}')
    async def update_model(
        provider: str,
        model_name: str,
        data: VerifiedModelUpdate,
        user_id: str = Depends(get_admin_user_id),
        model_service: VerifiedModelService = Depends(verified_model_store_dependency),
    ) -> VerifiedModel:
        """Update a model in the catalog by provider and model name."""
        try:
            model = await model_service.update_verified_model(
                model_name=model_name,
                provider=provider,
                is_enabled=data.is_enabled,
                is_verified=data.is_verified,
                is_free=data.is_free,
                is_default=data.is_default,
            )
        except LiteLLMSyncError as ex:
            raise _litellm_sync_error_response(ex) from ex
        if not model:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f'Model {provider}/{model_name} not found',
            )
        return model

    @router.delete('/{provider}/{model_name:path}')
    async def delete_model(
        provider: str,
        model_name: str,
        user_id: str = Depends(get_admin_user_id),
        model_service: VerifiedModelService = Depends(verified_model_store_dependency),
    ) -> bool:
        """Delete a model from the catalog by provider and model name."""
        try:
            await model_service.delete_verified_model(
                model_name=model_name, provider=provider
            )
            return True
        except ValueError as ex:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(ex),
            ) from ex
        except LiteLLMSyncError as ex:
            raise _litellm_sync_error_response(ex) from ex


api_router = APIRouter(prefix='/api/admin/model-catalog', tags=['Model Catalog'])
legacy_api_router = APIRouter(
    prefix='/api/admin/verified-models',
    tags=['Model Catalog (deprecated)'],
    deprecated=True,
)
_register_routes(api_router)
_register_routes(legacy_api_router)


class SaaSLLMModelService(DefaultLLMModelService):
    """SaaS implementation that reads the model catalog from the database.

    Inherits filtering, pagination, and provider logic from
    ``DefaultLLMModelService`` — only the catalog list is different.
    """

    def __init__(self, db_session) -> None:
        super().__init__()
        self._db_session = db_session
        self._verified_model_ids: set[str] | None = None

    def _is_model_verified(
        self, model_name: str, name: str, models_response: ModelsResponse
    ) -> bool:
        if self._verified_model_ids is None:
            return super()._is_model_verified(model_name, name, models_response)
        return model_name in self._verified_model_ids

    async def _get_models_response(
        self,
        verified_models: list[str] | None = None,
    ) -> ModelsResponse:
        if self._cached_response is not None:
            return self._cached_response

        model_service = VerifiedModelService(self._db_session)
        page = await model_service.search_verified_models(enabled_only=False)
        if page.next_page_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail='Too many models defined in database',
            )
        enabled_items = [m for m in page.items if m.is_enabled]
        openhands_models = [
            f'{m.provider}/{m.model_name}'
            for m in enabled_items
            if m.provider == 'openhands'
        ]
        extra_models = [
            f'{m.provider}/{m.model_name}'
            for m in enabled_items
            if m.provider != 'openhands'
        ]
        verified_model_ids = set(_VERIFIED_MODEL_SET)
        for model in page.items:
            model_id = f'{model.provider}/{model.model_name}'
            if model.is_enabled and model.is_verified:
                verified_model_ids.add(model_id)
            else:
                verified_model_ids.discard(model_id)
        self._verified_model_ids = verified_model_ids
        verified_openhands_models = [
            f'{m.provider}/{m.model_name}'
            for m in enabled_items
            if m.provider == 'openhands' and m.is_verified
        ]
        free_models = [
            f'{m.provider}/{m.model_name}' for m in enabled_items if m.is_free
        ]
        # At most one row per provider carries is_default (DB-enforced). The
        # openhands provider's default drives the app-wide default model shown
        # on onboarding and when creating a new OpenHands model.
        default_model = next(
            (
                f'{m.provider}/{m.model_name}'
                for m in enabled_items
                if m.is_default and m.provider == 'openhands'
            ),
            None,
        )
        self._cached_response = get_supported_llm_models(
            openhands_models,
            extra_models=extra_models or None,
            free_models=free_models,
            default_model=default_model,
            verified_openhands_models=verified_openhands_models,
        )
        return self._cached_response


class SaaSLLMModelServiceInjector(LLMModelServiceInjector):
    """Injector that provides the SaaS LLM model service.

    Activate via the environment variable::

        OH_LLM_MODEL_KIND=server.verified_models.verified_model_router.SaaSLLMModelServiceInjector
    """

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[LLMModelService, None]:
        async with get_db_session(state, request) as db_session:
            yield SaaSLLMModelService(db_session)
