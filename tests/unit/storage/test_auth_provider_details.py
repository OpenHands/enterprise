"""Unit tests for ``storage.auth_provider_details.SamlProviderDetails``.

Covers ``generate_sp_keypair`` -- the key+cert pair it returns are stored
verbatim on ``sp_private_key``/``sp_x509_cert`` (no derivation at read time,
unlike the pre-cert ``sp_public_key`` computed field this replaced).
``server.routes.oauth_providers_admin._ensure_sp_keypair`` and its
route-level tests cover when/whether a pair gets (re)generated; these tests
cover the generation itself in isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from pydantic import SecretStr, ValidationError

from openhands.app_server.integrations.service_types import ProviderType
from storage.auth_provider_details import (
    SP_CERT_COMMON_NAME,
    SP_CERT_VALID_DAYS,
    OAuthProviderDetails,
    SamlProviderDetails,
    generate_sp_keypair,
)


def test_generate_sp_keypair_returns_pem_encoded_key_and_cert():
    private_pem, cert_pem = generate_sp_keypair()

    assert private_pem.startswith('-----BEGIN PRIVATE KEY-----')
    assert private_pem.strip().endswith('-----END PRIVATE KEY-----')
    assert cert_pem.startswith('-----BEGIN CERTIFICATE-----')
    assert cert_pem.strip().endswith('-----END CERTIFICATE-----')


def test_generate_sp_keypair_is_not_deterministic():
    private_pem_1, cert_pem_1 = generate_sp_keypair()
    private_pem_2, cert_pem_2 = generate_sp_keypair()
    assert private_pem_1 != private_pem_2
    assert cert_pem_1 != cert_pem_2


def test_generate_sp_keypair_cert_embeds_the_matching_private_key():
    """The cert's public key must round-trip back to the same private key --
    a cert for a *different* key is worse than no cert at all (silently
    unusable at the IdP)."""
    private_pem, cert_pem = generate_sp_keypair()

    private_key = serialization.load_pem_private_key(
        private_pem.encode(), password=None
    )
    cert = x509.load_pem_x509_certificate(cert_pem.encode())

    expected_public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    actual_public_bytes = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    assert actual_public_bytes == expected_public_bytes


def test_generate_sp_keypair_cert_is_self_signed_with_expected_validity():
    _, cert_pem = generate_sp_keypair()
    cert = x509.load_pem_x509_certificate(cert_pem.encode())

    assert cert.subject == cert.issuer
    assert (
        cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
        == SP_CERT_COMMON_NAME
    )
    now = datetime.now(timezone.utc)
    assert cert.not_valid_before_utc <= now
    assert cert.not_valid_after_utc >= now + timedelta(days=SP_CERT_VALID_DAYS - 1)


def test_sp_x509_cert_is_none_when_not_set():
    details = SamlProviderDetails()
    assert details.sp_private_key is None
    assert details.sp_x509_cert is None


def test_sp_x509_cert_round_trips_the_generated_cert():
    private_pem, cert_pem = generate_sp_keypair()
    details = SamlProviderDetails(
        sp_private_key=SecretStr(private_pem), sp_x509_cert=cert_pem
    )

    assert details.sp_x509_cert == cert_pem


def test_sp_private_key_is_masked_on_default_serialization():
    private_pem, cert_pem = generate_sp_keypair()
    details = SamlProviderDetails(
        sp_private_key=SecretStr(private_pem), sp_x509_cert=cert_pem
    )

    dumped = details.model_dump()
    assert dumped['sp_private_key'] not in (None, private_pem)
    # The cert is public by construction and is dumped in full.
    assert dumped['sp_x509_cert'] == cert_pem


def test_sp_private_key_empty_string_normalizes_to_none():
    details = SamlProviderDetails(sp_private_key='')
    assert details.sp_private_key is None


def test_legacy_keycloak_type_defaults_to_none():
    details = OAuthProviderDetails(
        provider_type=ProviderType.ENTERPRISE_SSO, client_id='cid'
    )
    assert details.legacy_keycloak_type is None


def test_legacy_keycloak_type_accepts_a_git_provider():
    details = OAuthProviderDetails(
        provider_type=ProviderType.ENTERPRISE_SSO,
        client_id='cid',
        legacy_keycloak_type=ProviderType.GITHUB,
    )
    assert details.legacy_keycloak_type == ProviderType.GITHUB


def test_legacy_keycloak_type_rejects_enterprise_sso():
    with pytest.raises(ValidationError):
        OAuthProviderDetails(
            provider_type=ProviderType.ENTERPRISE_SSO,
            client_id='cid',
            legacy_keycloak_type=ProviderType.ENTERPRISE_SSO,
        )
