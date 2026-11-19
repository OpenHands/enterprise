"""Reviewer login with real PostgreSQL/Redis and an isolated mock OIDC server."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from mcp.server.auth.provider import AuthorizationParams
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select
from testcontainers.community.redis import RedisContainer

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.shared import server_config
from openhands.app_server.user_auth.user_auth import get_user_auth
from server.auth.external_mcp_oauth import ExternalMcpOAuth, RegisteredClient
from server.middleware import SetAuthCookieMiddleware
from server.routes import reviewer_login as login
from server.routes.auth import api_router
from server.routes.external_mcp_consent import create_consent_router
from server.routes.oauth_v2 import oauth_v2_router
from server.services.feature_flag_service import feature_flag_service
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import OAuthProviderDetails
from storage.auth_provider_user import AuthProviderUser
from storage.oauth_token import OAuthToken
from storage.org_member import OrgMember
from storage.role import Role

ORIGIN = 'https://app.example.com'
EMAIL = 'review@example.com'
PASSWORD = 'test-review-password'


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
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def setup(
    redis,
    create_user,
    session_maker,
    oauth_v2_stores,
    async_session_maker,
    app_db_session,
    monkeypatch,
):
    user = create_user(
        email=EMAIL, email_verified=True, accepted_tos=datetime.now(timezone.utc)
    )
    with session_maker() as session:
        role = Role(name='member', rank=3)
        provider = AuthProvider(
            title='Review credentials',
            is_idp=False,
            enabled=True,
            allow_registration=False,
            allow_match_by_email=False,
            details=OAuthProviderDetails(
                provider_type=ProviderType.ENTERPRISE_SSO,
                client_id='review-client',
                client_secret=SecretStr('test-client-secret'),
                token_url='https://idp.example.com/token',
                userinfo_url='https://idp.example.com/userinfo',
            ),
        )
        session.add_all([role, provider])
        session.flush()
        session.add(
            OrgMember(
                org_id=user.current_org_id,
                user_id=user.id,
                role_id=role.id,
                status='active',
                llm_api_key='test-only',
            )
        )
        session.add(
            AuthProviderUser(
                user_id=user.id,
                oauth_provider_id=provider.id,
                external_subject_id='review-subject',
                external_email=EMAIL,
            )
        )
        session.commit()
        session.refresh(provider)
        session.expunge(provider)
    monkeypatch.setenv('ENABLE_REVIEWER_LOGIN', 'true')
    monkeypatch.setenv('REVIEWER_LOGIN_PROVIDER_ID', str(provider.id))
    monkeypatch.setenv(
        'REVIEWER_LOGIN_ACCOUNTS',
        json.dumps(
            {EMAIL: {'user_id': str(user.id), 'org_id': str(user.current_org_id)}}
        ),
    )
    monkeypatch.setattr(login, 'get_redis_client_async', lambda: redis)
    monkeypatch.setattr(login, 'get_web_url', lambda request: ORIGIN)
    monkeypatch.setattr('storage.org_member_store.a_session_maker', async_session_maker)
    monkeypatch.setattr(
        server_config, 'user_auth_class', 'server.auth.saas_user_auth.SaasUserAuth'
    )
    calls = []
    responses = {}
    claims = {'sub': 'review-subject', 'email': EMAIL, 'email_verified': True}

    def respond(request):
        calls.append(request)
        if request.url.path in responses:
            status, body = responses[request.url.path]
            return httpx.Response(status, json=body)
        if request.url.path == '/userinfo':
            return httpx.Response(200, json=claims)
        body = parse_qs(request.content.decode())
        if body.get('grant_type') == ['password'] and body.get('password') != [
            PASSWORD
        ]:
            return httpx.Response(400, json={'error': 'invalid_grant'})
        return httpx.Response(
            200,
            json={
                'access_token': 'idp-access',
                'refresh_token': 'idp-refresh',
                'expires_in': 300,
                'refresh_expires_in': 3600,
            },
        )

    real_client = httpx.AsyncClient

    def factory(**kwargs):
        return real_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(
        login, 'httpx', SimpleNamespace(AsyncClient=factory, HTTPError=httpx.HTTPError)
    )
    monkeypatch.setattr(
        'server.auth.oauth_v2_refresh.httpx',
        SimpleNamespace(AsyncClient=factory, HTTPError=httpx.HTTPError),
    )
    monkeypatch.setenv('ENABLE_EXTERNAL_MCP', 'true')
    feature_flag_service.invalidate()
    mcp_provider = ExternalMcpOAuth(
        base_url=ORIGIN + '/external-mcp',
        redis=redis,
        clients={
            'chatgpt-review': RegisteredClient(
                client_id='chatgpt-review',
                name='ChatGPT',
                redirect_uris=('https://chatgpt.example/callback',),
                scopes=('tasks:read', 'tasks:write'),
            )
        },
    )
    app = FastAPI()
    app.include_router(login.router)
    app.include_router(oauth_v2_router)
    app.include_router(api_router)
    app.include_router(create_consent_router(mcp_provider))

    @app.middleware('http')
    async def auth_cookies(request, call_next):
        return await SetAuthCookieMiddleware()(request, call_next)

    @app.get('/probe')
    async def probe(request: login.Request):
        auth = await get_user_auth(request)
        return {
            'user_id': await auth.get_user_id(),
            'git': list((await auth.get_provider_tokens()) or {}),
        }

    async with real_client(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN, follow_redirects=False
    ) as client:
        yield SimpleNamespace(
            mcp=mcp_provider,
            responses=responses,
            client=client,
            app=app,
            provider=provider,
            user=user,
            calls=calls,
            claims=claims,
            session_maker=session_maker,
        )


async def sign_in(setup, *, password=PASSWORD, email=EMAIL, return_to='/'):
    page = await setup.client.get(login.LOGIN_PATH, params={'returnTo': return_to})
    assert page.status_code == 200
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    return await setup.client.post(
        login.LOGIN_PATH,
        data={'csrf': csrf, 'email': email, 'password': password},
        headers={'Origin': ORIGIN},
    )


async def test_normal_session_without_git_and_request_preserved(
    setup, async_session_maker
):
    destination = '/external-mcp/consent?request=opaque_handle'
    response = await sign_in(setup, return_to=destination)
    assert response.status_code == 303
    assert response.headers['location'] == ORIGIN + destination
    cookie = response.headers['set-cookie']
    assert 'openhands_auth=' in cookie and 'HttpOnly' in cookie and 'Secure' in cookie
    assert (await setup.client.post('/api/authenticate')).status_code == 200
    probe = await setup.client.get('/probe')
    assert probe.json() == {'user_id': str(setup.user.id), 'git': []}
    async with async_session_maker() as session:
        tokens = (
            (
                await session.execute(
                    select(OAuthToken).where(OAuthToken.user_id == setup.user.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(tokens) == 1 and tokens[0].oauth_provider_id == setup.provider.id
    payload = parse_qs(setup.calls[0].content.decode())
    assert payload['grant_type'] == ['password']
    assert payload['client_secret'] == ['test-client-secret']


async def test_real_mcp_consent_with_no_git(setup):
    client = await setup.mcp.get_client('chatgpt-review')
    consent = await setup.mcp.authorize(
        client,
        AuthorizationParams(
            redirect_uri='https://chatgpt.example/callback',
            redirect_uri_provided_explicitly=True,
            code_challenge='a' * 43,
            state='review-state',
            scopes=['tasks:read', 'tasks:write'],
            resource=ORIGIN + '/external-mcp/mcp',
        ),
    )
    response = await sign_in(setup, return_to=consent.removeprefix(ORIGIN))
    page = await setup.client.get(response.headers['location'])
    assert page.status_code == 200 and 'Connect ChatGPT to OpenHands?' in page.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    handle = parse_qs(httpx.URL(consent).query.decode())['request'][0]
    approved = await setup.client.post(
        '/external-mcp/consent',
        data={'handle': handle, 'csrf': csrf, 'decision': 'allow'},
    )
    assert approved.status_code == 303
    assert approved.headers['location'].startswith(
        'https://chatgpt.example/callback?code='
    )


async def test_standard_session_refresh_without_git(setup, async_session_maker):
    assert (await sign_in(setup)).status_code == 303
    async with async_session_maker() as session:
        token = (
            await session.execute(
                select(OAuthToken).where(OAuthToken.user_id == setup.user.id)
            )
        ).scalar_one()
        token.access_token_expires_at = datetime.now(timezone.utc) - timedelta(
            minutes=1
        )
        await session.commit()
    response = await setup.client.post('/api/authenticate')
    assert response.status_code == 200
    assert 'openhands_auth=' in response.headers['set-cookie']
    assert any(
        parse_qs(req.content.decode()).get('grant_type') == ['refresh_token']
        for req in setup.calls
        if req.url.path == '/token'
    )
    assert (await setup.client.get('/probe')).json()['git'] == []


async def test_login_does_not_offer_review_provider_publicly(setup):
    response = await setup.client.get('/oauth/idp-providers')
    assert response.status_code == 200
    assert 'Review credentials' not in response.text


@pytest.mark.parametrize(
    'email,password', [(EMAIL, 'incorrect'), ('other@example.com', PASSWORD)]
)
async def test_invalid_and_unlisted_credentials_have_same_error(setup, email, password):
    response = await sign_in(setup, email=email, password=password)
    assert response.status_code == 401
    assert 'Unable to sign in with those credentials.' in response.text
    assert (
        PASSWORD not in response.text
        and 'openhands_auth=' not in response.headers.get('set-cookie', '')
    )
    if email != EMAIL:
        assert not setup.calls


@pytest.mark.parametrize(
    'change',
    [
        'disabled',
        'deleted',
        'superadmin',
        'unlinked',
        'org',
        'member',
        'subject',
        'unverified',
        'user_unverified',
    ],
)
async def test_rejects_ineligible_identity(setup, change, create_org):
    other_org = create_org() if change == 'org' else None
    with setup.session_maker() as session:
        user = session.get(type(setup.user), setup.user.id)
        if change == 'disabled':
            user.is_disabled = True
        elif change == 'deleted':
            user.deletion_pending = True
        elif change == 'superadmin':
            role = Role(name='superadmin', rank=0)
            session.add(role)
            session.flush()
            user.role_id = role.id
        elif change == 'unlinked':
            link = session.execute(
                select(AuthProviderUser).where(AuthProviderUser.user_id == user.id)
            ).scalar_one()
            session.delete(link)
        elif change == 'org':
            user.current_org_id = other_org.id
        elif change == 'member':
            session.get(OrgMember, (user.current_org_id, user.id)).status = 'inactive'
        elif change == 'subject':
            setup.claims['sub'] = 'unlinked-subject'
        elif change == 'unverified':
            setup.claims['email_verified'] = False
        elif change == 'user_unverified':
            user.email_verified = False
        session.commit()
    response = await sign_in(setup)
    assert response.status_code == 401
    assert 'openhands_auth=' not in response.headers.get('set-cookie', '')


async def test_csrf_origin_expiry_and_replay(setup, redis):
    page = await setup.client.get(login.LOGIN_PATH)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    data = {'csrf': csrf, 'email': EMAIL, 'password': PASSWORD}
    assert (
        await setup.client.post(
            login.LOGIN_PATH, data=data, headers={'Origin': 'https://evil.example'}
        )
    ).status_code == 403
    assert (
        await setup.client.post(
            login.LOGIN_PATH, data={**data, 'csrf': 'wrong'}, headers={'Origin': ORIGIN}
        )
    ).status_code == 403
    assert not setup.calls
    key = f'reviewer-login:state:{hashlib.sha256(csrf.encode()).hexdigest()}'
    await redis.delete(key)
    assert (
        await setup.client.post(login.LOGIN_PATH, data=data, headers={'Origin': ORIGIN})
    ).status_code == 403
    assert (await sign_in(setup)).status_code == 303
    assert (
        await setup.client.post(login.LOGIN_PATH, data=data, headers={'Origin': ORIGIN})
    ).status_code == 403


async def test_per_account_throttling(setup):
    for _ in range(5):
        assert (await sign_in(setup, password='wrong')).status_code == 401
    response = await sign_in(setup)
    assert response.status_code == 429 and response.headers['retry-after'] == '300'
    assert len(setup.calls) == 5


async def test_successful_form_cannot_be_replayed(setup):
    page = await setup.client.get(login.LOGIN_PATH)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    data = {'csrf': csrf, 'email': EMAIL, 'password': PASSWORD}
    headers = {'Origin': ORIGIN, 'Cookie': f'__Host-openhands-reviewer={csrf}'}
    assert (
        await setup.client.post(login.LOGIN_PATH, data=data, headers=headers)
    ).status_code == 303
    calls = len(setup.calls)
    assert (
        await setup.client.post(login.LOGIN_PATH, data=data, headers=headers)
    ).status_code == 403
    assert len(setup.calls) == calls


@pytest.mark.parametrize(
    'field,value',
    [
        ('is_idp', True),
        ('enabled', False),
        ('allow_registration', True),
        ('allow_match_by_email', True),
        ('client_secret', None),
        ('token_url', 'http://idp.example.com/token'),
        ('legacy_keycloak_type', ProviderType.GITHUB),
    ],
)
async def test_unsuitable_provider_is_never_usable(setup, field, value):
    with setup.session_maker() as session:
        provider = session.get(AuthProvider, setup.provider.id)
        if field in {'is_idp', 'enabled', 'allow_registration', 'allow_match_by_email'}:
            setattr(provider, field, value)
        else:
            details = provider.details.model_dump()
            details[field] = value
            provider.details = OAuthProviderDetails(**details)
        session.commit()
    assert (await setup.client.get(login.LOGIN_PATH)).status_code == 404


@pytest.mark.parametrize('path,status', [('/token', 500), ('/userinfo', 401)])
async def test_upstream_failure_does_not_expose_secrets(setup, path, status, caplog):
    setup.responses[path] = (
        status,
        {'error_description': PASSWORD, 'access_token': 'do-not-expose'},
    )
    response = await sign_in(setup)
    assert response.status_code == 503
    assert PASSWORD not in response.text and 'do-not-expose' not in response.text
    assert PASSWORD not in caplog.text and 'do-not-expose' not in caplog.text


async def test_disabled_reviewer_loses_existing_normal_session(setup):
    assert (await sign_in(setup)).status_code == 303
    with setup.session_maker() as session:
        session.get(type(setup.user), setup.user.id).is_disabled = True
        session.commit()
    assert (await setup.client.post('/api/authenticate')).status_code == 401


async def test_form_body_limits_and_duplicate_fields(setup):
    headers = {'Origin': ORIGIN, 'Content-Type': 'application/x-www-form-urlencoded'}
    response = await setup.client.post(
        login.LOGIN_PATH, content=b'x' * 8193, headers=headers
    )
    assert response.status_code == 413
    response = await setup.client.post(
        login.LOGIN_PATH,
        content='csrf=x&email=x&password=x&password=y',
        headers=headers,
    )
    assert response.status_code == 400
    assert not setup.calls


async def test_redis_failure_cannot_disable_protections(setup, monkeypatch):
    monkeypatch.setattr(
        login,
        'get_redis_client_async',
        lambda: SimpleNamespace(
            eval=AsyncMock(side_effect=RuntimeError('unavailable'))
        ),
    )
    assert (await setup.client.get(login.LOGIN_PATH)).status_code == 503
    assert not setup.calls


async def test_tos_is_not_bypassed(setup):
    with setup.session_maker() as session:
        session.get(type(setup.user), setup.user.id).accepted_tos = None
        session.commit()
    response = await sign_in(setup, return_to='/external-mcp/consent?request=original')
    assert response.status_code == 303
    assert '/accept-tos?redirect_url=' in response.headers['location']
    assert 'request%3Doriginal' in response.headers['location']


@pytest.mark.parametrize(
    'target',
    [
        '//evil.example',
        '/\\evil.example',
        'https://evil.example',
        '/oauth/reviewer/login',
        '/\nevil.example',
    ],
)
async def test_off_origin_and_recursive_redirects_rejected(setup, target):
    response = await sign_in(setup, return_to=target)
    assert response.status_code == 303 and response.headers['location'] == '/'


async def test_disabled_and_misconfigured_route_is_404(setup, monkeypatch):
    monkeypatch.delenv('ENABLE_REVIEWER_LOGIN')
    assert (await setup.client.get(login.LOGIN_PATH)).status_code == 404
    assert (await setup.client.post(login.LOGIN_PATH)).status_code == 404
    monkeypatch.setenv('ENABLE_REVIEWER_LOGIN', 'true')
    monkeypatch.setenv('REVIEWER_LOGIN_ACCOUNTS', '{}')
    assert (await setup.client.get(login.LOGIN_PATH)).status_code == 404


async def test_form_security_headers_and_style_hash(setup):
    response = await setup.client.get(login.LOGIN_PATH)
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-robots-tag'] == 'noindex, nofollow'
    import base64

    css = re.search(r'<style>(.*?)</style>', response.text, re.S).group(1)
    digest = base64.b64encode(hashlib.sha256(css.encode()).digest()).decode()
    assert f"style-src 'sha256-{digest}'" in response.headers['content-security-policy']
    assert 'current-password' in response.text
