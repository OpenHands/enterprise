"""Isolation tests for the enterprise-mcp resource, mounted alongside legacy.

Uses the two real production mount entry points -- mount_external_mcp
(legacy, pinned) and mount_enterprise_mcp (new, default-off) -- on one
synthetic FastAPI app, the same way saas_server.py mounts both. Confirms the
legacy endpoint is unaffected by the new one, the two endpoints have
independent client lists and feature gates, and a grant minted for one
resource is rejected by the other.
"""

import json
import secrets
import time
from datetime import datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from testcontainers.community.redis import RedisContainer

from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    StoredConversationMetadata,
)
from server.auth.enterprise_mcp import mount_enterprise_mcp
from server.auth.external_mcp import mount_external_mcp
from server.auth.external_mcp_oauth import ExternalMcpOAuth
from server.services.feature_flag_service import feature_flag_service
from storage.org_member import OrgMember
from storage.role import Role
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas

LEGACY_BASE_URL = 'https://app.example.com/external-mcp'
ENTERPRISE_BASE_URL = 'https://app.example.com/enterprise-mcp'

LEGACY_CLIENT_ID = 'legacy-only-client'
LEGACY_CLIENTS_JSON = json.dumps(
    [
        {
            'client_id': LEGACY_CLIENT_ID,
            'name': 'Legacy Client',
            'redirect_uris': ['https://chatgpt.com/connector/oauth/test'],
            'scopes': ['tasks:read', 'tasks:write'],
        }
    ]
)

ENTERPRISE_CLIENT_ID = 'enterprise-only-client'
ENTERPRISE_CLIENTS_JSON = json.dumps(
    [
        {
            'client_id': ENTERPRISE_CLIENT_ID,
            'name': 'Enterprise MCP Client',
            'redirect_uris': ['https://mcp-client.example.com/oauth/callback'],
            'scopes': ['tasks:read', 'tasks:write'],
        }
    ]
)


@pytest.fixture(autouse=True)
def both_features_on(monkeypatch):
    monkeypatch.setenv('ENABLE_EXTERNAL_MCP', 'true')
    monkeypatch.setenv('ENABLE_ENTERPRISE_MCP', 'true')
    feature_flag_service.invalidate()
    yield
    feature_flag_service.invalidate()


@pytest.fixture(scope='module')
def redis_container():
    with RedisContainer('redis:7.4') as container:
        yield container


@pytest.fixture
async def redis(redis_container):
    client = Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
    )
    try:
        yield client
    finally:
        await client.aclose()


def _mounted(app: FastAPI, path: str) -> bool:
    return any(getattr(route, 'path', None) == path for route in app.routes)


def _capturing(sink: list) -> type:
    """A thin ExternalMcpOAuth subclass that records every real instance built.

    Lets the test observe the provider a production mount function actually
    constructed (its resource, redis_key_prefix, feature flag) without
    changing mount_external_mcp/mount_enterprise_mcp to expose it.
    """

    class _Capturing(ExternalMcpOAuth):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sink.append(self)

    return _Capturing


@pytest.fixture
def configured_mcp_clients(redis, monkeypatch):
    monkeypatch.setenv('OPENHANDS_EXTERNAL_MCP_CLIENTS', LEGACY_CLIENTS_JSON)
    monkeypatch.setenv('OPENHANDS_EXTERNAL_MCP_BASE_URL', LEGACY_BASE_URL)
    monkeypatch.setenv('OPENHANDS_ENTERPRISE_MCP_CLIENTS', ENTERPRISE_CLIENTS_JSON)
    monkeypatch.setenv('OPENHANDS_ENTERPRISE_MCP_BASE_URL', ENTERPRISE_BASE_URL)
    monkeypatch.setattr(
        'server.auth.external_mcp.get_redis_client_async', lambda: redis
    )
    monkeypatch.setattr(
        'server.auth.enterprise_mcp.get_redis_client_async', lambda: redis
    )


@pytest.fixture
def both_mounted(configured_mcp_clients, monkeypatch):
    """Both real production mount entry points, on one app, both enabled."""
    legacy_providers: list[ExternalMcpOAuth] = []
    enterprise_providers: list[ExternalMcpOAuth] = []
    monkeypatch.setattr(
        'server.auth.external_mcp.ExternalMcpOAuth', _capturing(legacy_providers)
    )
    monkeypatch.setattr(
        'server.auth.enterprise_mcp.ExternalMcpOAuth', _capturing(enterprise_providers)
    )
    app = FastAPI()
    mount_external_mcp(app)
    mount_enterprise_mcp(app)
    assert len(legacy_providers) == 1 and len(enterprise_providers) == 1
    return app, legacy_providers[0], enterprise_providers[0]


async def _grant(provider, user, scopes, client_id):
    tokens = await provider._issue_tokens(
        {
            'client_id': client_id,
            'resource': provider.resource,
            'user_id': str(user.id),
            'org_id': str(user.current_org_id),
            'issued_at': time.time(),
            'grant_id': secrets.token_urlsafe(24),
            'scopes': scopes,
        }
    )
    return tokens.access_token


