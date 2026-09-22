from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_default_config


IV_LENGTH = 12
TAG_LENGTH = 16


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _key(raw: str | None = None) -> bytes:
    if raw is not None:
        material = raw.strip()
    else:
        material = get_default_config().require_credential_encryption_key()
    return hashlib.sha256(material.encode("utf-8")).digest()


def encrypt_credentials(credentials: dict[str, Any]) -> str:
    iv = os.urandom(IV_LENGTH)
    aes = AESGCM(_key())
    plaintext = json.dumps(credentials, separators=(",", ":")).encode("utf-8")
    encrypted = aes.encrypt(iv, plaintext, None)
    ciphertext, tag = encrypted[:-TAG_LENGTH], encrypted[-TAG_LENGTH:]
    return _b64url_encode(iv + tag + ciphertext)


def decrypt_credentials(payload: str) -> dict[str, Any]:
    data = _b64url_decode(payload)
    iv = data[:IV_LENGTH]
    tag = data[IV_LENGTH : IV_LENGTH + TAG_LENGTH]
    ciphertext = data[IV_LENGTH + TAG_LENGTH :]
    aes = AESGCM(_key())
    plaintext = aes.decrypt(iv, ciphertext + tag, None)
    decoded = json.loads(plaintext.decode("utf-8"))
    return decoded if isinstance(decoded, dict) else {}


def encrypt_api_key(api_key: str) -> str:
    return encrypt_credentials({"apiKey": api_key})


def decrypt_api_key(payload: str | None) -> str | None:
    if not payload:
        return None
    # Backwards compatibility with the temporary FastAPI placeholder format used before parity porting.
    if payload.startswith("plain:"):
        return payload[6:]
    credentials = decrypt_credentials(payload)
    value = credentials.get("apiKey")
    return value if isinstance(value, str) else None
