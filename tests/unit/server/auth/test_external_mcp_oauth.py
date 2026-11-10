"""Real Redis and PostgreSQL coverage for OpenHands-issued external MCP grants."""

import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastmcp import FastMCP
from pydantic import SecretStr
from redis.asyncio import Redis
from testcontainers.community.redis import RedisContainer

from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    StoredConversationMetadata,
)
from openhands.app_server.services.jwt_service import JwtAudience, JwtService
from openhands.app_server.shared import server_config
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth.external_mcp import mount_external_mcp
from server.auth.external_mcp_oauth import (
    ExternalMcpOAuth,
    RegisteredClient,
    parse_registered_clients,
)
from server.auth.external_mcp_tools import register_external_mcp_tools
from server.routes.external_mcp_consent import create_consent_router
from server.services.feature_flag_service import feature_flag_service
from storage.org_member import OrgMember
from storage.role import Role
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas
from storage.user import User

BASE_URL = 'https://app.example.com/external-mcp'
REDIRECT_URI = 'https://chatgpt.com/connector/oauth/test'
CLIENT_ID = 'chatgpt-registered-test'
CLIENT_SECRET = 'registered-secret-for-test'
PUBLIC_CLIENT_ID = 'desktop-tool-test'
PUBLIC_REDIRECT_URI = 'http://127.0.0.1:53682/callback'
WRITE_CLIENT_ID = 'agent-tool-test'
WRITE_REDIRECT_URI = 'http://127.0.0.1:53683/callback'
CLIENTS = {
    CLIENT_ID: RegisteredClient(
        client_id=CLIENT_ID,
        name='ChatGPT',
        redirect_uris=(REDIRECT_URI,),
        client_secret=CLIENT_SECRET,
    ),
    PUBLIC_CLIENT_ID: RegisteredClient(
        client_id=PUBLIC_CLIENT_ID,
        name='Desktop <Tool>',
        redirect_uris=(PUBLIC_REDIRECT_URI,),
    ),
    WRITE_CLIENT_ID: RegisteredClient(
        client_id=WRITE_CLIENT_ID,
        name='Agent Tool',
        redirect_uris=(WRITE_REDIRECT_URI,),
        scopes=('tasks:read', 'tasks:write'),
    ),
}
CLIENTS_JSON = json.dumps(
    [
        {
            'client_id': CLIENT_ID,
            'name': 'ChatGPT',
            'redirect_uris': [REDIRECT_URI],
            'client_secret': CLIENT_SECRET,
        },
        {
            'client_id': PUBLIC_CLIENT_ID,
            'name': 'Desktop <Tool>',
            'redirect_uris': [PUBLIC_REDIRECT_URI],
        },
    ]
)


@pytest.fixture(autouse=True)
def feature_on(monkeypatch):
    monkeypatch.setenv('ENABLE_EXTERNAL_MCP', 'true')
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


@pytest.fixture
def oauth_app(redis):
    provider = ExternalMcpOAuth(base_url=BASE_URL, clients=dict(CLIENTS), redis=redis)
    app = FastAPI()
    app.include_router(create_consent_router(provider))
    for route in provider.get_well_known_routes('/mcp'):
        app.router.routes.append(route)
    mcp = FastMCP('OpenHands', auth=provider)
    register_external_mcp_tools(mcp, BASE_URL, redis)
    mcp_app = mcp.http_app(path='/mcp', stateless_http=True, json_response=True)
    app.mount('/external-mcp', mcp_app)
    app.state.mcp_app = mcp_app
    return app, provider


def _mounted(app: FastAPI) -> bool:
    return any(getattr(route, 'path', None) == '/external-mcp' for route in app.routes)


@pytest.mark.parametrize(
    'clients',
    [
        None,
        '[]',
        'not json',
        '{"client_id": "x"}',
        '[{"client_id": "x", "name": "X", "redirect_uris": ["http://evil.example/cb"]}]',
        '[{"client_id": "x", "name": "X", "redirect_uris": []}]',
        '[{"client_id": "x", "redirect_uris": ["https://x.example/cb"]}]',
    ],
)
def test_mount_needs_valid_registered_clients(clients, monkeypatch):
    monkeypatch.setenv('OPENHANDS_EXTERNAL_MCP_BASE_URL', BASE_URL)
    if clients is None:
        monkeypatch.delenv('OPENHANDS_EXTERNAL_MCP_CLIENTS', raising=False)
    else:
        monkeypatch.setenv('OPENHANDS_EXTERNAL_MCP_CLIENTS', clients)
    app = FastAPI()
    mount_external_mcp(app)
    assert not _mounted(app)


def test_mount_needs_https_base_url(monkeypatch):
    monkeypatch.setenv('OPENHANDS_EXTERNAL_MCP_CLIENTS', CLIENTS_JSON)
    monkeypatch.setenv(
        'OPENHANDS_EXTERNAL_MCP_BASE_URL', 'http://app.example.com/external-mcp'
    )
    app = FastAPI()
    mount_external_mcp(app)
    assert not _mounted(app)


