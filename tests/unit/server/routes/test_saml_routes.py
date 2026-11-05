"""Route tests for the SAML SP-initiated login / ACS / metadata routes.

External cryptographic verification (signature/assertion validation) is
python3-saml's own job, already covered by its test suite -- these tests
mock ``OneLogin_Saml2_Auth``'s instance methods at the ACS boundary (same
spirit as mocking ``httpx.AsyncClient`` for OAuth's token exchange in
``test_oauth_v2_routes.py``) and instead exercise *our* adapter: settings
building, RelayState round-tripping, user resolution, and cookie issuance.

The SP-initiated login route is exercised with a real, generated RSA
keypair so the real ``python3-saml``/``xmlsec`` AuthnRequest-signing path
runs end to end (no mocking) -- that is cheap and catches real
settings-shape mistakes that mocking would hide.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from pydantic import SecretStr

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth import provider_identity
from server.routes import saml
from storage.auth_provider_details import (
    OAuthProviderDetails,
    SamlProviderDetails,
    generate_sp_keypair,
)


def _make_jwt_service() -> JwtService:
    key = EncryptionKey(
        kid='test', key=SecretStr('test-secret-key-for-testing'), active=True
    )
    return JwtService(keys=[key])


@pytest.fixture
def jwt_svc():
    return _make_jwt_service()


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(saml.saml_router)
    return application


@pytest.fixture
def client(app):
    # python3-saml's strict-mode settings validation rejects single-label
    # hosts (e.g. TestClient's default "testserver") as an invalid ACS URL,
    # so pick a base_url that is a well-formed (if fake) domain.
    return TestClient(app, base_url='https://sp.example.com')


SP_PRIVATE_KEY_PEM, SP_CERT_PEM = generate_sp_keypair()


def _fake_saml_provider(
    *,
    provider_id: int = 1,
    idp_entity_id: str | None = 'https://idp.example.com/entity',
    sso_url: str | None = 'https://idp.example.com/sso',
    sso_binding: str = 'redirect',
    idp_x509_cert: str | None = SP_CERT_PEM,  # shape stand-in; never used to sign
    sp_private_key: str | None = SP_PRIVATE_KEY_PEM,
    sp_x509_cert: str | None = SP_CERT_PEM,
    attribute_mapping: dict | None = None,
    name_id_format: str | None = None,
    enabled: bool = True,
):
    provider = MagicMock()
    provider.id = provider_id
    provider.kind = SamlProviderDetails.__name__
    provider.title = 'Acme SSO'
    provider.is_idp = True
    provider.enabled = enabled
    provider.details = SamlProviderDetails(
        idp_entity_id=idp_entity_id,
        sso_url=sso_url,
        sso_binding=sso_binding,
        idp_x509_cert=idp_x509_cert,
        sp_private_key=SecretStr(sp_private_key) if sp_private_key else None,
        sp_x509_cert=sp_x509_cert,
        attribute_mapping=attribute_mapping or {'email': 'mail'},
        name_id_format=name_id_format,
    )
    return provider


def _fake_oauth_provider(provider_id: int = 2):
    """A non-SAML provider row, for the kind-mismatch 404 tests."""
    from openhands.app_server.integrations.service_types import ProviderType

    provider = MagicMock()
    provider.id = provider_id
    provider.kind = OAuthProviderDetails.__name__
    provider.enabled = True
    provider.details = OAuthProviderDetails(
        provider_type=ProviderType('github'), client_id='cid'
    )
    return provider


def _decode_authn_request(location: str) -> tuple[str, str]:
    """Return (decoded AuthnRequest XML, RelayState) from a login redirect."""
    parsed = urlparse(location)
    qs = parse_qs(parsed.query)
    xml = OneLogin_Saml2_Utils.decode_base64_and_inflate(qs['SAMLRequest'][0]).decode()
    return xml, qs['RelayState'][0]


# ── SP-initiated login ──────────────────────────────────────────────────


def test_login_redirects_with_signed_request(client, jwt_svc):
    provider = _fake_saml_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get(
            '/saml/1/login',
            params={'redirect_url': '/dashboard'},
            follow_redirects=False,
        )

    assert response.status_code == 302
    location = response.headers['location']
    assert location.startswith('https://idp.example.com/sso?')
    xml, relay_state = _decode_authn_request(location)
    assert '<samlp:AuthnRequest' in xml
    assert 'Signature' in location or 'SigAlg' in location

    decrypted = provider_identity.decrypt_state(relay_state)
    assert decrypted['redirect_url'] == '/dashboard'
    assert decrypted['request_id'] in xml


def test_login_defaults_redirect_to_root(client, jwt_svc):
    provider = _fake_saml_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get('/saml/1/login', follow_redirects=False)
    _, relay_state = _decode_authn_request(response.headers['location'])
    assert provider_identity.decrypt_state(relay_state)['redirect_url'] == '/'


def test_login_unknown_provider_404(client, jwt_svc):
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=None),
        ),
    ):
        response = client.get('/saml/999/login', follow_redirects=False)
    assert response.status_code == 404


def test_login_wrong_kind_404(client, jwt_svc):
    provider = _fake_oauth_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get('/saml/2/login', follow_redirects=False)
    assert response.status_code == 404


def test_login_disabled_provider_404(client, jwt_svc):
    """A disabled SAML provider must not start an SP-initiated login."""
    provider = _fake_saml_provider(enabled=False)
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get('/saml/1/login', follow_redirects=False)
    assert response.status_code == 404


def test_acs_disabled_provider_404(client, jwt_svc):
    """Fails closed on the ACS too, before the assertion is even parsed."""
    provider = _fake_saml_provider(enabled=False)
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.post(
            '/saml/1/acs', data={'SAMLResponse': 'x', 'RelayState': 'y'}
        )
    assert response.status_code == 404


def test_login_missing_sp_keypair_500(client, jwt_svc):
    provider = _fake_saml_provider(sp_private_key=None, sp_x509_cert=None)
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get('/saml/1/login', follow_redirects=False)
    assert response.status_code == 500


def test_login_incomplete_idp_config_400(client, jwt_svc):
    provider = _fake_saml_provider(sso_url=None)
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get('/saml/1/login', follow_redirects=False)
    assert response.status_code == 400


# ── ACS ──────────────────────────────────────────────────────────────────


def _relay_state(jwt_svc, *, redirect_url='/done', request_id='req-1'):
    with patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc):
        return provider_identity.encrypt_state(
            saml.SamlRelayState(
                redirect_url=redirect_url, request_id=request_id
            ).model_dump()
        )


def _patch_auth_process_response(
    *, authenticated=True, errors=None, name_id='user@example.com', attributes=None
):
    """Patch OneLogin_Saml2_Auth's instance methods used by saml_acs."""
    return (
        patch.object(saml.OneLogin_Saml2_Auth, 'process_response', return_value=None),
        patch.object(saml.OneLogin_Saml2_Auth, 'get_errors', return_value=errors or []),
        patch.object(
            saml.OneLogin_Saml2_Auth, 'is_authenticated', return_value=authenticated
        ),
        patch.object(saml.OneLogin_Saml2_Auth, 'get_nameid', return_value=name_id),
        patch.object(
            saml.OneLogin_Saml2_Auth, 'get_attributes', return_value=attributes or {}
        ),
        patch.object(
            saml.OneLogin_Saml2_Auth, 'get_last_error_reason', return_value='boom'
        ),
    )


