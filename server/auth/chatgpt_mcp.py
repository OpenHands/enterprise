"""Mount a separate Cloud OAuth resource, never the internal Git-writing MCP server."""

import os
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastmcp import FastMCP

from openhands.app_server.app import combine_lifespans
from server.auth.chatgpt_oauth import CloudMcpOAuth
from server.auth.chatgpt_tools import register_chatgpt_tools
from server.routes.chatgpt_consent import create_consent_router
from storage.redis import get_redis_client_async


def mount_chatgpt_mcp(app: FastAPI) -> None:
    if os.getenv('OPENHANDS_CHATGPT_OAUTH_ENABLED') != 'true':
        return

    base_url = os.environ['OPENHANDS_CHATGPT_OAUTH_BASE_URL'].rstrip('/')
    client_id = os.environ['OPENHANDS_CHATGPT_OAUTH_CLIENT_ID']
    client_secret = os.environ['OPENHANDS_CHATGPT_OAUTH_CLIENT_SECRET']
    redirect_uri = os.environ['OPENHANDS_CHATGPT_OAUTH_REDIRECT_URI']
    base = urlsplit(base_url)
    redirect = urlsplit(redirect_uri)
    if (
        base.scheme != 'https'
        or not base.hostname
        or base.path != '/chatgpt'
        or not client_id
        or not client_secret
        or redirect.scheme != 'https'
        or redirect.hostname != 'chatgpt.com'
        or not redirect.path.startswith('/connector/')
    ):
        raise ValueError('Invalid ChatGPT OAuth endpoint or registered redirect URI')

    provider = CloudMcpOAuth(
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        redis=get_redis_client_async(),
    )
    mcp = FastMCP('OpenHands Cloud for ChatGPT', auth=provider, mask_error_details=True)
    register_chatgpt_tools(mcp, base_url)
    mcp_app = mcp.http_app(path='/mcp', stateless_http=True, json_response=True)
    app.include_router(create_consent_router(provider))
    for route in provider.get_well_known_routes('/mcp'):
        app.router.routes.append(route)
    app.mount('/chatgpt', mcp_app)
    app.router.lifespan_context = combine_lifespans(
        app.router.lifespan_context, mcp_app.lifespan
    )
