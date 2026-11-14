"""Real Redis and PostgreSQL coverage for Cloud-issued ChatGPT MCP grants."""

import base64
import hashlib
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
from server.auth.chatgpt_mcp import mount_chatgpt_mcp
from server.auth.chatgpt_oauth import CloudMcpOAuth
from server.auth.chatgpt_tools import register_chatgpt_tools
from server.routes.chatgpt_consent import create_consent_router
from storage.org_member import OrgMember
from storage.role import Role
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas
from storage.user import User

BASE_URL = 'https://app.example.com/chatgpt'
REDIRECT_URI = 'https://chatgpt.com/connector/oauth/test'
CLIENT_ID = 'chatgpt-registered-test'
CLIENT_SECRET = 'registered-secret-for-test'


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
    provider = CloudMcpOAuth(
        base_url=BASE_URL,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        redis=redis,
    )
    app = FastAPI()
    app.include_router(create_consent_router(provider))
    for route in provider.get_well_known_routes('/mcp'):
        app.router.routes.append(route)
    mcp = FastMCP('ChatGPT', auth=provider)
    register_chatgpt_tools(mcp, BASE_URL)
    mcp_app = mcp.http_app(path='/mcp', stateless_http=True, json_response=True)
    app.mount('/chatgpt', mcp_app)
    app.state.mcp_app = mcp_app
    return app, provider


async def test_production_mount_requires_explicit_configuration(redis, monkeypatch):
    monkeypatch.delenv('OPENHANDS_CHATGPT_OAUTH_ENABLED', raising=False)
    app = FastAPI()
    mount_chatgpt_mcp(app)
    assert all(getattr(route, 'path', None) != '/chatgpt' for route in app.routes)
    monkeypatch.setenv('OPENHANDS_CHATGPT_OAUTH_ENABLED', 'true')
    monkeypatch.setenv('OPENHANDS_CHATGPT_OAUTH_BASE_URL', BASE_URL)
    monkeypatch.setenv('OPENHANDS_CHATGPT_OAUTH_CLIENT_ID', CLIENT_ID)
    monkeypatch.setenv('OPENHANDS_CHATGPT_OAUTH_CLIENT_SECRET', CLIENT_SECRET)
    monkeypatch.setenv('OPENHANDS_CHATGPT_OAUTH_REDIRECT_URI', REDIRECT_URI)
    monkeypatch.setattr('server.auth.chatgpt_mcp.get_redis_client_async', lambda: redis)
    mount_chatgpt_mcp(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://app.example.com'
    ) as client:
        assert (
            await client.get('/.well-known/oauth-authorization-server/chatgpt')
        ).status_code == 200
        assert (await client.post('/chatgpt/mcp', json={})).status_code == 401
        assert (await client.get('/mcp/mcp')).status_code == 404


