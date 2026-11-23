from __future__ import annotations

import json
from datetime import timedelta

import pytest

from server.licensing.verification import (
    TRUSTED_PUBLIC_KEYS,
    InvalidLicensePayload,
    LicenseVerificationError,
    OfflineLicensePayload,
    ValidLicensePayload,
    read_offline_license,
    verify_signed_license,
)
from tests.unit.license_signing import (
    LICENSE_KEY,
    NOW,
    Signer,
    invalid_payload,
    offline_payload,
    valid_payload,
)


def _verify(body, *, license_key=LICENSE_KEY, now=NOW):
    return verify_signed_license(
        body,
        license_key=license_key,
        now=now,
    )


def test_valid_payload_verifies(signer):
    payload = _verify(signer.sign(valid_payload()))

    assert isinstance(payload, ValidLicensePayload)
    assert payload.type == 'trial'
    assert payload.license_ends_at == NOW + timedelta(days=10)
    assert payload.features.sso is True
    assert payload.limits.users == 10
    assert payload.limits.sandboxes is None


def test_license_that_never_expires_verifies(signer):
    payload = _verify(signer.sign(valid_payload(type='paid', license_ends_at=None)))

    assert isinstance(payload, ValidLicensePayload)
    assert payload.license_ends_at is None


def test_invalid_payload_verifies(signer):
    payload = _verify(signer.sign(invalid_payload('expired')))

    assert isinstance(payload, InvalidLicensePayload)
    assert payload.reason == 'expired'


def test_unknown_fields_and_values_are_accepted(signer):
    body = signer.sign(
        valid_payload(
            type='something_new',
            features={'sso': False, 'new_feature': True},
            extra_field=1,
        )
    )

    payload = _verify(body)

    assert isinstance(payload, ValidLicensePayload)
    assert payload.type == 'something_new'


def test_edited_payload_fails(signer):
    body = signer.sign(invalid_payload('expired'))
    edited = json.loads(body['payload'])
    edited['valid'] = True
    body['payload'] = json.dumps(edited)

    with pytest.raises(LicenseVerificationError, match='signature'):
        _verify(body)


def test_signature_from_another_key_fails(signer):
    other = Signer(key_id=signer.key_id)
    body = other.sign(valid_payload())

    with pytest.raises(LicenseVerificationError, match='signature'):
        _verify(body)


def test_unknown_key_id_fails(signer):
    body = Signer(key_id='other-1').sign(valid_payload())

    with pytest.raises(LicenseVerificationError, match='Unknown signing key'):
        _verify(body)


def test_malformed_signature_fails(signer):
    body = signer.sign(valid_payload())
    body['signature'] = 'not base64!'

    with pytest.raises(LicenseVerificationError, match='signature'):
        _verify(body)


def test_answer_for_another_key_fails(signer):
    body = signer.sign(valid_payload())

    with pytest.raises(LicenseVerificationError, match='different license key'):
        _verify(body, license_key='ohe_lic_other')


def test_expired_answer_fails(signer):
    body = signer.sign(valid_payload())

    with pytest.raises(LicenseVerificationError, match='expired'):
        _verify(body, now=NOW + timedelta(days=14))


@pytest.mark.parametrize('body', [None, {}, {'key_id': 'test-1'}, 'text'])
def test_envelope_that_is_not_a_signed_license_fails(signer, body):
    with pytest.raises(LicenseVerificationError):
        _verify(body)


@pytest.mark.parametrize('payload', [{}, {'valid': True}])
def test_signed_payload_without_its_fields_fails(signer, payload):
    with pytest.raises(LicenseVerificationError, match='malformed'):
        _verify(signer.sign(payload))


def test_trusted_public_keys_are_ed25519():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    assert TRUSTED_PUBLIC_KEYS
    for pem in TRUSTED_PUBLIC_KEYS.values():
        assert isinstance(load_pem_public_key(pem.encode()), Ed25519PublicKey)


def _read_offline(body):
    return read_offline_license(body)


def test_offline_license_reads(signer):
    payload = _read_offline(signer.sign(offline_payload()))

    assert isinstance(payload, OfflineLicensePayload)
    assert payload.delivery == 'offline'
    assert payload.license_ends_at == NOW + timedelta(days=30)
    assert payload.expires_at == NOW + timedelta(days=44)
    assert payload.type == 'paid'


def test_offline_license_reads_after_it_expires(signer):
    # The service, not the reader, decides what a file past expires_at means.
    payload = _read_offline(
        signer.sign(offline_payload(license_ends_at=NOW - timedelta(days=60)))
    )

    assert payload.expires_at < NOW


@pytest.mark.parametrize(
    'payload',
    [
        valid_payload(),
        invalid_payload('expired'),
        offline_payload(valid=False),
        {**offline_payload(), 'license_ends_at': None},
    ],
)
def test_signed_payload_that_is_not_an_offline_license_fails(signer, payload):
    with pytest.raises(LicenseVerificationError, match='not an offline license'):
        _read_offline(signer.sign(payload))


def test_edited_offline_license_fails(signer):
    body = signer.sign(offline_payload())
    body['payload'] = body['payload'].replace('"paid"', '"trial"')

    with pytest.raises(LicenseVerificationError, match='signature'):
        _read_offline(body)


def test_offline_license_for_any_key_reads(signer):
    # The file is the license, so it isn't checked against a configured key.
    payload = _read_offline(signer.sign(offline_payload(key_sha256='0' * 64)))

    assert payload.key_sha256 == '0' * 64
