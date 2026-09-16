"""Bounded Argon2id work and password policy; passwords are never normalized."""

import asyncio
import threading

from email_validator import EmailNotValidError, validate_email
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

# 64 MiB each, at most two in flight per worker (128 MiB hashing budget).
_PASSWORD_HASH = PasswordHash((Argon2Hasher(),))
_HASH_CAPACITY = threading.BoundedSemaphore(2)


class NativeAuthError(ValueError):
    def __init__(
        self, message: str, status_code: int = 400, *, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def normalize_email(email: str) -> str:
    # Keep the established identity key. Adopting the library's NFC/IDNA
    # normalization here could merge distinct existing accounts.
    email = email.strip().casefold()
    try:
        # Private mail domains need no public DNS records in self-hosted installs.
        validate_email(email, check_deliverability=False)
    except EmailNotValidError as exc:
        raise NativeAuthError('Enter a valid email address') from exc
    return email


def validate_password(password: str) -> None:
    if len(password) < 15:
        raise NativeAuthError('Use a password with at least 15 characters')
    if len(password) > 1024 or len(password.encode('utf-8')) > 4096:
        raise NativeAuthError('Password is too long')


def _bounded_hash(password: str) -> str:
    if not _HASH_CAPACITY.acquire(blocking=False):
        raise NativeAuthError('Authentication temporarily unavailable', 503)
    try:
        return _PASSWORD_HASH.hash(password)
    finally:
        _HASH_CAPACITY.release()


async def hash_password(password: str) -> str:
    validate_password(password)
    return await asyncio.to_thread(_bounded_hash, password)