def test_registered_clients_reject_duplicates_and_accept_public_loopback():
    clients = parse_registered_clients(CLIENTS_JSON)
    assert clients[CLIENT_ID].client_secret == CLIENT_SECRET
    assert clients[PUBLIC_CLIENT_ID].client_secret is None
    duplicate = json.dumps(json.loads(CLIENTS_JSON)[:1] * 2)
    with pytest.raises(ValueError, match='Duplicate'):
        parse_registered_clients(duplicate)


async def test_mount_defaults_to_the_app_web_url(redis, monkeypatch):
    monkeypatch.delenv('OPENHANDS_EXTERNAL_MCP_BASE_URL', raising=False)
    monkeypatch.setenv('OPENHANDS_EXTERNAL_MCP_CLIENTS', CLIENTS_JSON)
    monkeypatch.setattr(
        'server.auth.external_mcp.get_global_config',
        lambda: type('Config', (), {'web_url': 'https://app.example.com'})(),
    )
    monkeypatch.setattr(
        'server.auth.external_mcp.get_redis_client_async', lambda: redis
    )
    app = FastAPI()
    mount_external_mcp(app)
    assert _mounted(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://app.example.com'
    ) as client:
        metadata = await client.get(
            '/.well-known/oauth-authorization-server/external-mcp'
        )
        assert metadata.status_code == 200
        assert metadata.json()['issuer'] == BASE_URL
        assert (await client.post('/external-mcp/mcp', json={})).status_code == 401
        assert (await client.get('/mcp/mcp')).status_code == 404


async def test_discovery_and_private_resource(oauth_app):
    app, provider = oauth_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://app.example.com'
    ) as client:
        metadata = await client.get(
            '/.well-known/oauth-authorization-server/external-mcp'
        )
        assert metadata.status_code == 200
        assert metadata.json()['issuer'] == BASE_URL
        assert metadata.json()['code_challenge_methods_supported'] == ['S256']
        assert (
            'client_secret_post'
            in metadata.json()['token_endpoint_auth_methods_supported']
        )
        resource = await client.get(
            '/.well-known/oauth-protected-resource/external-mcp/mcp'
        )
        assert resource.json()['resource'] == provider.resource
        assert resource.json()['authorization_servers'] == [BASE_URL]
        for headers in ({}, {'Authorization': 'Bearer api-key-not-a-cloud-grant'}):
            denied = await client.post('/external-mcp/mcp', headers=headers, json={})
            assert denied.status_code == 401


async def test_authorization_rejects_wrong_resource_client_redirect_and_scope(
    oauth_app, monkeypatch
):
    app, provider = oauth_app
    monkeypatch.setattr(
        server_config, 'user_auth_class', 'server.auth.saas_user_auth.SaasUserAuth'
    )
    params = {
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URI,
        'response_type': 'code',
        'scope': 'tasks:read',
        'state': 'client-state',
        'code_challenge': 'a' * 43,
        'code_challenge_method': 'S256',
        'resource': provider.resource,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url='https://app.example.com',
        follow_redirects=False,
    ) as client:
        for invalid in (
            {'client_id': 'unknown-client'},
            {'redirect_uri': 'https://attacker.example/collect'},
            {'resource': 'https://app.example.com/mcp/mcp'},
            {'scope': 'tasks:write'},
            {'code_challenge_method': 'plain'},
        ):
            response = await client.get(
                '/external-mcp/authorize', params={**params, **invalid}
            )
            if response.status_code in (302, 303, 307):
                callback = urlsplit(response.headers['location'])
                assert (
                    f'{callback.scheme}://{callback.netloc}{callback.path}'
                    == REDIRECT_URI
                )
                assert parse_qs(callback.query).get('error'), invalid
            else:
                assert 400 <= response.status_code < 500, (invalid, response.text)
        valid = await client.get('/external-mcp/authorize', params=params)
        assert valid.status_code in (302, 303, 307)
        consent = await client.get(valid.headers['location'])
        assert consent.status_code in (302, 307)
        login_url = urlsplit(consent.headers['location'])
        assert login_url.path == '/login'
        assert (
            '/external-mcp/consent?request=' in parse_qs(login_url.query)['returnTo'][0]
        )
        denied = await client.post(
            '/external-mcp/token',
            data={
                'grant_type': 'authorization_code',
                'client_id': CLIENT_ID,
                'code': 'none',
            },
        )
        assert denied.status_code == 401
        denied = await client.post(
            '/external-mcp/token',
            data={
                'grant_type': 'authorization_code',
                'client_id': CLIENT_ID,
                'client_secret': 'incorrect',
                'code': 'none',
            },
        )
        assert denied.status_code == 401


