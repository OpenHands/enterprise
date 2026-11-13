"""Verify the run token the automation service mints for an automation run.

The contract is shared with OpenHands/automation#726 and pinned by a test vector
committed in both repositories:

- JWT, HS256, signed with a key derived from ``AUTOMATIONS_SERVICE_KEY`` by
  HKDF-SHA256 (no salt, 32 bytes, info ``openhands-worklog-run-token-v1``). The
  raw service key never signs.
- Claims: ``aud`` ``worklog``, ``org_id``, ``factory_id``, ``automation_id``,
  ``automation_name``, ``run_id``, ``iat``, ``exp``.

Only the signature, ``aud`` and ``exp`` are checked. There is no call to the
automation service: it signed the token while the factory existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import Header, HTTPException, status

from server.services.worklog_service import (
    MAX_AUTHOR_NAME_LENGTH,
    automations_service_key,
)

WORKLOG_TOKEN_AUDIENCE = 'worklog'
WORKLOG_TOKEN_HKDF_INFO = b'openhands-worklog-run-token-v1'
_SIGNING_KEY_LENGTH = 32
_REQUIRED_CLAIMS = [
    'aud',
    'exp',
    'iat',
    'org_id',
    'factory_id',
    'automation_id',
    'run_id',
]


@dataclass(frozen=True)
class WorklogRunClaims:
    org_id: UUID
    factory_id: UUID
    automation_id: UUID
    automation_name: str | None
    run_id: UUID


def derive_worklog_signing_key(service_key: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_SIGNING_KEY_LENGTH,
        salt=None,
        info=WORKLOG_TOKEN_HKDF_INFO,
    ).derive(service_key.encode('utf-8'))


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={'WWW-Authenticate': 'Bearer'},
    )


def verify_worklog_token(token: str) -> WorklogRunClaims:
    """The claims of a valid run token. 503 when no service key is configured, 401
    for a token that is expired, wrongly signed, for another audience or
    malformed."""
    service_key = automations_service_key()
    if not service_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Run token authentication is not configured',
        )
    try:
        claims = jwt.decode(
            token,
            derive_worklog_signing_key(service_key),
            algorithms=['HS256'],
            audience=WORKLOG_TOKEN_AUDIENCE,
            options={'require': _REQUIRED_CLAIMS},
        )
        name = claims.get('automation_name')
        return WorklogRunClaims(
            org_id=UUID(claims['org_id']),
            factory_id=UUID(claims['factory_id']),
            automation_id=UUID(claims['automation_id']),
            automation_name=(
                name[:MAX_AUTHOR_NAME_LENGTH] if isinstance(name, str) else None
            ),
            run_id=UUID(claims['run_id']),
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized('The run token has expired') from None
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError):
        raise _unauthorized('Invalid run token') from None


async def require_worklog_run_token(
    authorization: str | None = Header(default=None),
) -> WorklogRunClaims:
    """Dependency: authenticate a request by its run token alone. A session or an
    API key in the same header is just an invalid token."""
    scheme, _, token = (authorization or '').partition(' ')
    if scheme.lower() != 'bearer' or not token.strip():
        raise _unauthorized('A run token is required')
    return verify_worklog_token(token.strip())