def test_acs_missing_saml_response_400(client, jwt_svc):
    provider = _fake_saml_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.post('/saml/1/acs', data={})
    assert response.status_code == 400


def test_acs_missing_relay_state_400(client, jwt_svc):
    provider = _fake_saml_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.post('/saml/1/acs', data={'SAMLResponse': 'x'})
    assert response.status_code == 400


def test_acs_tampered_relay_state_400(client, jwt_svc):
    provider = _fake_saml_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.post(
            '/saml/1/acs',
            data={'SAMLResponse': 'x', 'RelayState': 'not-a-real-relay-state'},
        )
    assert response.status_code == 400


def test_acs_invalid_response_400(client, jwt_svc):
    provider = _fake_saml_provider()
    relay_state = _relay_state(jwt_svc)
    with ExitStack() as stack:
        stack.enter_context(
            patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc)
        )
        stack.enter_context(
            patch.object(
                provider_identity.AuthProviderStore,
                'get_by_id',
                new=AsyncMock(return_value=provider),
            )
        )
        for p in _patch_auth_process_response(authenticated=False, errors=['invalid']):
            stack.enter_context(p)
        response = client.post(
            '/saml/1/acs',
            data={'SAMLResponse': 'bogus', 'RelayState': relay_state},
        )
    assert response.status_code == 400


def test_acs_missing_nameid_400(client, jwt_svc):
    provider = _fake_saml_provider()
    relay_state = _relay_state(jwt_svc)
    with ExitStack() as stack:
        stack.enter_context(
            patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc)
        )
        stack.enter_context(
            patch.object(
                provider_identity.AuthProviderStore,
                'get_by_id',
                new=AsyncMock(return_value=provider),
            )
        )
        for p in _patch_auth_process_response(name_id=None):
            stack.enter_context(p)
        response = client.post(
            '/saml/1/acs',
            data={'SAMLResponse': 'bogus', 'RelayState': relay_state},
        )
    assert response.status_code == 400


