"""API routes for organization-scoped MCP server listings (OHE-3091).

Every member can read the org's listings (credentials redacted) and launches
with them; only Admins/Owners (``MANAGE_ORG_MCP``) can create, edit or delete
them. A listing is either ``org``-managed (the admin's shared credential is
stored inline, encrypted, and never read back) or ``user``-managed (the
default: the config references a personal secret with ``${SECRET_NAME}`` and
each member connects by creating that secret).

Remote servers only: stdio servers run inside each member's sandbox, and an
OAuth credential needs a per-owner token store the org does not have yet.
Names are immutable because Agent Profiles reference them.
"""

from __future__ import annotations

import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError

from openhands.app_server.mcp.mcp_secret_references import (
    mcp_server_secret_references,
)
from openhands.app_server.settings.settings_models import (
    _preserve_redacted_mcp_secrets,
)
from openhands.app_server.utils.models import EditResponse
from openhands.sdk.mcp.config import MCPOAuthAuthCredential, MCPServer, dump_mcp_config
from server.auth.authorization import Permission, require_permission
from server.routes.org_secret_validation import OrgSecretValidationRoute
from storage.mcp_config import coerce_persisted_mcp_config
from storage.org_mcp_servers import (
    ORG_MCP_SERVER_NAME_PATTERN,
    RESERVED_MCP_SERVER_NAMES,
    OrgMcpManagedBy,
    OrgMcpServer,
    redacted_org_mcp_server,
)
from storage.org_mcp_servers_store import (
    OrgMcpServerAlreadyExistsError,
    OrgMcpServerInUseError,
    OrgMcpServerNotFoundError,
    OrgMcpServersOrgMissingError,
    OrgMcpServersStore,
)
from storage.org_secrets_store import OrgSecretsStore

_NAME_RE = re.compile(ORG_MCP_SERVER_NAME_PATTERN)


def _validate_server_name(name: str) -> str:
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            'MCP server name may only contain letters, digits, "-" and "_"'
        )
    if name in RESERVED_MCP_SERVER_NAMES:
        raise ValueError(f'{name!r} is reserved for the system MCP server')
    return name


OrgMcpServerName = Annotated[str, AfterValidator(_validate_server_name)]

org_mcp_servers_router = APIRouter(
    prefix='/api/organizations',
    tags=['Org MCP Servers'],
    # Never reflect a submitted credential back in a 422.
    route_class=OrgSecretValidationRoute,
)


class OrgMcpServerItem(BaseModel):
    """A listing as members see it: the config with every credential masked."""

    name: str
    managed_by: OrgMcpManagedBy
    server: dict[str, Any]
    # Names of the secrets the config references with ``${NAME}`` and no
    # default. The references themselves sit inside credential fields, which
    # the masked ``server`` hides, so a member learns here which personal
    # secret a ``user``-managed listing needs.
    secret_names: list[str]


class OrgMcpServerPage(BaseModel):
    items: list[OrgMcpServerItem]


class OrgMcpServerCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: OrgMcpServerName
    managed_by: OrgMcpManagedBy = 'user'
    # The SDK ``MCPServer`` shape (``url``, ``transport``, ``headers``,
    # ``auth``, ``timeout`` ...); validated below so the 422 never echoes it.
    server: dict[str, Any]


class OrgMcpServerUpdate(BaseModel):
    """Replace the config and/or who manages it. Omitted fields keep their value."""

    model_config = ConfigDict(extra='forbid')

    managed_by: OrgMcpManagedBy | None = None
    server: dict[str, Any] | None = None


def _validation_detail(exc: ValidationError) -> list[dict[str, Any]]:
    # ``input`` and ``ctx`` may carry the submitted credential; keep them out.
    return [
        {
            'type': error['type'],
            'loc': ['body', 'server', *error['loc']],
            'msg': error['msg'],
        }
        for error in exc.errors()
    ]


