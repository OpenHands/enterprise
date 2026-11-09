"""API routes for organization-scoped MCP server listings (OHE-3091).

Every member can read the org's listings (credentials redacted) and launches
with them; only Admins/Owners (``MANAGE_ORG_MCP``) can create, edit or delete
them. A listing is either ``org``-managed (the admin's shared credential is
stored inline, encrypted, and never read back) or ``user``-managed (the
default: the config references a personal secret with ``${SECRET_NAME}`` and
each member connects by creating that secret).

Remote (SSE / SHTTP) and stdio servers are accepted. A stdio listing runs
inside each member's sandbox like a personal stdio server, takes its
credentials through ``env`` and cannot be connection-tested from the
settings page. OAuth listings are supported: an ``org``-managed one must be
authorized by the admin before it is saved (the tokens ride on the listing),
a ``user``-managed one stores only the OAuth client configuration and each
member authorizes with their own account through the connection endpoints
below. Names are immutable because Agent Profiles reference them.
"""

from __future__ import annotations

import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError

from openhands.app_server.mcp.mcp_oauth_router import completed_oauth_job_state
from openhands.app_server.mcp.mcp_secret_references import (
    mcp_server_secret_references,
)
from openhands.app_server.settings.settings_models import (
    _preserve_redacted_mcp_secrets,
)
from openhands.app_server.user_auth import reject_service_account
from openhands.app_server.utils.models import EditResponse
from openhands.sdk.mcp.config import MCPOAuthState, MCPServer, dump_mcp_config
from server.auth.authorization import Permission, require_permission
from server.routes.org_secret_validation import OrgSecretValidationRoute
from storage.mcp_config import coerce_persisted_mcp_config
from storage.org_mcp_servers import (
    ORG_MCP_SERVER_NAME_PATTERN,
    RESERVED_MCP_SERVER_NAMES,
    OrgMcpManagedBy,
    OrgMcpServer,
    has_oauth_tokens,
    oauth_client_id,
    redacted_org_mcp_server,
    takes_member_oauth_state,
    with_oauth_state,
)
from storage.org_mcp_servers_store import (
    OrgMcpMemberMissingError,
    OrgMcpServerAlreadyExistsError,
    OrgMcpServerConnectionError,
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
    # For a ``user``-managed OAuth listing: whether the caller has authorized
    # it with their own account. ``None`` for every other listing.
    connected: bool | None = None


class OrgMcpServerPage(BaseModel):
    items: list[OrgMcpServerItem]


class OrgMcpServerCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: OrgMcpServerName
    managed_by: OrgMcpManagedBy = 'user'
    # The SDK ``MCPServer`` shape (``url``, ``transport``, ``headers``,
    # ``auth``, ``timeout`` ... or ``command``, ``args``, ``env`` for stdio);
    # validated below so the 422 never echoes it.
    server: dict[str, Any]
    # An ``org``-managed OAuth listing: the finished
    # ``POST /api/v1/mcp/oauth/start`` job holding the tokens to share.
    oauth_job_id: str | None = None


class OrgMcpServerUpdate(BaseModel):
    """Replace the config and/or who manages it. Omitted fields keep their value."""

    model_config = ConfigDict(extra='forbid')

    managed_by: OrgMcpManagedBy | None = None
    server: dict[str, Any] | None = None
    # See ``OrgMcpServerCreate.oauth_job_id``.
    oauth_job_id: str | None = None


class OrgMcpServerConnection(BaseModel):
    """The finished ``POST /api/v1/mcp/oauth/start`` job a member authorized with."""

    model_config = ConfigDict(extra='forbid')

    job_id: str


async def _job_oauth_state(
    job_id: str, user_id: str, server: MCPServer
) -> MCPOAuthState:
    """The tokens the caller's finished job obtained for ``server``."""
    response = await completed_oauth_job_state(job_id, user_id, server.url)
    return MCPOAuthState.model_validate(
        response.model_dump(mode='json', exclude_none=True)
    )


async def _resolve_listing_oauth_state(
    server: MCPServer,
    managed_by: OrgMcpManagedBy,
    stored: MCPServer | None,
    oauth_job_id: str | None,
    user_id: str,
) -> MCPServer:
    """Apply the OAuth rules for who holds a listing's tokens."""
    if managed_by == 'user':
        # Each member authorizes with their own account; whatever tokens the
        # admin holds never become the listing's.
        return with_oauth_state(server, None)
    if oauth_job_id is not None:
        server = with_oauth_state(
            server, await _job_oauth_state(oauth_job_id, user_id, server)
        )
    elif (
        stored is not None
        and stored.oauth_auth is not None
        and oauth_client_id(stored) != oauth_client_id(server)
    ):
        # Tokens restored from the stored listing belong to its old client.
        server = with_oauth_state(server, None)
    # Every member launches with these tokens, and a sandbox cannot run the
    # consent flow, so the admin authorizes before saving.
    if not has_oauth_tokens(server):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='Authorize the organization-managed OAuth MCP server before saving it',
        )
    return server


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
    user_id: str,
    oauth_job_id: str | None = None,
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
    if server.command is not None:
        # A stdio listing runs in each member's sandbox and takes its
        # credentials through ``env``; the remote-only fields do not apply.
        if (
            server.url is not None
            or server.auth is not None
            or server.headers is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    'A stdio MCP server listing takes a command, arguments and '
                    'environment variables only; URL, auth and headers do not apply'
                ),
            )
    elif server.url is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='An MCP server listing needs a URL (SSE or SHTTP) or a command (stdio)',
        )
    if server.oauth_auth is not None:
        server = await _resolve_listing_oauth_state(
            server, managed_by, stored, oauth_job_id, user_id
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
    store = OrgMcpServersStore(org_id)
    try:
        listings = await store.list()
    except OrgMcpServersOrgMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    connected: set[str] = set()
    if any(takes_member_oauth_state(entry) for entry in listings.values()):
        connected = await store.member_connected_names(UUID(user_id), listings)
    return OrgMcpServerPage(
        items=[
            OrgMcpServerItem(
                name=name,
                managed_by=entry.managed_by,
                server=redacted_org_mcp_server(name, entry),
                secret_names=sorted(
                    mcp_server_secret_references(entry.server).required
                ),
                connected=(
                    name in connected if takes_member_oauth_state(entry) else None
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
        org_id,
        body.name,
        body.managed_by,
        body.server,
        stored=None,
        user_id=user_id,
        oauth_job_id=body.oauth_job_id,
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
    long as the server's URL (or a stdio server's command and arguments) is
    unchanged; so does an OAuth credential sent without its ``state``.
    Handing an ``org``-managed listing over to each user is refused (409)
    while an org Agent Profile references it.
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
        org_id,
        name,
        managed_by,
        raw_server,
        stored=current.server,
        user_id=user_id,
        oauth_job_id=body.oauth_job_id,
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


@org_mcp_servers_router.put(
    '/{org_id}/mcp-servers/{name}/connection',
    response_model=EditResponse,
    dependencies=[Depends(reject_service_account)],
)
async def connect_org_mcp_server(
    org_id: UUID,
    name: str,
    body: OrgMcpServerConnection,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> EditResponse:
    """Store the caller's own OAuth tokens for a ``user``-managed OAuth listing.

    Any member: the tokens are theirs (obtained through
    ``POST /api/v1/mcp/oauth/start``) and only their conversations launch
    with them. They are read from the finished job, so they never pass through
    the browser. 422 for a listing that is not a user-managed OAuth server or
    a job that did not obtain tokens for this listing's URL.
    """
    store = OrgMcpServersStore(org_id)
    try:
        entry = await store.get(name)
        if entry is None:
            raise OrgMcpServerNotFoundError(
                f'Organization MCP server {name!r} not found'
            )
        if not takes_member_oauth_state(entry):
            raise OrgMcpServerConnectionError(
                f'Organization MCP server {name!r} is not a user-managed OAuth server'
            )
        state = await _job_oauth_state(body.job_id, user_id, entry.server)
        await store.set_member_oauth_state(UUID(user_id), name, state)
    except OrgMcpServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except OrgMcpServerConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except (OrgMcpMemberMissingError, OrgMcpServersOrgMissingError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return EditResponse(message='Organization MCP server connected successfully')


@org_mcp_servers_router.delete(
    '/{org_id}/mcp-servers/{name}/connection',
    response_model=EditResponse,
    dependencies=[Depends(reject_service_account)],
)
async def disconnect_org_mcp_server(
    org_id: UUID,
    name: str,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> EditResponse:
    """Forget the caller's own OAuth tokens for ``name``. Idempotent."""
    try:
        await OrgMcpServersStore(org_id).clear_member_oauth_state(UUID(user_id), name)
    except (OrgMcpMemberMissingError, OrgMcpServersOrgMissingError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return EditResponse(message='Organization MCP server disconnected successfully')