@pytest.mark.parametrize('login', ['oauth_v2', 'keycloak'])
async def test_cloud_login_consent_pkce_refresh_revoke_and_disabled_user(
    oauth_app, app_db_session, create_user, session_maker, monkeypatch, login
):
    app, provider = oauth_app
    user = create_user()
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

    monkeypatch.setattr(
        server_config, 'user_auth_class', 'server.auth.saas_user_auth.SaasUserAuth'
    )
    jwt_service = JwtService(
        keys=[
            EncryptionKey(
                kid='local',
                key=SecretStr('local-test-signing-key-32-bytes-plus'),
                active=True,
            )
        ]
    )
    monkeypatch.setattr('storage.encrypt_utils.get_jwt_service', lambda: jwt_service)
    if login == 'oauth_v2':
        cookie_name = 'openhands_auth'
        cookie = jwt_service.create_jws_token(
            {
                'user_id': str(user.id),
                'access_token_expires_at': None,
                'idp_provider_id': None,
                'accepted_tos': True,
            },
            expires_in=timedelta(hours=1),
            audience=JwtAudience.SESSION,
        )
    else:
        cookie_name = 'keycloak_auth'
        keycloak_token = jwt.encode(
            {'sub': str(user.id), 'email': None, 'email_verified': True},
            'local-test-signing-key-32-bytes-plus',
            algorithm='HS256',
        )
        cookie = jwt_service.create_jws_token(
            {
                'access_token': keycloak_token,
                'refresh_token': 'local-test-refresh-token',
                'accepted_tos': True,
            },
            expires_in=timedelta(hours=1),
            audience=JwtAudience.KEYCLOAK_SESSION,
        )
    verifier = secrets.token_urlsafe(40)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip('=')
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url='https://app.example.com',
        cookies={cookie_name: cookie},
        follow_redirects=False,
    ) as client:
        authorization = await client.get(
            '/external-mcp/authorize',
            params={
                'client_id': CLIENT_ID,
                'redirect_uri': REDIRECT_URI,
                'response_type': 'code',
                'scope': 'tasks:read',
                'state': 'independent-client-state',
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
                'resource': provider.resource,
            },
        )
        assert authorization.status_code in (302, 303, 307)
        consent_url = authorization.headers['location']
        consent = await client.get(consent_url)
        assert consent.status_code == 200
        assert 'Allow ChatGPT to read' in consent.text
        assert consent.headers['cache-control'] == 'no-store'
        handle = parse_qs(urlsplit(consent_url).query)['request'][0]
        csrf = re.search(r'name="csrf" value="([^"]+)"', consent.text).group(1)
        wrong = await client.post(
            '/external-mcp/consent',
            data={'handle': handle, 'csrf': 'wrong', 'decision': 'allow'},
        )
        assert wrong.status_code == 403
        consent = await client.get(consent_url)
        csrf = re.search(r'name="csrf" value="([^"]+)"', consent.text).group(1)
        allowed = await client.post(
            '/external-mcp/consent',
            data={'handle': handle, 'csrf': csrf, 'decision': 'allow'},
        )
        assert allowed.status_code == 303
        callback = urlsplit(allowed.headers['location'])
        assert f'{callback.scheme}://{callback.netloc}{callback.path}' == REDIRECT_URI
        assert parse_qs(callback.query)['state'] == ['independent-client-state']
        assert parse_qs(callback.query)['iss'] == [BASE_URL]
        code = parse_qs(callback.query)['code'][0]
        form = {
            'grant_type': 'authorization_code',
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'code': code,
            'redirect_uri': REDIRECT_URI,
            'code_verifier': verifier,
            'resource': provider.resource,
        }
        incorrect = await client.post(
            '/external-mcp/token', data={**form, 'code_verifier': 'a' * 43}
        )
        assert incorrect.status_code in (400, 401), incorrect.text
        assert incorrect.json()['error'] == 'invalid_grant'
        issued = await client.post('/external-mcp/token', data=form)
        assert issued.status_code == 200, issued.text
        tokens = issued.json()
        assert tokens['scope'] == 'tasks:read'
        assert (
            await provider.load_access_token(tokens['access_token'])
        ).subject == str(user.id)
        replay = await client.post('/external-mcp/token', data=form)
        assert replay.status_code in (400, 401)
        assert replay.json()['error'] == 'invalid_grant'
        refresh_form = {
            'grant_type': 'refresh_token',
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'refresh_token': tokens['refresh_token'],
            'resource': provider.resource,
        }
        rotated = await client.post('/external-mcp/token', data=refresh_form)
        assert rotated.status_code == 200, rotated.text
        reused_refresh = await client.post('/external-mcp/token', data=refresh_form)
        assert reused_refresh.status_code in (400, 401)
        assert reused_refresh.json()['error'] == 'invalid_grant'
        refreshed = rotated.json()
        assert (await provider.load_access_token(refreshed['access_token'])) is not None
        with session_maker() as session:
            session.get(User, user.id).is_disabled = True
            session.commit()
        assert await provider.load_access_token(refreshed['access_token']) is None
        with session_maker() as session:
            session.get(User, user.id).is_disabled = False
            session.get(OrgMember, (user.current_org_id, user.id)).status = 'inactive'
            session.commit()
        assert await provider.load_access_token(refreshed['access_token']) is None
        with session_maker() as session:
            session.get(OrgMember, (user.current_org_id, user.id)).status = 'active'
            session.commit()
        assert (await provider.load_access_token(refreshed['access_token'])) is not None
        revoked = await client.post(
            '/external-mcp/revoke',
            data={
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_SECRET,
                'token': refreshed['refresh_token'],
                'token_type_hint': 'refresh_token',
            },
        )
        assert revoked.status_code == 200
        assert await provider.load_access_token(refreshed['access_token']) is None


