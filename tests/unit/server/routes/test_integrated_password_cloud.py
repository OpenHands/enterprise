"""Hosted password login: real PostgreSQL accounts, Redis state and MCP consent."""

import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI, Request
from mcp.server.auth.provider import AuthorizationParams
from sqlalchemy import select

from openhands.app_server.shared import server_config
from openhands.app_server.user_auth.user_auth import get_user_auth
from server.auth.external_mcp_oauth import ExternalMcpOAuth, RegisteredClient
from server.auth.password_hashing import hash_password, verify_password
from server.middleware import SetAuthCookieMiddleware
from server.routes import idp
from server.routes import integrated_password_security as security
from server.routes.auth import api_router
from server.routes.external_mcp_consent import create_consent_router
from server.routes.oauth_v2 import oauth_v2_router
from server.services.feature_flag_service import feature_flag_service
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import PasswordProviderDetails
from storage.auth_provider_user import AuthProviderUser
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User
from tests.unit.server.routes import test_reviewer_login as reviewer_tests

cloud_redis = reviewer_tests.redis
redis_container = reviewer_tests.redis_container

ORIGIN = 'https://app.example.com'
EMAIL = 'review@example.com'
PASSWORD = 'review-test-password'
PATH = '/oauth/idp/login'


@pytest.fixture
async def setup(
    cloud_redis,
    create_user,
    session_maker,
    oauth_v2_stores,
    async_session_maker,
    app_db_session,
    monkeypatch,
):
    redis = cloud_redis
    monkeypatch.setattr('storage.redis.get_redis_client_async', lambda: redis)
    user = create_user(
        email=EMAIL, email_verified=True, accepted_tos=datetime.now(timezone.utc)
    )
    with session_maker() as session:
        provider = AuthProvider(
            title='Integrated Password',
            is_idp=True,
            enabled=True,
            details=PasswordProviderDetails(),
        )
        role = Role(name='member', rank=3)
        session.add_all([provider, role])
        session.flush()
        row = session.get(User, user.id)
        row.password_hash = hash_password(PASSWORD)
        row.role_id = None
        session.add(
            OrgMember(
                org_id=user.current_org_id,
                user_id=user.id,
                role_id=role.id,
                status='active',
                llm_api_key='test-only',
            )
        )
        session.commit()
        session.refresh(provider)
        session.expunge(provider)
    monkeypatch.setattr(idp, 'DEPLOYMENT_MODE', 'cloud')
    monkeypatch.setattr(idp, 'get_web_url', lambda request: ORIGIN)
    monkeypatch.setattr(security, 'get_web_url', lambda request: ORIGIN)
    monkeypatch.setattr(security, 'get_redis_client_async', lambda: redis)
    monkeypatch.setattr(idp, '_track_idp_login', AsyncMock())
    monkeypatch.setattr('storage.org_member_store.a_session_maker', async_session_maker)
    monkeypatch.setattr(
        server_config, 'user_auth_class', 'server.auth.saas_user_auth.SaasUserAuth'
    )
    monkeypatch.setenv('ENABLE_EXTERNAL_MCP', 'true')
    feature_flag_service.invalidate()
    mcp = ExternalMcpOAuth(
        base_url=ORIGIN + '/external-mcp',
        redis=redis,
        clients={
            'chatgpt-review': RegisteredClient(
                client_id='chatgpt-review',
                name='ChatGPT',
                redirect_uris=('https://chatgpt.example/callback',),
                scopes=('tasks:read', 'tasks:write'),
            ),
        },
    )
    app = FastAPI()
    app.include_router(idp.idp_router)
    app.include_router(idp.idp_invite_router)
    app.include_router(oauth_v2_router)
    app.include_router(api_router)
    app.include_router(create_consent_router(mcp))

    @app.middleware('http')
    async def auth_cookies(request, call_next):
        return await SetAuthCookieMiddleware()(request, call_next)

    @app.get('/probe')
    async def probe(request: Request):
        auth = await get_user_auth(request)
        return {
            'user_id': await auth.get_user_id(),
            'git': list((await auth.get_provider_tokens()) or {}),
        }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN, follow_redirects=False
    ) as client:
        yield SimpleNamespace(
            client=client,
            app=app,
            user=user,
            provider=provider,
            session_maker=session_maker,
            mcp=mcp,
            redis=redis,
        )


async def form(setup, destination='/'):
    response = await setup.client.get(PATH, params={'redirect_url': destination})
    assert response.status_code == 200
    assert 'Create Admin' not in response.text
    assert response.headers['cache-control'] == 'no-store'
    return re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)


