from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import EmailNotValidError, validate_email

from server.constants import DEPLOYMENT_MODE

PASSWORD_MIN_LENGTH = 8
PASSWORD_LINK_TTL_HOURS = 72
PASSWORD_SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

# Argon2 is deliberately expensive (~65ms each), so cap how many hashes run at
# once and make callers queue rather than fail. Only sustained saturation -
# i.e. a wait longer than the timeout - returns 503.
PASSWORD_HASH_CONCURRENCY = int(os.getenv('PASSWORD_HASH_CONCURRENCY', '0')) or max(
    2, os.cpu_count() or 2
)
PASSWORD_HASH_WAIT_TIMEOUT_SECONDS = float(
    os.getenv('PASSWORD_HASH_WAIT_TIMEOUT_SECONDS', '5')
)

_PASSWORD_HASHER = PasswordHasher()
_hash_semaphore: asyncio.Semaphore | None = None
_dummy_password_hash: str | None = None


class PasswordAuthError(ValueError):
    def __init__(self, message: str, status_code: int = 400, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _get_hash_semaphore() -> asyncio.Semaphore:
    # Created lazily so it binds to the running loop rather than import time.
    global _hash_semaphore
    if _hash_semaphore is None:
        _hash_semaphore = asyncio.Semaphore(PASSWORD_HASH_CONCURRENCY)
    return _hash_semaphore


def _get_dummy_password_hash() -> str:
    global _dummy_password_hash
    if _dummy_password_hash is None:
        _dummy_password_hash = _PASSWORD_HASHER.hash(secrets.token_urlsafe(32))
    return _dummy_password_hash


@asynccontextmanager
async def _hash_capacity() -> AsyncIterator[None]:
    semaphore = _get_hash_semaphore()
    try:
        await asyncio.wait_for(
            semaphore.acquire(), timeout=PASSWORD_HASH_WAIT_TIMEOUT_SECONDS
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise PasswordAuthError('Authentication temporarily unavailable', 503) from exc
    try:
        yield
    finally:
        semaphore.release()


def is_password_auth_enabled() -> bool:
    enabled = os.getenv('ENABLE_PASSWORD_AUTH', 'false').lower() in ('true', '1')
    return enabled and DEPLOYMENT_MODE == 'self_hosted'


def require_password_auth_enabled() -> None:
    if not is_password_auth_enabled():
        raise PasswordAuthError('Password authentication is not enabled', 404)


def normalize_email(email: str) -> str:
    normalized = email.strip().casefold()
    try:
        validate_email(normalized, check_deliverability=False)
    except EmailNotValidError as exc:
        raise PasswordAuthError('Enter a valid email address') from exc
    return normalized


def validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise PasswordAuthError(
            f'Use a password with at least {PASSWORD_MIN_LENGTH} characters',
            code='password_too_short',
        )
    if len(password) > 1024 or len(password.encode('utf-8')) > 4096:
        raise PasswordAuthError('Password is too long', code='password_too_long')


def _verify_password(password_hash: str | None, password: str) -> bool:
    # A missing hash still verifies against a dummy one so that unknown
    # accounts cost the same as known ones. VerificationError covers a
    # mismatch and an unreadable stored hash alike: both mean "not this user".
    try:
        return _PASSWORD_HASHER.verify(
            password_hash or _get_dummy_password_hash(), password
        )
    except (InvalidHashError, VerificationError):
        return False


async def hash_password(password: str) -> str:
    validate_password(password)
    async with _hash_capacity():
        return await asyncio.to_thread(_PASSWORD_HASHER.hash, password)


async def verify_password(password_hash: str | None, password: str) -> bool:
    if len(password.encode('utf-8')) > 4096:
        return False
    async with _hash_capacity():
        return await asyncio.to_thread(_verify_password, password_hash, password)


def new_password_token() -> str:
    return secrets.token_urlsafe(32)


def digest_password_token(token: str) -> str:
    return hashlib.sha256(f'openhands-password-link:{token}'.encode()).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
