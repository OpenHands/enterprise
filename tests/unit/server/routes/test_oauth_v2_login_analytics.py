import time
from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import select

from openhands.analytics import AnalyticsService
from openhands.app_server.integrations.service_types import ProviderType
from openhands.server.types import AppMode
from server.routes import oauth_v2
from storage.auth_provider_user_store import AuthProviderUserStore
from storage.oauth_token import OAuthToken


@pytest.fixture
async def login_setup(
    create_user, create_git_provider, async_session_maker, monkeypatch
):
    user = create_user(email='login@example.com', accepted_tos=datetime(2026, 1, 1))
    provider = await create_git_provider(ProviderType.GITHUB, is_idp=True)
    async with async_session_maker() as session:
        provider.enabled = True
        session.add(provider)
        await session.commit()
    await AuthProviderUserStore().link(
        oauth_provider_id=provider.id,
        user_id=user.id,
        external_subject_id='external-user',
    )
    monkeypatch.setattr('storage.org_store.a_session_maker', async_session_maker)
    monkeypatch.setattr('storage.org_member_store.a_session_maker', async_session_maker)
    application = FastAPI()
    application.include_router(oauth_v2.oauth_v2_router)
    return application, user, provider


@pytest.fixture
def analytics_client(monkeypatch):
    service = AnalyticsService(
        api_key='',
        host='https://posthog.example.com',
        app_mode=AppMode.SAAS,
        is_feature_env=False,
    )
    monkeypatch.setattr('openhands.analytics._analytics_service', service)
    # Only the external PostHog boundary is replaced; consent and capture run normally.
    with (
        patch.object(service._client, 'capture') as capture,
        patch.object(service._client, 'set') as identify,
    ):
        yield capture, identify
    service.shutdown()


async def _callback(application, provider, *, mode='login', user_id=None):
    state = oauth_v2._encrypt_state(
        {
            'redirect_url': '/done',
            'mode': mode,
            'user_id': user_id,
            'nonce': 'nonce',
            'issued_at': time.time(),
        }
    )
    token = {'access_token': 'access', 'refresh_token': 'refresh', 'expires_in': 3600}
    userinfo = {'sub': 'external-user', 'email': 'login@example.com'}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url='http://testserver'
    ) as client:
        client.cookies.set(oauth_v2.LINK_NONCE_COOKIE, 'nonce')
        with (
            patch.object(oauth_v2, '_exchange_code', AsyncMock(return_value=token)),
            patch.object(oauth_v2, '_fetch_userinfo', AsyncMock(return_value=userinfo)),
        ):
            return await client.get(
                f'/oauth/{provider.id}/callback',
                params={'code': 'code', 'state': state},
            )


@pytest.mark.asyncio
async def test_successful_login_captures_event_and_identifies_user(
    login_setup, analytics_client, async_session_maker
):
    application, user, provider = login_setup
    capture, identify = analytics_client
    response = await _callback(application, provider)

    assert response.status_code == 302
    assert response.headers['location'] == '/done'
    assert 'openhands_auth' in response.cookies
    capture.assert_called_once()
    assert capture.call_args.kwargs['distinct_id'] == str(user.id)
    assert capture.call_args.kwargs['event'] == 'user logged in'
    assert capture.call_args.kwargs['properties']['idp'] == 'github'
    assert capture.call_args.kwargs['properties']['org_id'] == str(user.current_org_id)
    identify.assert_called_once()
    assert identify.call_args.kwargs['properties']['email'] == user.email
    assert identify.call_args.kwargs['properties']['idp'] == 'github'
    async with async_session_maker() as session:
        token = await session.scalar(
            select(OAuthToken).where(
                OAuthToken.user_id == user.id,
                OAuthToken.oauth_provider_id == provider.id,
            )
        )
        assert token is not None


@pytest.mark.asyncio
async def test_login_without_consent_does_not_emit_analytics(
    login_setup, analytics_client, async_session_maker
):
    application, user, provider = login_setup
    async with async_session_maker() as session:
        user.accepted_tos = None
        session.add(user)
        await session.commit()
    capture, identify = analytics_client
    response = await _callback(application, provider)

    assert response.status_code == 302
    assert '/accept-tos?' in response.headers['location']
    assert 'openhands_auth' in response.cookies
    capture.assert_not_called()
    identify.assert_not_called()


@pytest.mark.asyncio
async def test_link_does_not_emit_login_analytics(login_setup, analytics_client):
    application, user, provider = login_setup
    capture, identify = analytics_client
    with patch.object(oauth_v2, 'invalidate_user_id_cache', AsyncMock()):
        response = await _callback(
            application, provider, mode='link', user_id=str(user.id)
        )
    assert response.status_code == 302
    assert 'openhands_auth' not in response.cookies
    capture.assert_not_called()
    identify.assert_not_called()


@pytest.mark.asyncio
async def test_analytics_failure_does_not_break_login(login_setup, analytics_client):
    application, _, provider = login_setup
    capture, _ = analytics_client
    capture.side_effect = RuntimeError('PostHog unavailable')
    response = await _callback(application, provider)

    assert response.status_code == 302
    assert response.headers['location'] == '/done'
    assert 'openhands_auth' in response.cookies
    capture.assert_called_once()


@pytest.mark.asyncio
async def test_login_without_analytics_service_still_succeeds(login_setup, monkeypatch):
    application, _, provider = login_setup
    monkeypatch.setattr('openhands.analytics._analytics_service', None)
    response = await _callback(application, provider)
    assert response.status_code == 302
    assert 'openhands_auth' in response.cookies


@pytest.mark.asyncio
async def test_rejected_login_does_not_emit_analytics(login_setup, analytics_client):
    application, _, provider = login_setup
    capture, identify = analytics_client
    state = oauth_v2._encrypt_state({'mode': 'login'})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url='http://testserver'
    ) as client:
        with patch.object(
            oauth_v2,
            '_exchange_code',
            AsyncMock(side_effect=HTTPException(status_code=502)),
        ):
            response = await client.get(
                f'/oauth/{provider.id}/callback',
                params={'code': 'code', 'state': state},
            )
    assert response.status_code == 502
    assert 'openhands_auth' not in response.cookies
    capture.assert_not_called()
    identify.assert_not_called()


@pytest.mark.asyncio
async def test_federated_login_preserves_identity_provider(
    login_setup, analytics_client, async_session_maker
):
    application, _, provider = login_setup
    provider.details = provider.details.model_copy(
        update={
            'provider_type': ProviderType.ENTERPRISE_SSO,
            'legacy_keycloak_type': ProviderType.GITHUB,
        }
    )
    async with async_session_maker() as session:
        session.add(provider)
        await session.commit()
    capture, identify = analytics_client
    with patch.object(oauth_v2, '_store_legacy_keycloak_git_token', AsyncMock()):
        response = await _callback(application, provider)
    assert response.status_code == 302
    capture.assert_called_once()
    assert capture.call_args.kwargs['properties']['idp'] == 'github'
    assert identify.call_args.kwargs['properties']['idp'] == 'github'
