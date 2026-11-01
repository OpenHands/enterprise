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
        'valid': True,
        'plan': 'teams',
        'status': {
            'state': 'trial_active',
            'trial_ends_at': _iso(NOW + timedelta(days=10)),
        },
        'features': {'sso': True},
        'limits': {'users': 10, 'conversations': 5000, 'sandboxes': None},
    }
    payload.update(overrides)
    return payload


def invalid_payload(reason: str = 'revoked', **overrides) -> dict:
    payload = {
        'key_sha256': license_key_sha256(LICENSE_KEY),
        'issued_at': _iso(NOW),
        'expires_at': _iso(NOW + timedelta(days=14)),
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