async def _validate_listing(
    org_id: UUID,
    name: str,
    managed_by: OrgMcpManagedBy,
    raw_server: dict[str, Any],
    stored: MCPServer | None,
) -> MCPServer:
    """Turn a submitted config into a storable ``MCPServer`` or raise 422.

    Redaction markers are restored from the stored listing of the same name
    before validation (the SDK model turns a marker into ``None``), the same
    pass the settings API and the connection test run.
    """
    restored = _preserve_redacted_mcp_secrets(
        {name: raw_server}, {name: stored} if stored is not None else None
    )[name]
    try:
        server = coerce_persisted_mcp_config({name: restored})[name]
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_validation_detail(exc),
        ) from None
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f'Invalid MCP server configuration: {type(exc).__name__}',
        ) from None
    if server.url is None or server.command is not None or server.transport == 'stdio':
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                'Organization MCP server listings must be remote servers (SSE or SHTTP)'
            ),
        )
    if isinstance(server.auth, MCPOAuthAuthCredential):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                'OAuth-authenticated MCP servers cannot be shared with the '
                'organization yet'
            ),
        )
    if managed_by == 'org':
        # Members' personal secrets shadow org-shared ones of the same name at
        # launch, so an org identity may only come from org-shared secrets.
        referenced = mcp_server_secret_references(server).names
        if referenced:
            store = await OrgSecretsStore.get_instance(org_id)
            shared = {secret.name for secret in await store.list_shared()}
            unknown = sorted(referenced - shared)
            if unknown:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        'An organization-managed MCP server may only reference '
                        'organization-shared secrets; not shared: ' + ', '.join(unknown)
                    ),
                )
    return server


@org_mcp_servers_router.get(
    '/{org_id}/mcp-servers',
    response_model=OrgMcpServerPage,
)
async def list_org_mcp_servers(
    org_id: UUID,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> OrgMcpServerPage:
    """List the org's MCP server listings with every credential masked.

    Available to all org members; conversations launch with these servers
    under the member's own ones.
    """
    try:
        listings = await OrgMcpServersStore(org_id).list()
    except OrgMcpServersOrgMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return OrgMcpServerPage(
        items=[
            OrgMcpServerItem(
                name=name,
                managed_by=entry.managed_by,
                server=redacted_org_mcp_server(name, entry),
                secret_names=sorted(
                    mcp_server_secret_references(entry.server).required
                ),
            )
            for name, entry in listings.items()
        ]
    )


@org_mcp_servers_router.post(
    '/{org_id}/mcp-servers',
    status_code=status.HTTP_201_CREATED,
    response_model=EditResponse,
)
async def create_org_mcp_server(
    org_id: UUID,
    body: OrgMcpServerCreate,
    user_id: str = Depends(require_permission(Permission.MANAGE_ORG_MCP)),
) -> EditResponse:
    """Create a listing. Admin/Owner only.

    An ``org``-managed listing's credential is encrypted at rest and never
    read back by anyone, including its creator.
    """
    server = await _validate_listing(
        org_id, body.name, body.managed_by, body.server, stored=None
    )
    try:
        await OrgMcpServersStore(org_id).create(
            body.name, OrgMcpServer(managed_by=body.managed_by, server=server)
        )
    except OrgMcpServerAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except OrgMcpServersOrgMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return EditResponse(message='Organization MCP server created successfully')


@org_mcp_servers_router.put(
    '/{org_id}/mcp-servers/{name}',
    response_model=EditResponse,
)
async def update_org_mcp_server(
    org_id: UUID,
    name: str,
    body: OrgMcpServerUpdate,
    user_id: str = Depends(require_permission(Permission.MANAGE_ORG_MCP)),
) -> EditResponse:
    """Replace a listing's config and/or management. Admin/Owner only.

    A redacted credential in the submitted config keeps the stored value as
    long as the server's URL is unchanged. Handing an ``org``-managed listing
    over to each user is refused (409) while an org Agent Profile references
    it.
    """
    store = OrgMcpServersStore(org_id)
    try:
        current = await store.get(name)
    except OrgMcpServersOrgMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Organization MCP server {name!r} not found',
        )
    managed_by = body.managed_by or current.managed_by
    raw_server = (
        body.server
        if body.server is not None
        else dump_mcp_config(
            {name: current.server}, context={'expose_secrets': 'plaintext'}
        )[name]
    )
    server = await _validate_listing(
        org_id, name, managed_by, raw_server, stored=current.server
    )
    try:
        await store.update(name, OrgMcpServer(managed_by=managed_by, server=server))
    except OrgMcpServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except OrgMcpServerInUseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return EditResponse(message='Organization MCP server updated successfully')


@org_mcp_servers_router.delete(
    '/{org_id}/mcp-servers/{name}',
    response_model=EditResponse,
)
async def delete_org_mcp_server(
    org_id: UUID,
    name: str,
    user_id: str = Depends(require_permission(Permission.MANAGE_ORG_MCP)),
) -> EditResponse:
    """Delete a listing. Admin/Owner only; 409 while an org Agent Profile references it."""
    try:
        await OrgMcpServersStore(org_id).delete(name)
    except OrgMcpServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except OrgMcpServerInUseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except OrgMcpServersOrgMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return EditResponse(message='Organization MCP server deleted successfully')