async def test_mcp_tool_only_lists_tasks_owned_in_granted_org(
    oauth_app, app_db_session, create_user, create_org, session_maker
):
    app, provider = oauth_app
    user = create_user()
    other_user = create_user(current_org_id=user.current_org_id)
    other_org = create_org()
    own_id, other_id, other_org_id = str(uuid4()), str(uuid4()), str(uuid4())
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
        for conversation_id, owner, org_id, title in (
            (own_id, user.id, user.current_org_id, 'my task'),
            (other_id, other_user.id, user.current_org_id, 'someone else task'),
            (other_org_id, user.id, other_org.id, 'my other organization task'),
        ):
            session.add(
                StoredConversationMetadata(
                    conversation_id=conversation_id,
                    title=title,
                    conversation_version='V1',
                    sandbox_id='local-test',
                )
            )
            session.add(
                StoredConversationMetadataSaas(
                    conversation_id=conversation_id,
                    user_id=owner,
                    org_id=org_id,
                )
            )
        session.commit()
    tokens = await provider._issue_tokens(
        {
            'client_id': CLIENT_ID,
            'resource': provider.resource,
            'user_id': str(user.id),
            'org_id': str(user.current_org_id),
            'issued_at': time.time(),
            'grant_id': secrets.token_urlsafe(24),
        }
    )
    async with app.state.mcp_app.lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url='https://app.example.com',
        ) as client:
            response = await client.post(
                '/external-mcp/mcp',
                headers={
                    'Authorization': f'Bearer {tokens.access_token}',
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
            assert own_id in response.text
            assert other_id not in response.text
            assert other_org_id not in response.text
            assert 'someone else task' not in response.text
            assert 'my other organization task' not in response.text
            listed = await client.post(
                '/external-mcp/mcp',
                headers={
                    'Authorization': f'Bearer {tokens.access_token}',
                    'Accept': 'application/json, text/event-stream',
                },
                json={'jsonrpc': '2.0', 'method': 'tools/list', 'id': 2, 'params': {}},
            )
            assert listed.status_code == 200, listed.text
            tools = listed.json()['result']['tools']
            by_name = {tool['name']: tool for tool in tools}
            assert sorted(by_name) == [
                'get_task',
                'list_my_tasks',
                'send_message',
                'start_task',
            ]
            assert by_name['list_my_tasks']['annotations']['readOnlyHint'] is True
            assert by_name['get_task']['annotations']['readOnlyHint'] is True
            assert by_name['start_task']['annotations']['readOnlyHint'] is False
            assert by_name['send_message']['annotations']['readOnlyHint'] is False


@pytest.fixture
def signed_in(app_db_session, create_user, session_maker, monkeypatch):
    """A member with an OAuth v2 session cookie."""
    user = create_user()
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
    monkeypatch.setattr(
        server_config, 'user_auth_class', 'server.auth.saas_user_auth.SaasUserAuth'
    )
    jwt_service = JwtService(
        keys=[
            EncryptionKey(
                kid='local',
                key=SecretStr('local-test-signing-key-32-bytes-plus'),
                active=True,
            )
        ]
    )
    monkeypatch.setattr('storage.encrypt_utils.get_jwt_service', lambda: jwt_service)
    cookie = jwt_service.create_jws_token(
        {
            'user_id': str(user.id),
            'access_token_expires_at': None,
            'idp_provider_id': None,
            'accepted_tos': True,
        },
        expires_in=timedelta(hours=1),
        audience=JwtAudience.SESSION,
    )
    return user, {'openhands_auth': cookie}


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(40)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip('=')
    )
    return verifier, challenge


async def _approve(
    client, provider, client_id, redirect_uri, challenge, scope='tasks:read'
):
    """Run authorize and consent; return the consent page and the code."""
    authorization = await client.get(
        '/external-mcp/authorize',
        params={
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': scope,
            'code_challenge': challenge,
            'code_challenge_method': 'S256',
            'resource': provider.resource,
        },
    )
    assert authorization.status_code in (302, 303, 307), authorization.text
    consent_url = authorization.headers['location']
    consent = await client.get(consent_url)
    if consent.status_code != 200:
        return consent, None
    handle = parse_qs(urlsplit(consent_url).query)['request'][0]
    csrf = re.search(r'name="csrf" value="([^"]+)"', consent.text).group(1)
    allowed = await client.post(
        '/external-mcp/consent',
        data={'handle': handle, 'csrf': csrf, 'decision': 'allow'},
    )
    assert allowed.status_code == 303, allowed.text
    callback = urlsplit(allowed.headers['location'])
    assert f'{callback.scheme}://{callback.netloc}{callback.path}' == redirect_uri
    return consent, parse_qs(callback.query)['code'][0]


async def test_public_client_connects_with_pkce_and_no_secret(oauth_app, signed_in):
    app, provider = oauth_app
    user, cookies = signed_in
    verifier, challenge = _pkce()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url='https://app.example.com',
        cookies=cookies,
        follow_redirects=False,
    ) as client:
        consent, code = await _approve(
            client, provider, PUBLIC_CLIENT_ID, PUBLIC_REDIRECT_URI, challenge
        )
        # The page names the connecting client (escaped) and where it returns.
        assert 'Connect Desktop &lt;Tool&gt; to OpenHands?' in consent.text
        assert '<Tool>' not in consent.text
        assert 'sent back to 127.0.0.1:53682' in consent.text
        # Chrome blocks the redirect back to the client unless its origin is
        # allowed as a form target.
        assert consent.headers['content-security-policy'] == (
            "default-src 'none'; form-action 'self' http://127.0.0.1:53682"
        )
        form = {
            'grant_type': 'authorization_code',
            'client_id': PUBLIC_CLIENT_ID,
            'code': code,
            'redirect_uri': PUBLIC_REDIRECT_URI,
            'code_verifier': verifier,
            'resource': provider.resource,
        }
        # Another registered client cannot redeem this client's code.
        stolen = await client.post(
            '/external-mcp/token',
            data={**form, 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET},
        )
        assert stolen.status_code in (400, 401), stolen.text
        issued = await client.post('/external-mcp/token', data=form)
        assert issued.status_code == 200, issued.text
        token = await provider.load_access_token(issued.json()['access_token'])
        assert token.subject == str(user.id)
        assert token.client_id == PUBLIC_CLIENT_ID
        # A port the administrator did not register is refused.
        other_port = await client.get(
            '/external-mcp/authorize',
            params={
                'client_id': PUBLIC_CLIENT_ID,
                'redirect_uri': 'http://127.0.0.1:9999/callback',
                'response_type': 'code',
                'scope': 'tasks:read',
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
                'resource': provider.resource,
            },
        )
        assert 400 <= other_port.status_code < 500