async def sign_in(setup, *, email=EMAIL, password=PASSWORD, destination='/'):
    csrf = await form(setup, destination)
    return await setup.client.post(
        PATH,
        data={'email': email, 'password': password, 'csrf': csrf},
        headers={'Origin': ORIGIN},
    )


async def test_member_login_without_password_admin(setup):
    response = await sign_in(setup, destination='/external-mcp/consent?request=opaque')
    assert response.status_code == 302
    assert (
        response.headers['location'] == ORIGIN + '/external-mcp/consent?request=opaque'
    )
    assert 'HttpOnly' in response.headers['set-cookie']
    assert (await setup.client.post('/api/authenticate')).status_code == 200
    assert (await setup.client.get('/probe')).json() == {
        'user_id': str(setup.user.id),
        'git': [],
    }
    with setup.session_maker() as session:
        assert session.get(User, setup.user.id).role_id is None
        link = session.scalar(
            select(AuthProviderUser).where(
                AuthProviderUser.oauth_provider_id == setup.provider.id
            )
        )
        assert link.user_id == setup.user.id


@pytest.mark.parametrize('method', ['get', 'post'])
async def test_bootstrap_unavailable_even_without_password_admin(setup, method):
    kwargs = (
        {'data': {'email': EMAIL, 'password': PASSWORD, 'confirm_password': PASSWORD}}
        if method == 'post'
        else {}
    )
    response = await getattr(setup.client, method)('/oauth/idp/signup', **kwargs)
    assert response.status_code == 404
    with setup.session_maker() as session:
        assert session.get(User, setup.user.id).role_id is None


@pytest.mark.parametrize(
    'failure',
    ['unknown', 'wrong', 'disabled', 'deleted', 'service', 'unverified', 'no_password'],
)
async def test_invalid_accounts_never_signup_or_create_session(setup, failure):
    with setup.session_maker() as session:
        user = session.get(User, setup.user.id)
        if failure == 'disabled':
            user.is_disabled = True
        if failure == 'deleted':
            user.deletion_pending = True
        if failure == 'service':
            user.account_type = 'service'
        if failure == 'unverified':
            user.email_verified = False
        if failure == 'no_password':
            user.password_hash = None
        session.commit()
    response = await sign_in(
        setup,
        email='unknown@example.com' if failure == 'unknown' else EMAIL,
        password='incorrect' if failure == 'wrong' else PASSWORD,
    )
    assert response.status_code == 302
    assert 'invalid_credentials' in response.headers['location']
    assert 'signup' not in response.headers['location']
    assert 'openhands_auth' not in setup.client.cookies


@pytest.mark.parametrize('origin', [None, 'https://evil.example'])
async def test_csrf_origin_required(setup, origin):
    csrf = await form(setup)
    response = await setup.client.post(
        PATH,
        data={'email': EMAIL, 'password': PASSWORD, 'csrf': csrf},
        headers={'Origin': origin} if origin else {},
    )
    assert response.status_code == 403


async def test_csrf_single_use_and_destination_bound(setup):
    csrf = await form(setup, '/settings')
    data = {
        'email': EMAIL,
        'password': PASSWORD,
        'csrf': csrf,
        'redirect_url': 'https://evil.example',
    }
    response = await setup.client.post(PATH, data=data, headers={'Origin': ORIGIN})
    assert response.headers['location'] == '/settings'
    setup.client.cookies.set(security.COOKIE, csrf, domain='app.example.com', path='/')
    assert (
        await setup.client.post(PATH, data=data, headers={'Origin': ORIGIN})
    ).status_code == 403


@pytest.mark.parametrize(
    'destination',
    [
        'https://evil.example/x',
        '//evil.example',
        '/\\evil.example',
        'javascript:alert(1)',
    ],
)
async def test_external_redirect_rejected(setup, destination):
    response = await sign_in(setup, destination=destination)
    assert response.headers['location'] == '/'


async def test_tos_preserves_mcp_request(setup):
    with setup.session_maker() as session:
        session.get(User, setup.user.id).accepted_tos = None
        session.commit()
    response = await sign_in(setup, destination='/external-mcp/consent?request=opaque')
    url = httpx.URL(response.headers['location'])
    assert url.path == '/accept-tos'
    assert parse_qs(url.query.decode())['redirect_url'] == [
        '/external-mcp/consent?request=opaque'
    ]


async def test_throttled_by_email(setup):
    for _ in range(5):
        assert (await sign_in(setup, password='incorrect')).status_code == 302
    assert (await sign_in(setup)).status_code == 429


