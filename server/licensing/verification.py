"""Verification of the signed licenses Customer Center issues.

Customer Center signs every answer to a license check, and every offline
license file, with an Ed25519 key. Both are an envelope of ``key_id``,
``payload`` (a JSON string) and ``signature`` (base64). OHE trusts a payload
only when all of these hold:

1. ``key_id`` names a public key in ``TRUSTED_PUBLIC_KEYS``.
2. The signature verifies over the exact UTF-8 bytes of ``payload``.
3. ``key_sha256`` in the payload is the SHA-256 of OHE's own license key.
4. ``expires_at`` in the payload is in the future.

An offline license file is a complete license, so OHE needs no license key
to use it. ``read_offline_license`` runs checks 1 and 2 only. The license
service runs check 4 itself, so that it can report a file past its
``expires_at`` as an expired license and not as an invalid file.

The payload's schema is ``docs/license-payload.schema.json`` in the
customer-center repository. Customer Center can add fields and values in v1,
so the models here ignore unknown fields and keep enums as plain strings.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from datetime import datetime
from typing import Annotated, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

# Public keys for each Customer Center signing key ID, as PEM (SPKI). They ship
# with OHE, and no setting can add or replace one: anyone who controls the keys
# OHE trusts can sign their own answers. Customer Center prints them with
# `npm run license:public-key`.
TRUSTED_PUBLIC_KEYS: dict[str, str] = {
    'staging-1': (
        '-----BEGIN PUBLIC KEY-----\n'
        'MCowBQYDK2VwAyEAE6HEy4HtofQuQOzCOewGdgg7pcbBBwmkKUe6BvkM3cU=\n'
        '-----END PUBLIC KEY-----\n'
    ),
    'prod-1': (
        '-----BEGIN PUBLIC KEY-----\n'
        'MCowBQYDK2VwAyEAEhg7vL0qE2HuViftD21LkisHXEUX/pC83hGjvphvivw=\n'
        '-----END PUBLIC KEY-----\n'
    ),
}


class LicenseVerificationError(Exception):
    """Customer Center's answer can't be trusted or can't be read."""


class LicenseFeatures(BaseModel):
    sso: bool = False


class LicenseLimits(BaseModel):
    """Each limit is a count, and None means unlimited."""

    users: int | None = None
    # Team organizations. Personal workspaces don't count.
    orgs: int | None = None
    conversations: int | None = None
    sandboxes: int | None = None


class _PayloadBase(BaseModel):
    key_sha256: str
    issued_at: datetime
    expires_at: datetime


class ValidLicensePayload(_PayloadBase):
    valid: Literal[True]
    # trial, paid or development. It never changes what the license allows.
    type: str
    # When the license expires, unless it's renewed first. None means it never
    # expires.
    license_ends_at: datetime | None
    features: LicenseFeatures
    limits: LicenseLimits


class OfflineLicensePayload(ValidLicensePayload):
    """The payload of an offline license file."""

    delivery: Literal['offline']
    # The install isn't licensed after this. It's always before expires_at.
    license_ends_at: datetime


class InvalidLicensePayload(_PayloadBase):
    valid: Literal[False]
    reason: str


LicensePayload = Annotated[
    ValidLicensePayload | InvalidLicensePayload, Field(discriminator='valid')
]
_payload_adapter: TypeAdapter[ValidLicensePayload | InvalidLicensePayload] = (
    TypeAdapter(LicensePayload)
)


class SignedLicense(BaseModel):
    key_id: str
    payload: str
    signature: str


def license_key_sha256(license_key: str) -> str:
    return hashlib.sha256(license_key.encode('utf-8')).hexdigest()


def verify_signed_license(
    body: object,
    *,
    license_key: str,
    now: datetime,
) -> ValidLicensePayload | InvalidLicensePayload:
    """Return the payload in Customer Center's answer if it can be trusted.

    Raises LicenseVerificationError if any check fails.
    """
    text = _verify_signature(body)
    # Parse only after the signature verifies.
    try:
        payload = _payload_adapter.validate_python(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as e:
        raise LicenseVerificationError('The signed payload is malformed') from e
    _check_license_key(payload, license_key)
    if payload.expires_at <= now:
        raise LicenseVerificationError('The answer has expired')
    return payload


def read_offline_license(body: object) -> OfflineLicensePayload:
    """Return the payload of an offline license file if its signature verifies.

    This does not check ``key_sha256`` or ``expires_at``. Raises
    LicenseVerificationError if the signature does not verify, or if the file
    is not an offline license.
    """
    text = _verify_signature(body)
    try:
        payload = OfflineLicensePayload.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as e:
        raise LicenseVerificationError(
            'The signed payload is not an offline license'
        ) from e
    return payload


def _verify_signature(body: object) -> str:
    """Return the signed payload text if the envelope's signature verifies."""
    try:
        envelope = SignedLicense.model_validate(body)
    except ValidationError as e:
        raise LicenseVerificationError('This is not a signed license') from e

    pem = TRUSTED_PUBLIC_KEYS.get(envelope.key_id)
    if pem is None:
        raise LicenseVerificationError(f'Unknown signing key ID {envelope.key_id!r}')
    public_key = load_pem_public_key(pem.encode('ascii'))
    if not isinstance(public_key, Ed25519PublicKey):
        raise LicenseVerificationError(f'Key {envelope.key_id!r} is not Ed25519')

    try:
        signature = base64.b64decode(envelope.signature, validate=True)
        public_key.verify(signature, envelope.payload.encode('utf-8'))
    except (binascii.Error, InvalidSignature) as e:
        raise LicenseVerificationError('The signature does not verify') from e
    return envelope.payload


def _check_license_key(payload: _PayloadBase, license_key: str) -> None:
    if not hmac.compare_digest(payload.key_sha256, license_key_sha256(license_key)):
        raise LicenseVerificationError(
            'The signed license is for a different license key'
        )
