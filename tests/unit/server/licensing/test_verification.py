from __future__ import annotations

import json
from datetime import timedelta

import pytest

from server.licensing.verification import (
    TRUSTED_PUBLIC_KEYS,
    InvalidLicensePayload,
    LicenseVerificationError,
    ValidLicensePayload,
    verify_signed_license,
)
from tests.unit.license_signing import (
    LICENSE_KEY,
    NOW,
    Signer,
    invalid_payload,
    valid_payload,
)


def _verify(body, signer: Signer, *, license_key=LICENSE_KEY, now=NOW):
    return verify_signed_license(
        body,
        license_key=license_key,
        now=now,
        trusted_public_keys=signer.trusted_keys,
    )


def test_valid_payload_verifies(signer):
    payload = _verify(signer.sign(valid_payload()), signer)

    assert isinstance(payload, ValidLicensePayload)
    assert payload.plan == 'teams'
    assert payload.status.state == 'trial_active'
    assert payload.features.sso is True
    assert payload.limits.users == 10
    assert payload.limits.sandboxes is None


def test_invalid_payload_verifies(signer):
    payload = _verify(signer.sign(invalid_payload('revoked')), signer)

    assert isinstance(payload, InvalidLicensePayload)
    assert payload.reason == 'revoked'


def test_unknown_fields_and_values_are_accepted(signer):
    body = signer.sign(
        valid_payload(
            plan='platinum',
            status={'state': 'something_new'},
            features={'sso': False, 'new_feature': True},
            extra_field=1,
        )
    )

    payload = _verify(body, signer)

    assert isinstance(payload, ValidLicensePayload)
    assert payload.plan == 'platinum'
    assert payload.status.state == 'something_new'


def test_edited_payload_fails(signer):
    body = signer.sign(invalid_payload('revoked'))
    edited = json.loads(body['payload'])
    edited['valid'] = True
    body['payload'] = json.dumps(edited)

    with pytest.raises(LicenseVerificationError, match='signature'):
        _verify(body, signer)


def test_signature_from_another_key_fails(signer):
    other = Signer(key_id=signer.key_id)
    body = other.sign(valid_payload())

    with pytest.raises(LicenseVerificationError, match='signature'):
        _verify(body, signer)


def test_unknown_key_id_fails(signer):
    body = Signer(key_id='other-1').sign(valid_payload())

    with pytest.raises(LicenseVerificationError, match='Unknown signing key'):
        _verify(body, signer)


def test_malformed_signature_fails(signer):
    body = signer.sign(valid_payload())
    body['signature'] = 'not base64!'

    with pytest.raises(LicenseVerificationError, match='signature'):
        _verify(body, signer)


def test_answer_for_another_key_fails(signer):
    body = signer.sign(valid_payload())

    with pytest.raises(LicenseVerificationError, match='different license key'):
        _verify(body, signer, license_key='ohe_lic_other')


def test_expired_answer_fails(signer):
    body = signer.sign(valid_payload())

    with pytest.raises(LicenseVerificationError, match='expired'):
        _verify(body, signer, now=NOW + timedelta(days=14))


@pytest.mark.parametrize('body', [None, {}, {'key_id': 'test-1'}, 'text'])
def test_envelope_that_is_not_a_signed_license_fails(signer, body):
    with pytest.raises(LicenseVerificationError):
        _verify(body, signer)


@pytest.mark.parametrize('payload', [{}, {'valid': True}])
def test_signed_payload_without_its_fields_fails(signer, payload):
    with pytest.raises(LicenseVerificationError, match='malformed'):
        _verify(signer.sign(payload), signer)


def test_trusted_public_keys_are_ed25519():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    assert TRUSTED_PUBLIC_KEYS
    for pem in TRUSTED_PUBLIC_KEYS.values():
        assert isinstance(load_pem_public_key(pem.encode()), Ed25519PublicKey)
