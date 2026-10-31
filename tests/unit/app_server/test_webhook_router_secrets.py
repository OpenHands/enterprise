"""Tests for ``GET /api/v1/webhooks/secrets``, the endpoint ``LookupSecret``s
created for a conversation call from inside the sandbox.

The user context is a stub: resolving a real one needs the SaaS auth stack,
and what is under test here is which lookup a token's payload routes to.
"""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.app_server.event_callback.webhook_router import (
    jwt_dependency,
    router,
)
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.services.jwt_service import JwtAudience, JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from tests.unit.unscoped_jws import mint_unscoped_jws_token


class _StubUserContext:
    def __init__(self) -> None:
        self.oauth_values = {7: 'oauth-access-token'}
        self.provider_tokens = {ProviderType.GITHUB: 'github-token'}

    async def get_named_oauth_secret_value(self, oauth_provider_id: int):
        return self.oauth_values.get(oauth_provider_id)

    async def get_latest_token(self, provider_type: ProviderType):
        return self.provider_tokens.get(provider_type)


JWT_SERVICE = JwtService([EncryptionKey(key=SecretStr('a' * 32))])


def _lookup_token(payload: dict) -> str:
    return JWT_SERVICE.create_jws_token(
        payload=payload, audience=JwtAudience.SECRET_LOOKUP
    )


def _get(payload: dict | None):
    return _get_with_token(None if payload is None else _lookup_token(payload))


def _get_with_token(token: str | None):
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.dependency_overrides[jwt_dependency.dependency] = lambda: JWT_SERVICE
    headers = {}
    if token is not None:
        headers['X-Access-Token'] = token
    with patch(
        'openhands.app_server.event_callback.webhook_router._resolve_user_context',
        return_value=_StubUserContext(),
    ):
        return TestClient(app).get('/api/v1/webhooks/secrets', headers=headers)


def test_oauth_provider_token_returns_the_named_oauth_secret_value():
    response = _get({'user_id': 'user-1', 'oauth_provider_id': 7})

    assert response.status_code == 200
    assert response.text == 'oauth-access-token'


def test_oauth_provider_token_without_a_value_is_not_found():
    response = _get({'user_id': 'user-1', 'oauth_provider_id': 8})

    assert response.status_code == 404


def test_provider_type_token_returns_the_latest_provider_token():
    response = _get({'user_id': 'user-1', 'provider_type': 'github'})

    assert response.status_code == 200
    assert response.text == 'github-token'


def test_missing_access_token_is_unauthorized():
    assert _get(None).status_code == 401


def test_session_cookie_is_not_a_lookup_token():
    """A signed-in user's ``openhands_auth`` cookie names a user too, but it
    must not be exchangeable for that user's git provider token."""
    cookie = JWT_SERVICE.create_jws_token(
        {'user_id': 'user-1', 'provider_type': 'github'},
        audience=JwtAudience.SESSION,
    )

    assert _get_with_token(cookie).status_code == 401


def test_lookup_token_minted_before_audiences_still_works():
    """Running conversations keep the unscoped tokens they were started with."""
    token = mint_unscoped_jws_token(
        JWT_SERVICE, {'user_id': 'user-1', 'provider_type': 'github'}
    )

    response = _get_with_token(token)

    assert response.status_code == 200
    assert response.text == 'github-token'


def test_unscoped_session_cookie_is_not_a_lookup_token():
    cookie = mint_unscoped_jws_token(
        JWT_SERVICE,
        {'user_id': 'user-1', 'accepted_tos': True, 'access_token_expires_at': None},
    )

    assert _get_with_token(cookie).status_code == 401
