"""Sandbox spec service limited to the specs the caller's organization may use."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable

from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.sandbox_spec_models import (
    SandboxSpecInfo,
    SandboxSpecInfoPage,
)
from openhands.app_server.sandbox.sandbox_spec_service import SandboxSpecService
from openhands.app_server.services.injector import Injector
from openhands.sdk.utils.models import DiscriminatedUnionMixin

_logger = logging.getLogger(__name__)

_PAGE_SIZE = 100


class SandboxSpecAccess(ABC):
    """Which sandbox specs the current request may use for new sandboxes."""

    @abstractmethod
    async def filter_accessible(self, spec_ids: Iterable[str]) -> set[str]:
        """Return the given spec IDs that are accessible."""

    async def get_default_spec_id(self) -> str | None:
        """ID of the default spec configured for the caller's organization."""
        return None


class SandboxSpecAccessInjector(
    DiscriminatedUnionMixin, Injector[SandboxSpecAccess], ABC
):
    pass


@dataclass
class ScopedSandboxSpecService(SandboxSpecService):
    """Hides specs the caller may not use.

    ``resolve_existing_sandbox_spec`` stays unscoped on purpose: a sandbox that is
    already running must keep working after access to its spec is revoked. Only
    new sandboxes, and reuse of grouped ones, are limited.
    """

    delegate: SandboxSpecService
    access: SandboxSpecAccess

    async def _is_accessible(self, spec: SandboxSpecInfo) -> bool:
        return spec.id in await self.access.filter_accessible([spec.id])

    async def _list_accessible(self) -> list[SandboxSpecInfo]:
        specs: list[SandboxSpecInfo] = []
        page_id: str | None = None
        while True:
            page = await self.delegate.search_sandbox_specs(
                page_id=page_id, limit=_PAGE_SIZE
            )
            specs.extend(page.items)
            page_id = page.next_page_id
            if page_id is None:
                break
        allowed = await self.access.filter_accessible(spec.id for spec in specs)
        return [spec for spec in specs if spec.id in allowed]

    async def search_sandbox_specs(
        self, page_id: str | None = None, limit: int = 100
    ) -> SandboxSpecInfoPage:
        specs = await self._list_accessible()
        start = int(page_id) if page_id else 0
        end = start + limit
        return SandboxSpecInfoPage(
            items=specs[start:end],
            next_page_id=str(end) if end < len(specs) else None,
        )

    async def get_sandbox_spec(self, sandbox_spec_id: str) -> SandboxSpecInfo | None:
        spec = await self.delegate.get_sandbox_spec(sandbox_spec_id)
        if spec is None or not await self._is_accessible(spec):
            return None
        return spec

    async def get_default_sandbox_spec(self) -> SandboxSpecInfo:
        org_default_id = await self.access.get_default_spec_id()
        if org_default_id is not None:
            spec = await self.get_sandbox_spec(org_default_id)
            if spec is not None:
                return spec
            _logger.warning(
                'Organization default sandbox spec %r is unavailable; '
                'using the system default.',
                org_default_id,
            )
        # The delegate's default may be private; only use it when accessible.
        spec = await self.delegate.get_default_sandbox_spec()
        if await self._is_accessible(spec):
            return spec
        accessible = await self._list_accessible()
        if not accessible:
            raise SandboxError('No sandbox specs are available to your organization.')
        return accessible[0]

    async def is_sandbox_spec_accessible(self, sandbox_spec_id: str) -> bool:
        spec = await self.delegate.get_sandbox_spec(sandbox_spec_id)
        return spec is None or await self._is_accessible(spec)

    async def resolve_existing_sandbox_spec(
        self, sandbox_spec_id: str, sandbox_id: str
    ) -> SandboxSpecInfo:
        return await self.delegate.resolve_existing_sandbox_spec(
            sandbox_spec_id, sandbox_id
        )