async def _tools_list(client, mount_path, token):
    return await client.post(
        f'{mount_path}/mcp',
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json, text/event-stream',
        },
        json={'jsonrpc': '2.0', 'method': 'tools/list', 'id': 1, 'params': {}},
    )


def _make_active_member(session_maker, user):
    with session_maker() as session:
        role = Role(name='member', rank=3)
        session.add(role)
        session.flush()
        session.add(
            OrgMember(
                org_id=user.current_org_id,
                user_id=user.id,
                role_id=role.id,
                status='active',
                llm_api_key='local-test-only',
            )
        )
        session.commit()


def test_enterprise_mcp_default_off_leaves_legacy_mounted(redis, monkeypatch):
    """With no enterprise env vars, only the legacy endpoint mounts."""
    monkeypatch.setenv('OPENHANDS_EXTERNAL_MCP_CLIENTS', LEGACY_CLIENTS_JSON)
    monkeypatch.setenv('OPENHANDS_EXTERNAL_MCP_BASE_URL', LEGACY_BASE_URL)
    monkeypatch.delenv('OPENHANDS_ENTERPRISE_MCP_CLIENTS', raising=False)
    monkeypatch.delenv('OPENHANDS_ENTERPRISE_MCP_BASE_URL', raising=False)
    monkeypatch.setattr(
        'server.auth.external_mcp.get_redis_client_async', lambda: redis
    )
    app = FastAPI()
    mount_external_mcp(app)
    mount_enterprise_mcp(app)
    assert _mounted(app, '/external-mcp')
    assert not _mounted(app, '/enterprise-mcp')


@pytest.mark.parametrize(
    'base_url',
    [
        ENTERPRISE_BASE_URL + '?bad=1',
        ENTERPRISE_BASE_URL + '#bad',
        'https://app.example.com:invalid/enterprise-mcp',
        'https://user@app.example.com/enterprise-mcp',
    ],
)
def test_invalid_enterprise_base_url_leaves_legacy_mounted(
    configured_mcp_clients, monkeypatch, base_url
):
    monkeypatch.setenv('OPENHANDS_ENTERPRISE_MCP_BASE_URL', base_url)
    app = FastAPI()
    mount_external_mcp(app)
    mount_enterprise_mcp(app)
    assert _mounted(app, '/external-mcp')
    assert not _mounted(app, '/enterprise-mcp')


def test_invalid_enterprise_client_scopes_leave_legacy_mounted(
    configured_mcp_clients, monkeypatch
):
    clients = json.loads(ENTERPRISE_CLIENTS_JSON)
    clients[0]['scopes'] = ['tasks:read', {'scope': 'tasks:write'}]
    monkeypatch.setenv('OPENHANDS_ENTERPRISE_MCP_CLIENTS', json.dumps(clients))
    app = FastAPI()
    mount_external_mcp(app)
    mount_enterprise_mcp(app)
    assert _mounted(app, '/external-mcp')
    assert not _mounted(app, '/enterprise-mcp')


async def test_legacy_discovery_and_tools_unchanged_with_enterprise_mounted(
    both_mounted,
):
    """Mounting /enterprise-mcp alongside it does not alter the legacy wire contract."""
    app, legacy_provider, _ = both_mounted
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://app.example.com'
    ) as client:
        metadata = await client.get(
            '/.well-known/oauth-authorization-server/external-mcp'
        )
        assert metadata.status_code == 200
        assert metadata.json()['issuer'] == LEGACY_BASE_URL
        oidc_path = '/.well-known/openid-configuration'
        assert (
            sum(getattr(route, 'path', None) == oidc_path for route in app.routes) == 1
        )
        oidc = await client.get(oidc_path)
        assert oidc.status_code == 200
        assert oidc.json()['issuer'] == LEGACY_BASE_URL
        resource = await client.get(
            '/.well-known/oauth-protected-resource/external-mcp/mcp'
        )
        assert resource.status_code == 200
        assert resource.json()['resource'] == legacy_provider.resource
        denied = await client.post('/external-mcp/mcp', json={})
        assert denied.status_code == 401
        assert denied.headers['www-authenticate'] == (
            'Bearer scope="tasks:read", resource_metadata='
            '"https://app.example.com/.well-known/oauth-protected-resource'
            '/external-mcp/mcp"'
        )


async def test_enterprise_mcp_has_its_own_discovery_documents(both_mounted):
    app, _, enterprise_provider = both_mounted
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://app.example.com'
    ) as client:
        metadata = await client.get(
            '/.well-known/oauth-authorization-server/enterprise-mcp'
        )
        assert metadata.status_code == 200
        assert metadata.json()['issuer'] == ENTERPRISE_BASE_URL
        resource = await client.get(
            '/.well-known/oauth-protected-resource/enterprise-mcp/mcp'
        )
        assert resource.status_code == 200
        assert resource.json()['resource'] == enterprise_provider.resource
        denied = await client.post('/enterprise-mcp/mcp', json={})
        assert denied.status_code == 401
        assert denied.headers['www-authenticate'] == (
            'Bearer scope="tasks:read", resource_metadata='
            '"https://app.example.com/.well-known/oauth-protected-resource'
            '/enterprise-mcp/mcp"'
        )


