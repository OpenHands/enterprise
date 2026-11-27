from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncGenerator

from fastapi import Request

from openhands.app_server.errors import AuthError
from openhands.app_server.services.injector import Injector, InjectorState
from openhands.app_server.user.user_context import UserContext
from server.routes.org_models import (
    OrgAppSettingsResponse,
    OrgAppSettingsUpdate,
    OrgNotFoundError,
)
from storage.org import Org
from storage.org_app_settings_store import OrgAppSettingsStore


@dataclass
class OrgAppSettingsService:
    """Service for organization app settings with injected dependencies."""

    store: OrgAppSettingsStore
    user_context: UserContext

    async def _resolve_effective_org(self) -> Org:
        user_id = await self.user_context.get_user_id()
        if not user_id:
            raise AuthError('User not authenticated')

        user_auth = getattr(self.user_context, 'user_auth', None)
        effective_org_id = None
        if user_auth is not None and hasattr(user_auth, 'get_effective_org_id'):
            effective_org_id = await user_auth.get_effective_org_id()

        if effective_org_id is not None:
            org = await self.store.get_org_by_id(effective_org_id)
        else:
            org = await self.store.get_current_org_by_user_id(user_id)
        if not org:
            raise OrgNotFoundError('current')
        return org

    async def get_org_app_settings(self) -> OrgAppSettingsResponse:
        return OrgAppSettingsResponse.from_org(await self._resolve_effective_org())

    async def update_org_app_settings(
        self, update_data: OrgAppSettingsUpdate, *, user_id: str
    ) -> OrgAppSettingsResponse:
        org = await self._resolve_effective_org()
        if not update_data.model_fields_set:
            return OrgAppSettingsResponse.from_org(org)
        updated = await self.store.update_org_app_settings(
            org_id=org.id, update_data=update_data, user_id=user_id
        )
        if updated is None:
            raise OrgNotFoundError(str(org.id))
        return OrgAppSettingsResponse.from_org(updated)


class OrgAppSettingsServiceInjector(Injector[OrgAppSettingsService]):
    """Injector that composes store and user_context for OrgAppSettingsService."""

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[OrgAppSettingsService, None]:
        # Local imports to avoid circular dependencies
        from openhands.app_server.config import get_db_session, get_user_context

        async with (
            get_user_context(state, request) as user_context,
            get_db_session(state, request) as db_session,
        ):
            store = OrgAppSettingsStore(db_session=db_session)
            yield OrgAppSettingsService(store=store, user_context=user_context)