def _acs_with_new_user(client, jwt_svc, provider, *, name_id, attributes):
    """Post an ACS response that resolves to a brand-new user; return
    (response, link mock)."""
    provider.allow_match_by_email = False
    provider.allow_registration = True
    fake_user = MagicMock()
    fake_user.id = uuid4()
    fake_user.accepted_tos = '2024-01-01'
    with ExitStack() as stack:
        stack.enter_context(
            patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc)
        )
        stack.enter_context(
            patch.object(
                provider_identity.AuthProviderStore,
                'get_by_id',
                new=AsyncMock(return_value=provider),
            )
        )
        stack.enter_context(
            patch.object(
                provider_identity.AuthProviderUserStore,
                'get',
                new=AsyncMock(return_value=None),
            )
        )
        link_mock = AsyncMock()
        stack.enter_context(
            patch.object(provider_identity.AuthProviderUserStore, 'link', new=link_mock)
        )
        for name, value in (
            ('create_user', fake_user),
            ('is_user_disabled', False),
            ('get_user_by_id', fake_user),
        ):
            stack.enter_context(
                patch.object(
                    provider_identity.UserStore,
                    name,
                    new=AsyncMock(return_value=value),
                )
            )
        for p in _patch_auth_process_response(name_id=name_id, attributes=attributes):
            stack.enter_context(p)
        response = client.post(
            '/saml/1/acs',
            data={'SAMLResponse': 'bogus', 'RelayState': _relay_state(jwt_svc)},
            follow_redirects=False,
        )
    return response, link_mock, fake_user


def test_acs_subject_from_mapped_sub_attribute(client, jwt_svc):
    provider = _fake_saml_provider(
        attribute_mapping={'sub': 'objectGUID', 'email': 'mail'}
    )

    response, link, fake_user = _acs_with_new_user(
        client,
        jwt_svc,
        provider,
        name_id='transient-123',
        attributes={'objectGUID': ['guid-1'], 'mail': ['user@example.com']},
    )

    assert response.status_code == 302
    link.assert_awaited_once_with(
        oauth_provider_id=provider.id,
        user_id=fake_user.id,
        external_subject_id='guid-1',
        external_email='user@example.com',
    )


def test_acs_mapped_sub_attribute_missing_400(client, jwt_svc):
    provider = _fake_saml_provider(
        attribute_mapping={'sub': 'objectGUID', 'email': 'mail'}
    )

    response, link, _ = _acs_with_new_user(
        client,
        jwt_svc,
        provider,
        name_id='transient-123',
        attributes={'mail': ['user@example.com']},
    )

    assert response.status_code == 400
    assert (
        response.json()['detail']
        == 'SAML assertion is missing the attribute mapped to sub'
    )
    link.assert_not_awaited()


