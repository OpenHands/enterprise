"""Unit tests for ``storage.auth_provider_details.SamlProviderDetails``.

Covers ``generate_sp_keypair`` and the ``sp_public_key`` computed field it
backs -- ``server.routes.oauth_providers_admin._ensure_sp_keypair`` and its
route-level tests cover when/whether a key gets (re)generated; these tests
cover the keypair/derivation itself in isolation.
"""

from __future__ import annotations

from pydantic import SecretStr

from storage.auth_provider_details import (
    SamlProviderDetails,
    generate_sp_keypair,
)


def test_generate_sp_keypair_returns_pem_encoded_pair():
    private_pem, public_pem = generate_sp_keypair()

    assert private_pem.startswith('-----BEGIN PRIVATE KEY-----')
    assert private_pem.strip().endswith('-----END PRIVATE KEY-----')
    assert public_pem.startswith('-----BEGIN PUBLIC KEY-----')
    assert public_pem.strip().endswith('-----END PUBLIC KEY-----')


def test_generate_sp_keypair_is_not_deterministic():
    private_pem_1, _ = generate_sp_keypair()
    private_pem_2, _ = generate_sp_keypair()
    assert private_pem_1 != private_pem_2


def test_sp_public_key_is_none_when_no_private_key_set():
    details = SamlProviderDetails()
    assert details.sp_private_key is None
    assert details.sp_public_key is None


def test_sp_public_key_matches_derived_public_half_of_private_key():
    private_pem, public_pem = generate_sp_keypair()
    details = SamlProviderDetails(sp_private_key=SecretStr(private_pem))

    assert details.sp_public_key == public_pem


def test_sp_public_key_returns_none_for_unparseable_private_key():
    details = SamlProviderDetails(sp_private_key=SecretStr('not a real PEM key'))
    assert details.sp_public_key is None


def test_sp_private_key_is_masked_on_default_serialization():
    private_pem, _ = generate_sp_keypair()
    details = SamlProviderDetails(sp_private_key=SecretStr(private_pem))

    dumped = details.model_dump()
    assert dumped['sp_private_key'] not in (None, private_pem)
    # The derived public key is not a secret and is dumped in full.
    assert dumped['sp_public_key'] == details.sp_public_key


def test_sp_private_key_empty_string_normalizes_to_none():
    details = SamlProviderDetails(sp_private_key='')
    assert details.sp_private_key is None
    assert details.sp_public_key is None
