"""Password hashing for the dev IDP (self-hosted email + password login).

Real IDP users never have a local password — they authenticate via OAuth/OIDC
and ``User.password_hash`` stays ``NULL`` for them. Only accounts created
through the dev IDP sign-up flow (``server.routes.idp``) set this column.

Uses Argon2id (the OWASP-recommended default) via ``argon2-cffi``, a
dependency already pulled in transitively by ``jupyter-server`` and promoted
here to a direct, version-pinned dependency.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

# Minimum password length enforced at sign-up. Not a full policy (no
# character-class requirements) — the dev IDP is for self-hosted/trial use,
# not a hardened production auth system.
MIN_PASSWORD_LENGTH = 8

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash ``password`` for storage in ``User.password_hash``."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Whether ``password`` matches ``password_hash``.

    Never raises — a malformed/foreign hash (e.g. ``None`` coerced upstream,
    or a hash produced by a different scheme) is treated as "does not match"
    rather than propagating as a 500.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHash):
        return False