async def test_unregistering_a_client_ends_its_grants(oauth_app, signed_in):
    app, provider = oauth_app
    _, cookies = signed_in
    verifier, challenge = _pkce()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url='https://app.example.com',
        cookies=cookies,
        follow_redirects=False,
    ) as client:
        _, code = await _approve(
            client, provider, PUBLIC_CLIENT_ID, PUBLIC_REDIRECT_URI, challenge
        )
        issued = await client.post(
            '/external-mcp/token',
            data={
                'grant_type': 'authorization_code',
                'client_id': PUBLIC_CLIENT_ID,
                'code': code,
                'redirect_uri': PUBLIC_REDIRECT_URI,
                'code_verifier': verifier,
                'resource': provider.resource,
            },
        )
        access = issued.json()['access_token']
        assert await provider.load_access_token(access) is not None
        del provider.clients[PUBLIC_CLIENT_ID]
        assert await provider.load_access_token(access) is None
        # The other client is unaffected and still registered.
        assert await provider.get_client(CLIENT_ID) is not None


async def test_feature_flag_gates_consent_and_existing_grants(
    oauth_app, signed_in, monkeypatch
):
    app, provider = oauth_app
    _, cookies = signed_in
    verifier, challenge = _pkce()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url='https://app.example.com',
        cookies=cookies,
        follow_redirects=False,
    ) as client:
        _, code = await _approve(
            client, provider, PUBLIC_CLIENT_ID, PUBLIC_REDIRECT_URI, challenge
        )
        issued = await client.post(
            '/external-mcp/token',
            data={
                'grant_type': 'authorization_code',
                'client_id': PUBLIC_CLIENT_ID,
                'code': code,
                'redirect_uri': PUBLIC_REDIRECT_URI,
                'code_verifier': verifier,
                'resource': provider.resource,
            },
        )
        tokens = issued.json()
        assert await provider.load_access_token(tokens['access_token']) is not None

        monkeypatch.setenv('ENABLE_EXTERNAL_MCP', 'false')
        feature_flag_service.invalidate()
        assert await provider.load_access_token(tokens['access_token']) is None
        refreshed = await client.post(
            '/external-mcp/token',
            data={
                'grant_type': 'refresh_token',
                'client_id': PUBLIC_CLIENT_ID,
                'refresh_token': tokens['refresh_token'],
                'resource': provider.resource,
            },
        )
        assert refreshed.status_code in (400, 401)
        consent, code = await _approve(
            client, provider, PUBLIC_CLIENT_ID, PUBLIC_REDIRECT_URI, challenge
        )
        assert consent.status_code == 403
        assert code is None

        monkeypatch.setenv('ENABLE_EXTERNAL_MCP', 'true')
        feature_flag_service.invalidate()
        assert await provider.load_access_token(tokens['access_token']) is not None


