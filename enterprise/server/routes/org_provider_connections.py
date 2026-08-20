"""Organization LLM provider-connections router.

Provides CRUD over org-level *provider connections* — a shared ``api_key`` +
optional ``base_url`` that one or more LLM profiles reference by id. The
credential is resolved into a profile's runnable LLM at activation time (see
``org_profiles.activate_profile``); this router only manages the stored
connections.

Mirrors ``org_profiles``:
- Storage is the ``org.provider_connections`` ``EncryptedJSON`` column.
- Mutations run inside ``SELECT ... FOR UPDATE`` on the org row so concurrent
  writes serialize instead of racing (last-writer-wins would drop changes).
- CRUD requires ``EDIT_ORG_SETTINGS``; listing requires ``VIEW_ORG_SETTINGS``.

Permission model:
- List/Get: VIEW_ORG_SETTINGS
- Create/Update/Delete: EDIT_ORG_SETTINGS (owner/admin)
"""

import contextlib
from typing import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from openhands.app_server.settings.provider_connections import (
    CONNECTION_ID_PATTERN,
    ProviderConnection,
    ProviderConnectionInUseError,
    ProviderConnectionLimitExceededError,
    ProviderConnections,
    now_epoch,
)
from openhands.app_server.utils.logger import openhands_logger as logger
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.routes.org_models import OrgNotFoundError
from storage.agent_profile_resolution import load_llm_profiles
from storage.database import a_session_maker
from storage.org import Org
from storage.org_service import OrgService

from ..auth.authorization import Permission, require_permission

router = APIRouter(tags=['Organization Provider Connections'])


# ── Request/Response Models ────────────────────────────────────────────────


class ProviderConnectionCreateRequest(BaseModel):
    """Body for creating a provider connection.

    ``extra='forbid'`` so a typo'd or credential-bearing extra field (e.g. a
    custom header) fails loud instead of being silently dropped — matching the
    SDK's create request and ``StrictLLM``.
    """

    display_name: str = Field(..., min_length=1, max_length=128)
    provider: str = Field(default='custom', min_length=1, max_length=128)
    api_key: SecretStr = Field(..., min_length=1)
    base_url: str | None = Field(default=None, max_length=2048)

    model_config = ConfigDict(extra='forbid')


