"""Tests for ScopedSandboxSpecService over an in-memory spec service."""

from dataclasses import dataclass
from typing import Iterable

import pytest

from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.sandbox_spec_models import (
    SandboxSpecInfo,
    SandboxSpecInfoPage,
)
from openhands.app_server.sandbox.sandbox_spec_service import (
    SandboxSpecService,
    resolve_sandbox_spec,
)
from openhands.app_server.sandbox.scoped_sandbox_spec_service import (
    SandboxSpecAccess,
    ScopedSandboxSpecService,
)


def _spec(spec_id: str) -> SandboxSpecInfo:
    return SandboxSpecInfo(
        id=spec_id, command=[], initial_env={}, working_dir='/workspace'
    )


@dataclass
class InMemorySpecService(SandboxSpecService):
    specs: list[SandboxSpecInfo]
    default_id: str

    async def search_sandbox_specs(
        self, page_id: str | None = None, limit: int = 100
    ) -> SandboxSpecInfoPage:
        start = int(page_id) if page_id else 0
        end = start + limit
        return SandboxSpecInfoPage(
            items=self.specs[start:end],
            next_page_id=str(end) if end < len(self.specs) else None,
        )

    async def get_sandbox_spec(self, sandbox_spec_id: str) -> SandboxSpecInfo | None:
        return next((s for s in self.specs if s.id == sandbox_spec_id), None)

    async def get_default_sandbox_spec(self) -> SandboxSpecInfo:
        spec = await self.get_sandbox_spec(self.default_id)
        assert spec is not None
        return spec


@dataclass
class FixedAccess(SandboxSpecAccess):
    allowed: set[str]
    org_default: str | None = None

    async def filter_accessible(self, spec_ids: Iterable[str]) -> set[str]:
        return set(spec_ids) & self.allowed

    async def get_default_spec_id(self) -> str | None:
        return self.org_default


def _service(
    allowed: set[str],
    org_default: str | None = None,
    names: tuple[str, ...] = ('small', 'large', 'private'),
    default_id: str = 'small',
) -> ScopedSandboxSpecService:
    return ScopedSandboxSpecService(
        delegate=InMemorySpecService([_spec(n) for n in names], default_id),
        access=FixedAccess(allowed, org_default),
    )


async def _ids(service: SandboxSpecService) -> list[str]:
    return [s.id for s in (await service.search_sandbox_specs()).items]


async def test_search_hides_specs_the_caller_cannot_use():
    service = _service({'small', 'large'})

    assert await _ids(service) == ['small', 'large']


async def test_search_pages_over_the_accessible_specs_only():
    service = _service({'small', 'private'})

    first = await service.search_sandbox_specs(limit=1)
    second = await service.search_sandbox_specs(page_id=first.next_page_id, limit=1)

    assert [s.id for s in first.items] == ['small']
    assert [s.id for s in second.items] == ['private']
    assert second.next_page_id is None


async def test_search_covers_specs_beyond_the_delegates_first_page():
    names = tuple(f'spec{i}' for i in range(150))
    service = _service(set(names), names=names, default_id='spec0')

    first = await service.search_sandbox_specs()
    second = await service.search_sandbox_specs(page_id=first.next_page_id)

    assert (len(first.items), len(second.items)) == (100, 50)


async def test_get_returns_none_for_an_inaccessible_spec():
    service = _service({'small'})

    assert await service.get_sandbox_spec('private') is None
    assert (await service.get_sandbox_spec('small')).id == 'small'  # type: ignore[union-attr]


async def test_batch_get_is_scoped():
    service = _service({'small'})

    result = await service.batch_get_sandbox_specs(['small', 'private', 'missing'])

    assert [s.id if s else None for s in result] == ['small', None, None]


async def test_default_prefers_the_org_default():
    service = _service({'small', 'large'}, org_default='large')

    assert (await service.get_default_sandbox_spec()).id == 'large'


async def test_default_ignores_an_org_default_the_org_lost_access_to():
    service = _service({'small'}, org_default='private')

    assert (await service.get_default_sandbox_spec()).id == 'small'


async def test_default_never_returns_an_inaccessible_system_default():
    service = _service({'large'}, default_id='private')

    assert (await service.get_default_sandbox_spec()).id == 'large'


async def test_default_fails_when_nothing_is_accessible():
    service = _service(set())

    with pytest.raises(SandboxError, match='No sandbox specs are available'):
        await service.get_default_sandbox_spec()


async def test_is_accessible_distinguishes_revoked_from_unknown():
    service = _service({'small'})

    assert await service.is_sandbox_spec_accessible('small') is True
    assert await service.is_sandbox_spec_accessible('private') is False
    # An unknown spec (e.g. a stale image) is left to the callers that resolve specs.
    assert await service.is_sandbox_spec_accessible('retired') is True


async def test_existing_sandbox_keeps_its_spec_after_access_is_revoked():
    service = _service({'small'})

    spec = await service.resolve_existing_sandbox_spec('private', 'sandbox-1')

    assert spec.id == 'private'


async def test_new_sandbox_resolution_rejects_an_inaccessible_explicit_spec():
    service = _service({'small'})

    with pytest.raises(ValueError, match="'private' not found"):
        await resolve_sandbox_spec('private', None, service, _NullLogger())


async def test_new_sandbox_resolution_falls_back_from_an_inaccessible_user_default():
    service = _service({'small', 'large'}, org_default='large')

    spec = await resolve_sandbox_spec(None, 'private', service, _NullLogger())

    assert spec.id == 'large'


async def test_new_sandbox_resolution_uses_the_org_default_without_a_user_default():
    service = _service({'small', 'large'}, org_default='large')

    spec = await resolve_sandbox_spec(None, None, service, _NullLogger())

    assert spec.id == 'large'


class _NullLogger:
    def warning(self, *args, **kwargs) -> None:
        pass
