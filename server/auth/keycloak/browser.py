"""Validate the existing signed, chunked Keycloak browser session."""

import hashlib
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import UUID

import jwt
from pydantic import SecretStr
from starlette.requests import Request

from server.auth.authentication import AuthenticationResult, active_account
from server.auth.contracts import InvalidCredentials, Principal, SessionExpired
from server.auth.cookie_chunking import read_chunked_cookie


async def authenticate_cookie(cookies: Mapping[str, str]) -> AuthenticationResult:
    request = Request({'type': 'http', 'headers': []})
    request._cookies = dict(cookies)
    signed_token = read_chunked_cookie(request, 'keycloak_auth')
    if not signed_token:
        raise InvalidCredentials('Authentication required')
    from storage.encrypt_utils import get_jwt_service

    try:
        decoded = get_jwt_service().verify_jws_token(signed_token)
        access_token = decoded['access_token']
        refresh_token = decoded['refresh_token']
        # The containing OpenHands JWS authenticates these provider claims.
        payload = jwt.decode(access_token, options={'verify_signature': False})
        user_id = UUID(payload['sub'])
    except (jwt.InvalidTokenError, ValueError, KeyError, TypeError):
        raise InvalidCredentials('Invalid browser session') from None
    user = await active_account(user_id)
    refreshed = False
    if payload.get('exp', 0) <= time.time():
        from server.auth.keycloak.token_manager import TokenManager

        tokens = await TokenManager().refresh(refresh_token)
        try:
            refreshed_payload = jwt.decode(
                tokens['access_token'], options={'verify_signature': False}
            )
            if UUID(refreshed_payload['sub']) != user_id:
                raise InvalidCredentials('Invalid refreshed account identifier')
            access_token = tokens['access_token']
            refresh_token = tokens['refresh_token']
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
            raise SessionExpired('Invalid refreshed browser session') from None
        refreshed = True
    return AuthenticationResult(
        principal=Principal(
            user_id=user_id,
            authentication_method='keycloak',
            authenticated_at=datetime.now(timezone.utc),
            session_id=hashlib.sha256(signed_token.encode()).hexdigest(),
        ),
        user=user,
        via_cookie=True,
        access_token=SecretStr(access_token),
        refresh_token=SecretStr(refresh_token),
        accepted_tos=decoded.get('accepted_tos'),
        refreshed=refreshed,
    )