async def test_redis_failure_closed(setup, monkeypatch):
    monkeypatch.setattr(
        security,
        'get_redis_client_async',
        lambda: SimpleNamespace(eval=AsyncMock(side_effect=RuntimeError())),
    )
    assert (await setup.client.get(PATH)).status_code == 503


async def test_body_size_limited(setup):
    response = await setup.client.post(
        PATH,
        content=b'x' * 9000,
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Origin': ORIGIN},
    )
    assert response.status_code == 413


async def test_real_mcp_consent_after_integrated_login(setup):
    client = await setup.mcp.get_client('chatgpt-review')
    consent = await setup.mcp.authorize(
        client,
        AuthorizationParams(
            redirect_uri='https://chatgpt.example/callback',
            redirect_uri_provided_explicitly=True,
            code_challenge='a' * 43,
            state='test-state',
            scopes=['tasks:read', 'tasks:write'],
            resource=ORIGIN + '/external-mcp/mcp',
        ),
    )
    response = await sign_in(setup, destination=consent)
    page = await setup.client.get(response.headers['location'])
    assert page.status_code == 200 and 'Connect ChatGPT to OpenHands?' in page.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    handle = parse_qs(httpx.URL(consent).query.decode())['request'][0]
    response = await setup.client.post(
        '/external-mcp/consent',
        data={'handle': handle, 'csrf': csrf, 'decision': 'allow'},
    )
    assert response.status_code == 303
    assert response.headers['location'].startswith(
        'https://chatgpt.example/callback?code='
    )


async def test_admin_setup_existing_member_and_single_use(setup, monkeypatch):
    # Exercise the existing permission-gated provisioning API rather than any
    # public bootstrap. Recipient already belongs to the dedicated demo org.
    from openhands.app_server.user_auth import get_user_id

    assert (await sign_in(setup)).status_code == 302
    monkeypatch.setattr(idp, 'authorize_permission', AsyncMock())
    setup.app.dependency_overrides[get_user_id] = lambda: str(setup.user.id)
    response = await setup.client.post(
        '/api/idp/signup-links',
        json={
            'email': EMAIL,
            'role': 'member',
            'org_id': str(setup.user.current_org_id),
        },
    )
    assert response.status_code == 201
    token = parse_qs(httpx.URL(response.json()['url']).query.decode())['token'][0]
    data = {
        'token': token,
        'password': 'updated-review-password',
        'confirm_password': 'updated-review-password',
    }
    assert (await setup.client.post('/oauth/idp/invite', data=data)).status_code == 302
    assert (await setup.client.post('/oauth/idp/invite', data=data)).status_code == 400
    with setup.session_maker() as session:
        user = session.get(User, setup.user.id)
        assert user.role_id is None
        assert verify_password('updated-review-password', user.password_hash)
    setup.client.cookies.clear()
    assert (await sign_in(setup, password='updated-review-password')).status_code == 302


async def test_expired_or_mismatched_csrf(setup):
    csrf = await form(setup)
    data = {'email': EMAIL, 'password': PASSWORD, 'csrf': 'wrong'}
    assert (
        await setup.client.post(PATH, data=data, headers={'Origin': ORIGIN})
    ).status_code == 403
    await setup.redis.delete(f'integrated-password:state:{security.digest(csrf)}')
    data['csrf'] = csrf
    assert (
        await setup.client.post(PATH, data=data, headers={'Origin': ORIGIN})
    ).status_code == 403


async def test_disabled_provider_hides_login(setup):
    with setup.session_maker() as session:
        session.get(AuthProvider, setup.provider.id).enabled = False
        session.commit()
    assert (await setup.client.get(PATH)).status_code == 404
    assert (
        await setup.client.post(PATH, data={'email': EMAIL, 'password': PASSWORD})
    ).status_code == 404


async def test_throttled_by_ip_even_with_different_emails(setup):
    for _ in range(30):
        assert (await setup.client.get(PATH)).status_code == 200
    assert (await setup.client.get(PATH)).status_code == 429


async def test_form_escapes_return_destination(setup):
    value = '/?q="><script>alert(1)</script>'
    response = await setup.client.get(PATH, params={'redirect_url': value})
    assert '<script>' not in response.text
    assert '&lt;script&gt;' in response.text


async def test_newly_issued_setup_links_are_distinct(setup):
    first = idp._create_signup_link_token(EMAIL)
    second = idp._create_signup_link_token(EMAIL)
    assert first != second
    await security.consume_invitation(first)
    await security.consume_invitation(second)
