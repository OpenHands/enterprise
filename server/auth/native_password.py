"""Bounded Argon2id work and password policy; passwords are never normalized."""

import asyncio
import re
import secrets
import threading

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

# 64 MiB each, at most two in flight per worker (128 MiB hashing budget).
HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1, type=Type.ID)
_HASH_CAPACITY = threading.BoundedSemaphore(2)
_DUMMY_HASH: str | None = None
_DUMMY_LOCK = threading.Lock()
_COMMON = {
    'password',
    'password123',
    'qwerty',
    'letmein',
    'welcome',
    'admin',
    'iloveyou',
    '1234567890',
    '123456789012345',
    '1234567890123456',
    'passwordpassword',
    'qwertyuiopasdfgh',
    'correct horse battery staple',
    'this is a password',
    'thisismypassword',
    'changeme',
    'openhands',
}


class NativeAuthError(ValueError):
    def __init__(
        self, message: str, status_code: int = 400, *, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def normalize_email(email: str) -> str:
    email = email.strip().casefold()
    if (
        len(email) > 320
        or len(email) < 3
        or email.count('@') != 1
        or any(c.isspace() or ord(c) < 32 for c in email)
        or not email.split('@')[0]
        or '.' not in email.split('@')[1]
        or email.startswith('.')
        or email.endswith('.')
    ):
        raise NativeAuthError('Enter a valid email address')
    return email


def validate_password(password: str) -> None:
    if len(password) < 15:
        raise NativeAuthError('Use a password with at least 15 characters')
    if len(password) > 1024 or len(password.encode('utf-8')) > 4096:
        raise NativeAuthError('Password is too long')
    candidate = password.casefold().strip()
    stem = re.sub(r'[\d\W_]+', '', candidate)
    if (
        candidate in _COMMON
        or stem in _COMMON
        or len(set(candidate)) < 4
        or any(
            candidate == candidate[:n] * (len(candidate) // n)
            for n in range(1, min(9, len(candidate)))
        )
    ):
        raise NativeAuthError('Choose a less common password')


def _bounded_hash(password: str) -> str:
    if not _HASH_CAPACITY.acquire(blocking=False):
        raise NativeAuthError('Authentication temporarily unavailable', 503)
    try:
        return HASHER.hash(password)
    finally:
        _HASH_CAPACITY.release()


async def hash_password(password: str) -> str:
    validate_password(password)
    return await asyncio.to_thread(_bounded_hash, password)


def _verify(password_hash: str | None, password: str) -> bool:
    global _DUMMY_HASH
    if not _HASH_CAPACITY.acquire(blocking=False):
        raise NativeAuthError('Authentication temporarily unavailable', 503)
    try:
        if password_hash is None:
            with _DUMMY_LOCK:
                if _DUMMY_HASH is None:
                    _DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))
            password_hash = _DUMMY_HASH
        try:
            return HASHER.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False
    finally:
        _HASH_CAPACITY.release()


async def verify_password(password_hash: str | None, password: str) -> bool:
    if len(password) > 1024 or len(password.encode('utf-8')) > 4096:
        return False
    return await asyncio.to_thread(_verify, password_hash, password)
