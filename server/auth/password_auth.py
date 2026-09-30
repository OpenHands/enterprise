from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import threading
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from email_validator import EmailNotValidError, validate_email

from server.constants import DEPLOYMENT_MODE

PASSWORD_MIN_LENGTH = 15
PASSWORD_LINK_TTL_HOURS = 72
PASSWORD_SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

_PASSWORD_HASHER = PasswordHasher()
_HASH_CAPACITY = threading.BoundedSemaphore(2)
_DUMMY_PASSWORD_HASH = _PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


class PasswordAuthError(ValueError):
    def __init__(self, message: str, status_code: int = 400, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


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


def _hash_password(password: str) -> str:
    if not _HASH_CAPACITY.acquire(blocking=False):
        raise PasswordAuthError('Authentication temporarily unavailable', 503)
    try:
        return _PASSWORD_HASHER.hash(password)
    finally:
        _HASH_CAPACITY.release()


async def hash_password(password: str) -> str:
    validate_password(password)
    return await asyncio.to_thread(_hash_password, password)


def _verify_password(password_hash: str, password: str) -> bool:
    if len(password.encode('utf-8')) > 4096:
        return False
    if not _HASH_CAPACITY.acquire(blocking=False):
        raise PasswordAuthError('Authentication temporarily unavailable', 503)
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerifyMismatchError):
        return False
    finally:
        _HASH_CAPACITY.release()


async def verify_password(password_hash: str, password: str) -> bool:
    return await asyncio.to_thread(_verify_password, password_hash, password)


async def verify_password_or_dummy(password_hash: str | None, password: str) -> bool:
    return await verify_password(password_hash or _DUMMY_PASSWORD_HASH, password)


def new_password_token() -> str:
    return secrets.token_urlsafe(32)


def digest_password_token(token: str) -> str:
    return hashlib.sha256(f'openhands-password-link:{token}'.encode()).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
