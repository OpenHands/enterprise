"""Mount a second, default-off MCP endpoint for Orca parity work.

Separate from ``/external-mcp`` (the legacy ChatGPT-facing endpoint, pinned
and unchanged by this module): its own mount path, its own registered-client
list, its own feature gate, and its own OAuth grant namespace, built from the
same reusable primitives (``ExternalMcpOAuth``, ``create_consent_router``,
``register_external_mcp_tools``). A bad or absent configuration leaves this
endpoint unmounted; it never falls back to the legacy one.
"""

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

MOUNT_PATH = '/enterprise-mcp'
CONSENT_PATH = f'{MOUNT_PATH}/consent'
CLIENTS_ENV = 'OPENHANDS_ENTERPRISE_MCP_CLIENTS'
BASE_URL_ENV = 'OPENHANDS_ENTERPRISE_MCP_BASE_URL'
FEATURE_FLAG = 'ENABLE_ENTERPRISE_MCP'
REDIS_KEY_PREFIX = 'enterprise-mcp-oauth'


def _base_url() -> str | None:
    """The public URL of the endpoint: an override, else the app's web URL."""
    override = os.getenv(BASE_URL_ENV)
    if override:
        return override.rstrip('/')
    web_url = get_global_config().web_url
    return f'{web_url.rstrip("/")}{MOUNT_PATH}' if web_url else None


def mount_enterprise_mcp(app: FastAPI) -> None:
    """Mount the endpoint when at least one MCP client is registered.

    Mirrors ``mount_external_mcp``'s structure and safety properties (see
    that function's docstring) on an independent mount path, client list,
    feature flag and grant namespace, so it can be rolled out, audited and
    disabled without any effect on the legacy endpoint.
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
        logger.exception('enterprise_mcp_not_mounted:invalid_configuration')
        return
    if not clients or base_url is None:
        return
    provider = ExternalMcpOAuth(
        base_url=base_url,
        clients=clients,
        redis=get_redis_client_async(),
        redis_key_prefix=REDIS_KEY_PREFIX,
        feature_flag=FEATURE_FLAG,
    )
    mcp = FastMCP('OpenHands', auth=provider, mask_error_details=True)
    register_external_mcp_tools(mcp, base_url, provider.redis)
    mcp_app = mcp.http_app(path='/mcp', stateless_http=True, json_response=True)
    app.include_router(create_consent_router(provider, consent_path=CONSENT_PATH))
    for route in provider.get_well_known_routes('/mcp'):
        app.router.routes.append(route)
    app.mount(MOUNT_PATH, mcp_app)
    app.router.lifespan_context = combine_lifespans(
        app.router.lifespan_context, mcp_app.lifespan
    )