def test_acs_login_success_sets_cookie_and_redirects(client, jwt_svc):
    provider = _fake_saml_provider()
    provider.allow_match_by_email = False
    fake_user = MagicMock()
    fake_user.id = uuid4()
    fake_user.accepted_tos = '2024-01-01'
    relay_state = _relay_state(jwt_svc, redirect_url='/dashboard')
    with ExitStack() as stack:
        stack.enter_context(
            patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc)
        )
        stack.enter_context(
            patch.object(
                provider_identity.AuthProviderStore,
                'get_by_id',
                new=AsyncMock(return_value=provider),
            )
        )
        stack.enter_context(
            patch.object(
                provider_identity.AuthProviderUserStore,
                'get',
                new=AsyncMock(return_value=None),
            )
        )
        link_mock = AsyncMock()
        stack.enter_context(
            patch.object(provider_identity.AuthProviderUserStore, 'link', new=link_mock)
        )
        stack.enter_context(
            patch.object(
                provider_identity.UserStore,
                'count_users_with_email',
                new=AsyncMock(return_value=0),
            )
        )
        stack.enter_context(
            patch.object(
                provider_identity.UserStore,
                'get_user_by_email',
                new=AsyncMock(return_value=None),
            )
        )
        create_user_mock = AsyncMock(return_value=fake_user)
        stack.enter_context(
            patch.object(
                provider_identity.UserStore, 'create_user', new=create_user_mock
            )
        )
        stack.enter_context(
            patch.object(
                provider_identity.UserStore,
                'is_user_disabled',
                new=AsyncMock(return_value=False),
            )
        )
        stack.enter_context(
            patch.object(
                provider_identity.UserStore,
                'get_user_by_id',
                new=AsyncMock(return_value=fake_user),
            )
        )
        for p in _patch_auth_process_response(
            name_id='ext-1', attributes={'mail': ['user@example.com']}
        ):
            stack.enter_context(p)
        response = client.post(
            '/saml/1/acs',
            data={'SAMLResponse': 'bogus', 'RelayState': relay_state},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert response.headers['location'] == '/dashboard'
        assert 'openhands_auth' in response.cookies

        created_user_id = create_user_mock.await_args.args[0]
        # The external NameID/sub is never used as the internal User.id --
        # UserStore.create_user requires a real UUID (and derives Org.id
        # from it); a SAML NameID has no guaranteed shape ('ext-1' here
        # isn't one). This must not raise.
        UUID(created_user_id)
        assert created_user_id != 'ext-1'
        userinfo = create_user_mock.await_args.args[1]
        assert userinfo['email'] == 'user@example.com'
        assert userinfo['email_verified'] is True
        link_mock.assert_awaited_once_with(
            oauth_provider_id=provider.id,
            user_id=fake_user.id,
            external_subject_id='ext-1',
            external_email='user@example.com',
        )


def test_acs_disabled_user_redirects_without_cookie(client, jwt_svc):
    provider = _fake_saml_provider()
    fake_user = MagicMock()
    fake_user.id = uuid4()
    relay_state = _relay_state(jwt_svc)
    link = MagicMock()
    link.user_id = fake_user.id
    with ExitStack() as stack:
        stack.enter_context(
            patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc)
        )
        stack.enter_context(
            patch.object(
                provider_identity.AuthProviderStore,
                'get_by_id',
                new=AsyncMock(return_value=provider),
            )
        )
        stack.enter_context(
            patch.object(
                provider_identity.AuthProviderUserStore,
                'get',
                new=AsyncMock(return_value=link),
            )
        )
        stack.enter_context(
            patch.object(
                provider_identity.UserStore,
                'is_user_disabled',
                new=AsyncMock(return_value=True),
            )
        )
        for p in _patch_auth_process_response(name_id='ext-2'):
            stack.enter_context(p)
        response = client.post(
            '/saml/1/acs',
            data={'SAMLResponse': 'bogus', 'RelayState': relay_state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers['location'].endswith('/login?account_disabled=true')
    assert 'openhands_auth' not in response.cookies


def test_acs_no_tos_redirects_through_accept_tos(client, jwt_svc):
    provider = _fake_saml_provider()
    fake_user = MagicMock()
    fake_user.id = uuid4()
    fake_user.accepted_tos = None
    relay_state = _relay_state(jwt_svc, redirect_url='/dashboard')
    link = MagicMock()
    link.user_id = fake_user.id
    with ExitStack() as stack:
        stack.enter_context(
            patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc)
        )
        stack.enter_context(
            patch.object(
                provider_identity.AuthProviderStore,
                'get_by_id',
                new=AsyncMock(return_value=provider),
            )
        )
        stack.enter_context(
            patch.object(
                provider_identity.AuthProviderUserStore,
                'get',
                new=AsyncMock(return_value=link),
            )
        )
        stack.enter_context(
            patch.object(
                provider_identity.UserStore,
                'is_user_disabled',
                new=AsyncMock(return_value=False),
            )
        )
        stack.enter_context(
            patch.object(
                provider_identity.UserStore,
                'get_user_by_id',
                new=AsyncMock(return_value=fake_user),
            )
        )
        for p in _patch_auth_process_response(name_id='ext-3'):
            stack.enter_context(p)
        response = client.post(
            '/saml/1/acs',
            data={'SAMLResponse': 'bogus', 'RelayState': relay_state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    location = response.headers['location']
    assert '/accept-tos' in location
    assert 'redirect_url=%2Fdashboard' in location
    assert 'openhands_auth' in response.cookies


# ── metadata ─────────────────────────────────────────────────────────────


def test_metadata_returns_sp_entity_descriptor(client, jwt_svc):
    provider = _fake_saml_provider()
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get('/saml/1/metadata')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/samlmetadata+xml'
    assert b'EntityDescriptor' in response.content
    assert b'https://sp.example.com/saml/1/metadata' in response.content


def test_metadata_unknown_provider_404(client, jwt_svc):
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=None),
        ),
    ):
        response = client.get('/saml/999/metadata')
    assert response.status_code == 404


def test_metadata_incomplete_config_400(client, jwt_svc):
    provider = _fake_saml_provider(idp_x509_cert=None)
    with (
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
        patch.object(
            provider_identity.AuthProviderStore,
            'get_by_id',
            new=AsyncMock(return_value=provider),
        ),
    ):
        response = client.get('/saml/1/metadata')
    assert response.status_code == 400