# ---------------------------------------------------------------------------
# Write scope and the tools that start and continue tasks
# ---------------------------------------------------------------------------


def test_registered_client_scopes_default_to_read_and_reject_unknown():
    clients = parse_registered_clients(CLIENTS_JSON)
    assert clients[PUBLIC_CLIENT_ID].scopes == ('tasks:read',)
    entry = json.loads(CLIENTS_JSON)[1]
    with_write = parse_registered_clients(
        json.dumps([{**entry, 'scopes': ['tasks:write', 'tasks:read']}])
    )
    assert with_write[PUBLIC_CLIENT_ID].scopes == ('tasks:read', 'tasks:write')
    for bad in (['tasks:write'], ['tasks:read', 'admin'], 'tasks:read', []):
        with pytest.raises(ValueError, match='scopes'):
            parse_registered_clients(json.dumps([{**entry, 'scopes': bad}]))


async def _token(client, provider, client_id, redirect_uri, scope):
    verifier, challenge = _pkce()
    consent, code = await _approve(
        client, provider, client_id, redirect_uri, challenge, scope=scope
    )
    issued = await client.post(
        '/external-mcp/token',
        data={
            'grant_type': 'authorization_code',
            'client_id': client_id,
            'code': code,
            'redirect_uri': redirect_uri,
            'code_verifier': verifier,
            'resource': provider.resource,
        },
    )
    assert issued.status_code == 200, issued.text
    return consent, issued.json()


async def test_write_scope_needs_an_allowed_client_and_is_spelled_out(
    oauth_app, signed_in
):
    app, provider = oauth_app
    _, cookies = signed_in
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url='https://app.example.com',
        cookies=cookies,
        follow_redirects=False,
    ) as client:
        # A read-only client cannot ask for write.
        refused = await client.get(
            '/external-mcp/authorize',
            params={
                'client_id': PUBLIC_CLIENT_ID,
                'redirect_uri': PUBLIC_REDIRECT_URI,
                'response_type': 'code',
                'scope': 'tasks:read tasks:write',
                'code_challenge': 'a' * 43,
                'code_challenge_method': 'S256',
                'resource': provider.resource,
            },
        )
        callback = urlsplit(refused.headers.get('location', ''))
        assert parse_qs(callback.query).get('error') or refused.status_code >= 400
        assert not parse_qs(callback.query).get('code')

        read_page, read_tokens = await _token(
            client, provider, WRITE_CLIENT_ID, WRITE_REDIRECT_URI, 'tasks:read'
        )
        assert 'start new tasks' not in read_page.text
        assert read_tokens['scope'] == 'tasks:read'

        write_page, tokens = await _token(
            client,
            provider,
            WRITE_CLIENT_ID,
            WRITE_REDIRECT_URI,
            'tasks:read tasks:write',
        )
        assert 'Agent Tool will also be able to start new tasks' in write_page.text
        assert 'with your access' in write_page.text
        assert tokens['scope'] == 'tasks:read tasks:write'
        granted = await provider.load_access_token(tokens['access_token'])
        assert granted.scopes == ['tasks:read', 'tasks:write']

        # A refresh keeps the scopes and cannot widen a read-only grant.
        widened = await client.post(
            '/external-mcp/token',
            data={
                'grant_type': 'refresh_token',
                'client_id': WRITE_CLIENT_ID,
                'refresh_token': read_tokens['refresh_token'],
                'scope': 'tasks:read tasks:write',
                'resource': provider.resource,
            },
        )
        assert widened.status_code in (400, 401), widened.text
        rotated = await client.post(
            '/external-mcp/token',
            data={
                'grant_type': 'refresh_token',
                'client_id': WRITE_CLIENT_ID,
                'refresh_token': tokens['refresh_token'],
                'resource': provider.resource,
            },
        )
        assert rotated.status_code == 200, rotated.text
        assert rotated.json()['scope'] == 'tasks:read tasks:write'

        # The administrator takes write away from the client: write grants stop.
        provider.clients[WRITE_CLIENT_ID] = RegisteredClient(
            client_id=WRITE_CLIENT_ID,
            name='Agent Tool',
            redirect_uris=(WRITE_REDIRECT_URI,),
        )
        assert await provider.load_access_token(rotated.json()['access_token']) is None


class _Context:
    """Async context manager returning a fixed service, like the injectors."""

    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *exc):
        return False