class ProviderConnectionUpdateRequest(BaseModel):
    """Body for partially updating a provider connection.

    Only ``base_url`` may be set to null (to clear it). ``api_key: null`` is
    rejected — a connection must always keep a key; omit ``api_key`` to leave
    it unchanged. ``display_name`` / ``provider`` null is rejected because they
    are required on the stored model.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    provider: str | None = Field(default=None, min_length=1, max_length=128)
    api_key: SecretStr | None = None
    base_url: str | None = Field(default=None, max_length=2048)

    model_config = ConfigDict(extra='forbid')

    @model_validator(mode='after')
    def _reject_null_required_fields(self) -> 'ProviderConnectionUpdateRequest':
        for field in ('display_name', 'provider', 'api_key'):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f'{field} cannot be set to null')
        return self


class ProviderConnectionResponse(BaseModel):
    """Secret-free view of a stored connection."""

    id: str
    display_name: str
    provider: str
    base_url: str | None = None
    created_at: int
    updated_at: int
    api_key_set: bool = False


class ProviderConnectionListResponse(BaseModel):
    connections: list[ProviderConnectionResponse]


# ── Helper Functions ────────────────────────────────────────────────────────


def _load_connections(org: Org) -> ProviderConnections:
    """Load ProviderConnections from the org row, defaulting to empty.

    Degrades to empty on schema drift rather than 500-ing — same contract as
    ``org_profiles._load_profiles``.
    """
    if org.provider_connections is None:
        return ProviderConnections()
    try:
        return ProviderConnections.model_validate(org.provider_connections)
    except Exception as exc:  # noqa: BLE001 - parity with _load_profiles
        logger.warning(
            'Failed to load org provider connections for %s: %s', org.id, exc
        )
        return ProviderConnections()


def _to_response(conn: ProviderConnection) -> ProviderConnectionResponse:
    return ProviderConnectionResponse(
        id=conn.id,
        display_name=conn.display_name,
        provider=conn.provider,
        base_url=conn.base_url,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
        api_key_set=conn.api_key_value() is not None,
    )


def _referencing_profiles(org: Org, connection_id: str) -> list[str]:
    """Names of LLM profiles whose LLM links to ``connection_id``."""
    profiles = load_llm_profiles(org)
    return sorted(
        name
        for name, llm in profiles.profiles.items()
        if getattr(llm, 'provider_connection_id', None) == connection_id
    )


async def _get_org(org_id: UUID, user_id: str) -> Org:
    try:
        return await OrgService.get_org_by_id(org_id=org_id, user_id=user_id)
    except OrgNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@contextlib.asynccontextmanager
async def _org_connections_transaction(
    org_id: UUID, user_id: str
) -> AsyncIterator[tuple[AsyncSession, Org, ProviderConnections]]:
    """Yield ``(session, org, connections)`` for a single locked mutation.

    Wraps read → mutate → write in one session with ``SELECT ... FOR UPDATE``
    so concurrent connection mutations serialize at the database level. The
    caller mutates ``connections`` in place; on normal exit the helper
    serializes it back onto the org row (with secrets exposed into the encrypted
    column) and commits. Exceptions skip the commit, so partial state never
    lands.
    """
    await _get_org(org_id, user_id)

    async with a_session_maker() as session:
        result = await session.execute(
            select(Org).filter(Org.id == org_id).with_for_update()
        )
        org = result.scalars().first()
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f'Organization {org_id} not found',
            )
        connections = _load_connections(org)
        yield session, org, connections
        org.provider_connections = connections.model_dump(
            mode='json', context={'expose_secrets': True}
        )
        await session.commit()


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get(
    '/{org_id}/provider-connections',
    response_model=ProviderConnectionListResponse,
)
async def list_provider_connections(
    org_id: UUID,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> ProviderConnectionListResponse:
    """List all provider connections for this organization (no secrets)."""
    org = await _get_org(org_id, user_id)
    connections = _load_connections(org)
    return ProviderConnectionListResponse(
        connections=[_to_response(conn) for conn in connections.list()]
    )


@router.post(
    '/{org_id}/provider-connections',
    response_model=ProviderConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider_connection(
    org_id: UUID,
    request: ProviderConnectionCreateRequest = Body(...),
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
) -> ProviderConnectionResponse:
    """Create a new provider connection. The id is generated server-side."""
    import uuid as _uuid

    async with _org_connections_transaction(org_id, user_id) as (
        _session,
        _org,
        connections,
    ):
        connection_id = _uuid.uuid4().hex
        now = now_epoch()
        conn = ProviderConnection(
            id=connection_id,
            display_name=request.display_name,
            provider=request.provider,
            api_key=request.api_key,
            base_url=request.base_url,
            created_at=now,
            updated_at=now,
        )
        try:
            connections.create(conn)
        except ProviderConnectionLimitExceededError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    return _to_response(conn)


@router.patch(
    '/{org_id}/provider-connections/{connection_id}',
    response_model=ProviderConnectionResponse,
)
async def update_provider_connection(
    org_id: UUID,
    connection_id: str = Path(..., min_length=1, pattern=CONNECTION_ID_PATTERN),
    request: ProviderConnectionUpdateRequest = Body(...),
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
) -> ProviderConnectionResponse:
    """Partially update a provider connection.

    Rotating the key (``api_key`` present) takes effect the next time a linked
    profile is activated — resolution is read-at-use, nothing is pushed into
    already-active settings or a running conversation.
    """
    async with _org_connections_transaction(org_id, user_id) as (
        _session,
        _org,
        connections,
    ):
        existing = connections.get(connection_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider connection '{connection_id}' not found",
            )
        fields = request.model_fields_set
        updated = existing.model_copy(
            update={
                **(
                    {'display_name': request.display_name}
                    if 'display_name' in fields
                    else {}
                ),
                **({'provider': request.provider} if 'provider' in fields else {}),
                **({'api_key': request.api_key} if 'api_key' in fields else {}),
                # base_url is the one field where an explicit null clears it.
                **({'base_url': request.base_url} if 'base_url' in fields else {}),
                'updated_at': now_epoch(),
            }
        )
        connections.update(updated)

    return _to_response(updated)


@router.delete(
    '/{org_id}/provider-connections/{connection_id}',
    response_model=ProviderConnectionResponse,
)
async def delete_provider_connection(
    org_id: UUID,
    connection_id: str = Path(..., min_length=1, pattern=CONNECTION_ID_PATTERN),
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
) -> ProviderConnectionResponse:
    """Delete a provider connection.

    Blocked with 409 if any LLM profile still references it by
    ``provider_connection_id``. Both collections live on the same org row, so
    the ``SELECT ... FOR UPDATE`` lock makes the referrer check and the delete
    atomic (no TOCTOU window).
    """
    async with _org_connections_transaction(org_id, user_id) as (
        _session,
        org,
        connections,
    ):
        existing = connections.get(connection_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider connection '{connection_id}' not found",
            )
        referrers = _referencing_profiles(org, connection_id)
        if referrers:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(ProviderConnectionInUseError(connection_id, referrers)),
            )
        connections.delete(connection_id)

    return _to_response(existing)
