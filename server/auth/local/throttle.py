"""Fixed authentication limits using the existing shared Redis strategy."""

import hashlib
from functools import lru_cache

from fastapi import HTTPException, Request

from server.rate_limit import RateLimiter, create_redis_rate_limiter
from storage.local_credentials import normalize_login_email


@lru_cache(maxsize=2)
def _limiter(recovery: bool) -> RateLimiter:
    limiter = create_redis_rate_limiter(
        '5/minute; 20/hour' if recovery else '10/minute; 100/hour'
    )
    assert limiter is not None
    return limiter


async def throttle(
    request: Request, identifier: str, *, recovery: bool = False
) -> None:
    identifier = hashlib.sha256(normalize_login_email(identifier).encode()).hexdigest()
    source_ip = request.client.host if request.client else 'unknown'
    try:
        limiter = _limiter(recovery)
        allowed = True
        for category, key in (('identifier', identifier), ('source', source_ip)):
            for window in limiter.limit_items:
                hit = await limiter.strategy.hit(
                    window,
                    'local-recovery' if recovery else 'local-login',
                    category,
                    key,
                )
                allowed = hit and allowed
    except Exception:
        # RateLimiter.hit intentionally fails open for existing application APIs.
        # Password endpoints call its shared strategy directly and fail closed.
        raise HTTPException(503, 'Authentication is temporarily unavailable.') from None
    if not allowed:
        raise HTTPException(
            429, 'Too many attempts. Try again later.', headers={'Retry-After': '60'}
        )