async def _grant(provider, user, scopes, client_id=WRITE_CLIENT_ID, org_id=None):
    tokens = await provider._issue_tokens(
        {
            'client_id': client_id,
            'resource': provider.resource,
            'user_id': str(user.id),
            'org_id': str(org_id or user.current_org_id),
            'issued_at': time.time(),
            'grant_id': secrets.token_urlsafe(24),
            'scopes': scopes,
        }
    )
    return tokens.access_token


async def _call(client, token, name, arguments):
    response = await client.post(
        '/external-mcp/mcp',
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json, text/event-stream',
        },
        json={
            'jsonrpc': '2.0',
            'method': 'tools/call',
            'id': 1,
            'params': {'name': name, 'arguments': arguments},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()['result']
    text = result['content'][0]['text'] if result.get('content') else ''
    return bool(result.get('isError')), result.get('structuredContent'), text


@pytest.fixture
def two_members(app_db_session, create_user, session_maker):
    """Two active members of the same organization."""
    user = create_user()
    other = create_user(current_org_id=user.current_org_id)
    with session_maker() as session:
        role = Role(name='member', rank=3)
        session.add(role)
        session.flush()
        for member in (user, other):
            session.add(
                OrgMember(
                    org_id=user.current_org_id,
                    user_id=member.id,
                    role_id=role.id,
                    status='active',
                    llm_api_key='local-test-only',
                )
            )
        session.commit()
    return user, other


def _store_conversation(session_maker, conversation_id, user, title='stored task'):
    with session_maker() as session:
        session.add(
            StoredConversationMetadata(
                conversation_id=conversation_id,
                title=title,
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


async def test_start_task_runs_as_the_granting_user_with_only_the_allowed_fields(
    oauth_app, two_members, monkeypatch
):
    from openhands.app_server.app_conversation.app_conversation_models import (
        AppConversationStartTask,
        AppConversationStartTaskStatus,
        ConversationTrigger,
    )
    from openhands.app_server.user.specifiy_user_context import USER_CONTEXT_ATTR

    app, provider = oauth_app
    user, other = two_members
    seen: dict = {}

    class FakeConversations:
        async def start_app_conversation(self, request):
            seen['request'] = request
            task = AppConversationStartTask(
                created_by_user_id=str(user.id), request=request
            )
            seen['task'] = task
            yield task
            # Let the tool return while the start is still in progress.
            await asyncio.sleep(0.3)
            task.status = AppConversationStartTaskStatus.READY
            seen['finished'] = True
            yield task

    class FakeStartTasks:
        async def get_app_conversation_start_task(self, task_id):
            return seen['task'] if task_id == seen['task'].id else None

    def conversations(state):
        seen['context'] = getattr(state, USER_CONTEXT_ATTR)
        return _Context(FakeConversations())

    monkeypatch.setattr(
        'server.auth.external_mcp_tools.get_app_conversation_service', conversations
    )
    monkeypatch.setattr(
        'server.auth.external_mcp_tools.get_app_conversation_start_task_service',
        lambda state: _Context(FakeStartTasks()),
    )
    write = await _grant(provider, user, ['tasks:read', 'tasks:write'])
    read_only = await _grant(provider, user, ['tasks:read'])
    others = await _grant(provider, other, ['tasks:read', 'tasks:write'])
    async with app.state.mcp_app.lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='https://app.example.com'
        ) as client:
            # A read-only grant cannot start or continue anything.
            for name, arguments in (
                ('start_task', {'prompt': 'do it'}),
                ('send_message', {'task_id': str(uuid4()), 'message': 'more'}),
            ):
                failed, _, text = await _call(client, read_only, name, arguments)
                assert failed and 'tasks:write' in text
            assert 'request' not in seen

            for bad in (
                {'prompt': '   '},
                {'prompt': 'x' * 8001},
                {'prompt': 'fix it', 'branch': 'main'},
            ):
                failed, _, _ = await _call(client, write, 'start_task', bad)
                assert failed
            assert 'request' not in seen

            failed, started, text = await _call(
                client,
                write,
                'start_task',
                {
                    'prompt': ' Fix the flaky test ',
                    'repository': 'acme/app',
                    'branch': 'dev',
                },
            )
            assert not failed, text
            request = seen['request']
            assert started['status'] == 'starting'
            assert started['id'] == str(request.conversation_id)
            assert started['url'] == (
                f'https://app.example.com/canvas/conversations/{started["id"]}'
            )
            assert request.initial_message.content[0].text == 'Fix the flaky test'
            assert request.selected_repository == 'acme/app'
            assert request.selected_branch == 'dev'
            assert request.trigger == ConversationTrigger.REMOTE_API_KEY
            assert request.tags == {'source': 'mcp', 'mcpclient': WRITE_CLIENT_ID}
            # Nothing a caller could use to widen the launch is set.
            for field in (
                'system_prompt',
                'system_message_suffix',
                'llm_model',
                'llm_profile',
                'agent_profile_id',
                'sandbox_id',
                'processors',
            ):
                assert getattr(request, field) is None, field
            # The conversation service acts as the granting user in the granted org.
            context = seen['context']
            assert await context.get_user_id() == str(user.id)
            assert context.resolver_org_id == user.current_org_id

            # Before the conversation is stored, only its starter can see it.
            failed, pending, _ = await _call(
                client, write, 'get_task', {'task_id': started['id']}
            )
            assert not failed and pending['status'] == 'starting'
            failed, _, text = await _call(
                client, others, 'get_task', {'task_id': started['id']}
            )
            assert failed and 'Unknown task' in text
            failed, _, text = await _call(
                client,
                write,
                'send_message',
                {'task_id': started['id'], 'message': 'more'},
            )
            assert failed and 'still starting' in text
            # The start keeps running to completion after the tool has returned.
            assert not seen.get('finished')
            for _ in range(100):
                if seen.get('finished'):
                    break
                await asyncio.sleep(0.02)
            assert seen.get('finished')


async def test_get_task_and_send_message_only_reach_owned_conversations(
    oauth_app, two_members, session_maker, monkeypatch
):
    from fastapi import HTTPException

    from openhands.sdk import Message, MessageEvent, TextContent
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    app, provider = oauth_app
    user, other = two_members
    own_id, other_id = uuid4(), uuid4()
    _store_conversation(session_maker, str(own_id), user, title='my task')
    _store_conversation(session_maker, str(other_id), other, title='their task')
    calls: dict = {'get': [], 'events': [], 'sent': []}
    state = {'execution': ConversationExecutionStatus.RUNNING}

    class Conversation:
        title = 'my task'
        selected_repository = 'acme/app'
        selected_branch = 'dev'
        pr_number = [12]

        class sandbox_status:
            value = 'RUNNING'

        @property
        def execution_status(self):
            return state['execution']

    class FakeConversations:
        async def get_app_conversation(self, conversation_id):
            calls['get'].append(conversation_id)
            return Conversation()

    class FakeEvents:
        async def search_events(self, conversation_id, **kwargs):
            calls['events'].append(conversation_id)
            events = [
                MessageEvent(
                    source='agent',
                    llm_message=Message(
                        role='assistant', content=[TextContent(text='All tests pass.')]
                    ),
                ),
                MessageEvent(
                    source='user',
                    llm_message=Message(
                        role='user', content=[TextContent(text='Fix the test')]
                    ),
                ),
            ]
            return type('Page', (), {'items': events})()

    async def fake_send(conversation_id, request, **services):
        calls['sent'].append((conversation_id, request.content[0].text, request.run))
        if request.content[0].text == 'while paused':
            raise HTTPException(409, 'Sandbox is PAUSED. Resume it first.')

    module = 'server.auth.external_mcp_tools.'
    monkeypatch.setattr(
        module + 'get_app_conversation_service', lambda s: _Context(FakeConversations())
    )
    monkeypatch.setattr(module + 'get_event_service', lambda s: _Context(FakeEvents()))
    monkeypatch.setattr(module + 'get_sandbox_service', lambda s: _Context(object()))
    monkeypatch.setattr(module + 'get_httpx_client', lambda s: _Context(object()))
    monkeypatch.setattr(module + 'send_message_to_conversation', fake_send)
    token = await _grant(provider, user, ['tasks:read', 'tasks:write'])
    async with app.state.mcp_app.lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='https://app.example.com'
        ) as client:
            failed, task, text = await _call(
                client, token, 'get_task', {'task_id': str(own_id)}
            )
            assert not failed, text
            assert task['status'] == 'running' and task['result'] is None
            assert task['title'] == 'my task' and task['pull_requests'] == [12]
            assert calls['events'] == []

            state['execution'] = ConversationExecutionStatus.FINISHED
            failed, task, _ = await _call(
                client, token, 'get_task', {'task_id': str(own_id)}
            )
            assert task['status'] == 'finished'
            assert task['result'] == 'All tests pass.'

            # Someone else's conversation, an unknown id and a malformed id all look
            # the same, and no service is asked about them.
            calls['get'].clear()
            for task_id in (str(other_id), str(uuid4()), 'not-a-uuid'):
                for name, arguments in (
                    ('get_task', {'task_id': task_id}),
                    ('send_message', {'task_id': task_id, 'message': 'hi'}),
                ):
                    failed, _, text = await _call(client, token, name, arguments)
                    assert failed and 'Unknown task' in text, (name, task_id)
            assert calls['get'] == [] and calls['sent'] == []

            failed, sent, text = await _call(
                client,
                token,
                'send_message',
                {'task_id': str(own_id), 'message': ' also update the docs '},
            )
            assert not failed, text
            assert sent == {'id': str(own_id), 'status': 'accepted'}
            assert calls['sent'] == [(own_id, 'also update the docs', True)]

            failed, _, text = await _call(
                client,
                token,
                'send_message',
                {'task_id': str(own_id), 'message': 'while paused'},
            )
            assert failed and 'PAUSED' in text
