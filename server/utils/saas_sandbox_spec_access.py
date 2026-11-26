"""Database-backed sandbox spec access: private specs are granted to orgs."""

from dataclasses import dataclass
from typing import AsyncGenerator, Iterable

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.sandbox.sandbox_store import get_credential_org_id
from openhands.app_server.sandbox.scoped_sandbox_spec_service import (
    SandboxSpecAccess,
    SandboxSpecAccessInjector,
)
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.specifiy_user_context import ADMIN
from openhands.app_server.user.user_context import UserContext
from storage import sandbox_spec_access_store


@dataclass
class SaasSandboxSpecAccess(SandboxSpecAccess):
    """Access for the org the request launches sandboxes in.

    That is the org ``get_credential_org_id`` records on the sandbox, so
    interactive, API-key and integration launches are all scoped the same way.
    """

    db_session: AsyncSession
    user_context: UserContext

    async def filter_accessible(self, spec_ids: Iterable[str]) -> set[str]:
        spec_ids = set(spec_ids)
        if self.user_context == ADMIN:
            return spec_ids
        org_id = await get_credential_org_id(self.user_context)
        return await sandbox_spec_access_store.filter_accessible(
            self.db_session, spec_ids, org_id
        )

    async def get_default_spec_id(self) -> str | None:
        if self.user_context == ADMIN:
            return None
        org_id = await get_credential_org_id(self.user_context)
        if org_id is None:
            return None
        return await sandbox_spec_access_store.get_org_default_spec_name(
            self.db_session, org_id
        )


class SaasSandboxSpecAccessInjector(SandboxSpecAccessInjector):
    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[SandboxSpecAccess, None]:
        from openhands.app_server.config import get_db_session, get_user_context

        async with (
            get_user_context(state, request) as user_context,
            get_db_session(state, request) as db_session,
        ):
            yield SaasSandboxSpecAccess(
                db_session=db_session, user_context=user_context
            )
