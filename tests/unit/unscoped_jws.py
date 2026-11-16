from datetime import timedelta
from typing import Any

import jwt

from openhands.agent_server.utils import utc_now
from openhands.app_server.services.jwt_service import JwtService


def mint_unscoped_jws_token(
    jwt_service: JwtService,
    payload: dict[str, Any],
    expires_in: timedelta = timedelta(hours=1),
) -> str:
    """Sign a token the way ``JwtService.create_jws_token`` did before every
    token carried an ``aud`` claim, as tokens still in circulation are."""
    key_id = jwt_service.default_key_id
    now = utc_now()
    claims = {
        **payload,
        'iat': int(now.timestamp()),
        'exp': int((now + expires_in).timestamp()),
    }
    secret = jwt_service.get_key(key_id).key.get_secret_value()
    return jwt.encode(claims, secret, algorithm='HS256', headers={'kid': key_id})