async def test_discovery_and_private_resource(oauth_app):
    app, provider = oauth_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='https://app.example.com'
    ) as client:
        metadata = await client.get('/.well-known/oauth-authorization-server/chatgpt')
        assert metadata.status_code == 200
        assert metadata.json()['issuer'] == BASE_URL
        assert metadata.json()['code_challenge_methods_supported'] == ['S256']
        assert (
            'client_secret_post'
            in metadata.json()['token_endpoint_auth_methods_supported']
        )
        resource = await client.get('/.well-known/oauth-protected-resource/chatgpt/mcp')
        assert resource.json()['resource'] == provider.resource
        assert resource.json()['authorization_servers'] == [BASE_URL]
        for headers in ({}, {'Authorization': 'Bearer api-key-not-a-cloud-grant'}):
            denied = await client.post('/chatgpt/mcp', headers=headers, json={})
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
            {'client_id': 'unknown-chatgpt-client'},
            {'redirect_uri': 'https://attacker.example/collect'},
            {'resource': 'https://app.example.com/mcp/mcp'},
            {'scope': 'tasks:write'},
            {'code_challenge_method': 'plain'},
        ):
            response = await client.get(
                '/chatgpt/authorize', params={**params, **invalid}
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
        valid = await client.get('/chatgpt/authorize', params=params)
        assert valid.status_code in (302, 303, 307)
        consent = await client.get(valid.headers['location'])
        assert consent.status_code in (302, 307)
        login_url = urlsplit(consent.headers['location'])
        assert login_url.path == '/login'
        assert '/chatgpt/consent?request=' in parse_qs(login_url.query)['returnTo'][0]
        denied = await client.post(
            '/chatgpt/token',
            data={
                'grant_type': 'authorization_code',
                'client_id': CLIENT_ID,
                'code': 'none',
            },
        )
        assert denied.status_code == 401
        denied = await client.post(
            '/chatgpt/token',
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
            '/chatgpt/authorize',
            params={
                'client_id': CLIENT_ID,
                'redirect_uri': REDIRECT_URI,
                'response_type': 'code',
                'scope': 'tasks:read',
                'state': 'independent-chatgpt-state',
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
                'resource': provider.resource,
            },
        )
        assert authorization.status_code in (302, 303, 307)
        consent_url = authorization.headers['location']
        consent = await client.get(consent_url)
        assert consent.status_code == 200
        assert 'Allow ChatGPT' in consent.text
        assert consent.headers['cache-control'] == 'no-store'
        handle = parse_qs(urlsplit(consent_url).query)['request'][0]
        csrf = re.search(r'name="csrf" value="([^"]+)"', consent.text).group(1)
        wrong = await client.post(
            '/chatgpt/consent',
            data={'handle': handle, 'csrf': 'wrong', 'decision': 'allow'},
        )
        assert wrong.status_code == 403
        consent = await client.get(consent_url)
        csrf = re.search(r'name="csrf" value="([^"]+)"', consent.text).group(1)
        allowed = await client.post(
            '/chatgpt/consent',
            data={'handle': handle, 'csrf': csrf, 'decision': 'allow'},
        )
        assert allowed.status_code == 303
        callback = urlsplit(allowed.headers['location'])
        assert f'{callback.scheme}://{callback.netloc}{callback.path}' == REDIRECT_URI
        assert parse_qs(callback.query)['state'] == ['independent-chatgpt-state']
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
            '/chatgpt/token', data={**form, 'code_verifier': 'a' * 43}
        )
        assert incorrect.status_code in (400, 401), incorrect.text
        assert incorrect.json()['error'] == 'invalid_grant'
        issued = await client.post('/chatgpt/token', data=form)
        assert issued.status_code == 200, issued.text
        tokens = issued.json()
        assert tokens['scope'] == 'tasks:read'
        assert (
            await provider.load_access_token(tokens['access_token'])
        ).subject == str(user.id)
        replay = await client.post('/chatgpt/token', data=form)
        assert replay.status_code in (400, 401)
        assert replay.json()['error'] == 'invalid_grant'
        refresh_form = {
            'grant_type': 'refresh_token',
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'refresh_token': tokens['refresh_token'],
            'resource': provider.resource,
        }
        rotated = await client.post('/chatgpt/token', data=refresh_form)
        assert rotated.status_code == 200, rotated.text
        reused_refresh = await client.post('/chatgpt/token', data=refresh_form)
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
            '/chatgpt/revoke',
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
                '/chatgpt/mcp',
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
                '/chatgpt/mcp',
                headers={
                    'Authorization': f'Bearer {tokens.access_token}',
                    'Accept': 'application/json, text/event-stream',
                },
                json={'jsonrpc': '2.0', 'method': 'tools/list', 'id': 2, 'params': {}},
            )
            assert listed.status_code == 200, listed.text
            tools = listed.json()['result']['tools']
            assert [tool['name'] for tool in tools] == ['list_my_tasks']
            assert tools[0]['annotations']['readOnlyHint'] is True
