"""Inference-only bearer on the existing JwtService (audience openhands:inference).

Bound to one sandbox of one member in one org. Never accepted by any other route,
and other audiences (session/API keys, other JWS) are never accepted here."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from openhands.app_server.services.jwt_service import JwtAudience, JwtService

ISSUER = 'openhands-enterprise:customer-gateway'
SCOPE = 'inference'
MAX_TTL = timedelta(hours=1)


class InvalidInferenceToken(Exception):
    pass


@dataclass(frozen=True)
class InferencePrincipal:
    user_id: str
    org_id: str
    session_id: str  # sandbox id
    gateway_id: str
    policy_version: int
    jti: str


def mint_inference_token(
    jwt_service: JwtService,
    *,
    user_id: str,
    org_id: str,
    sandbox_id: str,
    gateway_id: str,
    policy_version: int,
    ttl: timedelta,
) -> str:
    if not timedelta(0) < ttl <= MAX_TTL:
        raise ValueError('inference token ttl out of range')
    payload = {
        'iss': ISSUER,
        'sub': str(user_id),
        'org': str(org_id),
        'sid': str(sandbox_id),
        'gw': str(gateway_id),
        'jti': uuid.uuid4().hex,
        'scope': SCOPE,
        'pv': int(policy_version),
    }
    return jwt_service.create_jws_token(
        payload, expires_in=ttl, audience=JwtAudience.INFERENCE
    )


def verify_inference_token(jwt_service: JwtService, token: str) -> InferencePrincipal:
    try:
        claims = jwt_service.verify_jws_token(token, audience=JwtAudience.INFERENCE)
    except Exception as exc:
        raise InvalidInferenceToken('signature/audience/expiry') from exc
    for c in ('iss', 'sub', 'org', 'sid', 'gw', 'jti', 'scope'):
        v = claims.get(c)
        if not isinstance(v, str) or not 0 < len(v) <= 256:
            raise InvalidInferenceToken(f'claim {c}')
    for c in ('iat', 'exp', 'pv'):
        if type(claims.get(c)) is not int:
            raise InvalidInferenceToken(f'claim {c}')
    if not 0 < claims['exp'] - claims['iat'] <= MAX_TTL.total_seconds():
        raise InvalidInferenceToken('lifetime')
    if claims['iss'] != ISSUER or claims['scope'] != SCOPE:
        raise InvalidInferenceToken('issuer/scope')
    return InferencePrincipal(
        claims['sub'],
        claims['org'],
        claims['sid'],
        claims['gw'],
        claims['pv'],
        claims['jti'],
    )
