"""Mount the external MCP endpoint, never the internal Git-writing MCP server."""

import os
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastmcp import FastMCP

from openhands.app_server.app import combine_lifespans
from openhands.app_server.config import get_global_config
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.external_mcp_oauth import ExternalMcpOAuth, parse_registered_clients
from server.auth.external_mcp_tools import register_external_mcp_tools
from server.routes.external_mcp_consent import create_consent_router
from storage.redis import get_redis_client_async

MOUNT_PATH = '/external-mcp'
CLIENTS_ENV = 'OPENHANDS_EXTERNAL_MCP_CLIENTS'
BASE_URL_ENV = 'OPENHANDS_EXTERNAL_MCP_BASE_URL'


def _base_url() -> str | None:
    """The public URL of the endpoint: an override, else the app's web URL."""
    override = os.getenv(BASE_URL_ENV)
    if override:
        return override.rstrip('/')
    web_url = get_global_config().web_url
    return f'{web_url.rstrip("/")}{MOUNT_PATH}' if web_url else None


def mount_external_mcp(app: FastAPI) -> None:
    """Mount the endpoint when at least one MCP client is registered.

    Mounting only makes the endpoint reachable. Whether a user may connect a
    client, and whether an issued grant still works, is decided per request by
    the ``ENABLE_EXTERNAL_MCP`` feature flag. A bad configuration leaves the
    endpoint unmounted and the rest of the app running.
    """
    raw_clients = os.getenv(CLIENTS_ENV)
    if not raw_clients:
        return
    try:
        clients = parse_registered_clients(raw_clients)
        base_url = _base_url()
        base = urlsplit(base_url or '')
        if base.scheme != 'https' or not base.hostname or base.path != MOUNT_PATH:
            raise ValueError(f'The endpoint needs an HTTPS URL ending in {MOUNT_PATH}')
    except ValueError:
        logger.exception('external_mcp_not_mounted:invalid_configuration')
        return
    if not clients or base_url is None:
        return
    provider = ExternalMcpOAuth(
        base_url=base_url, clients=clients, redis=get_redis_client_async()
    )
    mcp = FastMCP('OpenHands', auth=provider, mask_error_details=True)
    register_external_mcp_tools(mcp, base_url, provider.redis)
    mcp_app = mcp.http_app(path='/mcp', stateless_http=True, json_response=True)
    app.include_router(create_consent_router(provider))
    for route in provider.get_well_known_routes('/mcp'):
        app.router.routes.append(route)
    app.mount(MOUNT_PATH, mcp_app)
    app.router.lifespan_context = combine_lifespans(
        app.router.lifespan_context, mcp_app.lifespan
    )
