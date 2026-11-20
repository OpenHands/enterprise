"""Signs license answers the way Customer Center does, for tests."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from server.licensing import verification
from server.licensing.verification import license_key_sha256

LICENSE_KEY = 'ohe_lic_test_key'
KEY_ID = 'test-1'
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def valid_payload(**overrides) -> dict:
    payload = {
        'key_sha256': license_key_sha256(LICENSE_KEY),
        'issued_at': _iso(NOW),
        'expires_at': _iso(NOW + timedelta(days=14)),
        'delivery': 'online',
        'valid': True,
        'type': 'trial',
        'license_ends_at': _iso(NOW + timedelta(days=10)),
        'features': {'sso': True, 'offline': False},
        'limits': {'users': 10, 'orgs': None, 'conversations': 5000, 'sandboxes': None},
    }
    payload.update(overrides)
    return payload


def offline_payload(
    license_ends_at: datetime = NOW + timedelta(days=30), **overrides
) -> dict:
    """The payload of an offline license file, as Customer Center signs it."""
    payload = valid_payload(
        type='paid',
        features={'sso': True, 'offline': True},
        delivery='offline',
        license_ends_at=_iso(license_ends_at),
        expires_at=_iso(license_ends_at + timedelta(days=14)),
    )
    payload.update(overrides)
    return payload


def invalid_payload(reason: str = 'invalid_key', **overrides) -> dict:
    payload = {
        'key_sha256': license_key_sha256(LICENSE_KEY),
        'issued_at': _iso(NOW),
        'expires_at': _iso(NOW + timedelta(days=14)),
        'delivery': 'online',
        'valid': False,
        'reason': reason,
    }
    payload.update(overrides)
    return payload


class Signer:
    def __init__(self, key_id: str = KEY_ID):
        self.key_id = key_id
        self.private_key = Ed25519PrivateKey.generate()

    @property
    def public_key_pem(self) -> str:
        return (
            self.private_key.public_key()
            .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
            .decode('ascii')
        )

    def trust(self, monkeypatch) -> None:
        """Make OHE trust only this signer's key for the rest of the test."""
        monkeypatch.setattr(
            verification, 'TRUSTED_PUBLIC_KEYS', {self.key_id: self.public_key_pem}
        )

    def sign(self, payload: dict) -> dict:
        text = json.dumps(payload)
        signature = self.private_key.sign(text.encode('utf-8'))
        return {
            'key_id': self.key_id,
            'payload': text,
            'signature': base64.b64encode(signature).decode('ascii'),
        }
