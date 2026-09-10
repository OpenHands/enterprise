"""Argon2id password operations, isolated from the event loop."""

import asyncio
from functools import lru_cache

from fastapi_users.password import PasswordHelper
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError
from pydantic import SecretStr


class PasswordPolicyError(ValueError):
    """A password does not satisfy the application's fixed length policy."""


password_helper = PasswordHelper(PasswordHash.recommended())


def validate_password(password: SecretStr) -> None:
    # NIST SP 800-63B-4, 3.1.1.2: single-factor minimum of 15 code points.
    # Do not strip, normalize, truncate, or impose composition requirements.
    if not 15 <= len(password.get_secret_value()) <= 1024:
        raise PasswordPolicyError('Password must contain 15 to 1024 characters.')


async def hash_password(password: SecretStr) -> str:
    validate_password(password)
    return await asyncio.to_thread(password_helper.hash, password.get_secret_value())


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return password_helper.hash(password_helper.generate())


def _verify(password: str, hashed_password: str | None) -> tuple[bool, str | None]:
    dummy = _dummy_hash()
    if len(password) > 1024:
        password_helper.verify_and_update('invalid password', dummy)
        return False, None
    try:
        result = password_helper.verify_and_update(password, hashed_password or dummy)
    except (ValueError, TypeError, PwdlibError):
        password_helper.verify_and_update(password, dummy)
        return False, None
    return result if hashed_password is not None else (False, None)


async def verify_password(
    password: SecretStr, hashed_password: str | None
) -> tuple[bool, str | None]:
    return await asyncio.to_thread(
        _verify, password.get_secret_value(), hashed_password
    )