def test_providers_have_independent_client_lists_and_namespaces(both_mounted):
    _, legacy_provider, enterprise_provider = both_mounted
    assert LEGACY_CLIENT_ID in legacy_provider.clients
    assert ENTERPRISE_CLIENT_ID not in legacy_provider.clients
    assert ENTERPRISE_CLIENT_ID in enterprise_provider.clients
    assert LEGACY_CLIENT_ID not in enterprise_provider.clients
    assert legacy_provider.redis_key_prefix == 'external-mcp-oauth'
    assert enterprise_provider.redis_key_prefix == 'enterprise-mcp-oauth'
    assert legacy_provider.resource != enterprise_provider.resource


async def test_enterprise_feature_flag_is_independent_of_legacy(
    both_mounted, app_db_session, create_user, session_maker, monkeypatch
):
    """Disabling ENABLE_ENTERPRISE_MCP does not disable ENABLE_EXTERNAL_MCP."""
    _, legacy_provider, enterprise_provider = both_mounted
    user = create_user(email_verified=True, accepted_tos=datetime.now())
    _make_active_member(session_maker, user)

    legacy_token = await _grant(legacy_provider, user, ['tasks:read'], LEGACY_CLIENT_ID)
    enterprise_token = await _grant(
        enterprise_provider, user, ['tasks:read'], ENTERPRISE_CLIENT_ID
    )
    assert await legacy_provider.load_access_token(legacy_token) is not None
    assert await enterprise_provider.load_access_token(enterprise_token) is not None

    monkeypatch.setenv('ENABLE_ENTERPRISE_MCP', 'false')
    feature_flag_service.invalidate()
    try:
        assert await legacy_provider.load_access_token(legacy_token) is not None
        assert await enterprise_provider.load_access_token(enterprise_token) is None
    finally:
        feature_flag_service.invalidate()


async def test_grant_for_one_resource_is_rejected_by_the_other(
    both_mounted, app_db_session, create_user, session_maker
):
    """Audience separation: a token minted for one resource fails on the other."""
    app, legacy_provider, enterprise_provider = both_mounted
    user = create_user(email_verified=True, accepted_tos=datetime.now())
    _make_active_member(session_maker, user)

    legacy_token = await _grant(legacy_provider, user, ['tasks:read'], LEGACY_CLIENT_ID)
    enterprise_token = await _grant(
        enterprise_provider, user, ['tasks:read'], ENTERPRISE_CLIENT_ID
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://app.example.com'
    ) as client:
        # mount_external_mcp/mount_enterprise_mcp fold both FastMCP session
        # managers into app.router.lifespan_context; entering it once starts
        # both (see combine_lifespans in openhands/app_server/app.py).
        async with app.router.lifespan_context(app):
            legacy_ok = await _tools_list(client, '/external-mcp', legacy_token)
            assert legacy_ok.status_code == 200, legacy_ok.text
            assert 'result' in legacy_ok.json()

            enterprise_ok = await _tools_list(
                client, '/enterprise-mcp', enterprise_token
            )
            assert enterprise_ok.status_code == 200, enterprise_ok.text
            assert 'result' in enterprise_ok.json()

            cross_1 = await _tools_list(client, '/enterprise-mcp', legacy_token)
            assert cross_1.status_code == 401, cross_1.text

            cross_2 = await _tools_list(client, '/external-mcp', enterprise_token)
            assert cross_2.status_code == 401, cross_2.text


async def test_list_my_tasks_returns_canvas_links_from_both_resources(
    both_mounted, app_db_session, create_user, session_maker
):
    app, legacy, enterprise = both_mounted
    user = create_user(email_verified=True, accepted_tos=datetime.now())
    _make_active_member(session_maker, user)
    conversation_id = str(uuid4())
    with session_maker() as session:
        session.add(
            StoredConversationMetadata(
                conversation_id=conversation_id,
                title='My task',
                conversation_version='V1',
                sandbox_id='local-test',
            )
        )
        session.add(
            StoredConversationMetadataSaas(
                conversation_id=conversation_id,
                user_id=user.id,
                org_id=user.current_org_id,
            )
        )
        session.commit()

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='https://app.example.com'
        ) as client,
    ):
        for path, provider, client_id in (
            ('/external-mcp', legacy, LEGACY_CLIENT_ID),
            ('/enterprise-mcp', enterprise, ENTERPRISE_CLIENT_ID),
        ):
            token = await _grant(provider, user, ['tasks:read'], client_id)
            response = await client.post(
                f'{path}/mcp',
                headers={
                    'Authorization': f'Bearer {token}',
                    'Accept': 'application/json, text/event-stream',
                },
                json={
                    'jsonrpc': '2.0',
                    'method': 'tools/call',
                    'id': 1,
                    'params': {'name': 'list_my_tasks', 'arguments': {}},
                },
            )
            assert response.status_code == 200, response.text
            tasks = response.json()['result']['structuredContent']['result']
            assert len(tasks) == 1
            assert (
                tasks[0]['url']
                == f'https://app.example.com/canvas/conversations/{conversation_id}'
            )
