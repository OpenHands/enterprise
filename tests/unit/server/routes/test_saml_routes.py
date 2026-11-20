"""SAML adapter tests and real signed-response replay/binding regressions."""

from __future__ import annotations

import asyncio
import time
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from freezegun import freeze_time
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from pydantic import SecretStr

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth import provider_identity
from server.licensing.limits import UserLimitReachedError
from server.routes import saml
from storage.auth_provider_details import (
    GroupOrgMapping,
    OAuthProviderDetails,
    SamlProviderDetails,
    generate_sp_keypair,
)
from tests.unit.server.routes.saml_signed_helpers import (
    SignedLogin,
    bounded,
    make_provider,
    signed_response,
    stamp,
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
                redirect_url=redirect_url, request_id=request_id, issued_at=time.time()
            ).model_dump()
        )


def _patch_auth_process_response(
    *, authenticated=True, errors=None, name_id='user@example.com', attributes=None
):
    """Patch OneLogin_Saml2_Auth's instance methods used by saml_acs."""
    return (
        patch.object(
            saml.OneLogin_Saml2_Auth,
            'process_response',
            autospec=True,
            side_effect=lambda auth, **kwargs: setattr(
                auth, 'bearer_not_on_or_after', time.time() + 900
            ),
        ),
        patch.object(
            saml.OneLogin_Saml2_Auth,
            'get_last_assertion_id',
            return_value='_mock_assertion',
        ),
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


def _acs_with_new_user(
    client, jwt_svc, provider, *, name_id, attributes, disabled: bool = False
):
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
            ('is_user_disabled', disabled),
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


def test_acs_without_email_400(client, jwt_svc):
    provider = _fake_saml_provider(attribute_mapping={'sub': 'objectGUID'})

    response, link, _ = _acs_with_new_user(
        client,
        jwt_svc,
        provider,
        name_id='persistent-id',
        attributes={'objectGUID': ['guid-1'], 'cn': ['Tim']},
    )

    assert response.status_code == 400
    assert 'no email' in response.json()['detail']
    link.assert_not_awaited()


def test_acs_email_from_email_typed_name_id(client, jwt_svc):
    provider = _fake_saml_provider(
        attribute_mapping={'sub': 'objectGUID'},
        name_id_format='urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress',
    )

    response, link, fake_user = _acs_with_new_user(
        client,
        jwt_svc,
        provider,
        name_id='user@example.com',
        attributes={'objectGUID': ['guid-1']},
    )

    assert response.status_code == 302
    link.assert_awaited_once_with(
        oauth_provider_id=provider.id,
        user_id=fake_user.id,
        external_subject_id='guid-1',
        external_email='user@example.com',
    )


GROUP_ORG_MAPPINGS = [GroupOrgMapping(group='Platform', org_id=uuid4())]
GROUPS_ATTRIBUTES = {'mail': ['user@example.com'], 'groups': ['Everyone', 'Platform']}


def _group_mapped_provider():
    provider = _fake_saml_provider()
    provider.details.group_org_mappings = GROUP_ORG_MAPPINGS
    return provider


def test_acs_adds_the_user_to_the_orgs_mapped_to_their_groups(client, jwt_svc):
    provider = _group_mapped_provider()

    with patch.object(saml, 'add_user_to_mapped_orgs', new=AsyncMock()) as add_to_orgs:
        response, _, fake_user = _acs_with_new_user(
            client, jwt_svc, provider, name_id='ext-1', attributes=GROUPS_ATTRIBUTES
        )

    assert response.status_code == 302
    add_to_orgs.assert_awaited_once_with(
        GROUP_ORG_MAPPINGS, str(fake_user.id), ['Everyone', 'Platform']
    )


def test_acs_does_not_add_a_disabled_user_to_mapped_orgs(client, jwt_svc):
    provider = _group_mapped_provider()

    with patch.object(saml, 'add_user_to_mapped_orgs', new=AsyncMock()) as add_to_orgs:
        _acs_with_new_user(
            client,
            jwt_svc,
            provider,
            name_id='ext-1',
            attributes=GROUPS_ATTRIBUTES,
            disabled=True,
        )

    add_to_orgs.assert_not_awaited()


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
        for p in _patch_auth_process_response(
            name_id='ext-2', attributes={'mail': ['user@example.com']}
        ):
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
        for p in _patch_auth_process_response(
            name_id='ext-3', attributes={'mail': ['user@example.com']}
        ):
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


def test_acs_new_user_past_the_license_limit_redirects_to_login(client, jwt_svc):
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
        stack.enter_context(
            patch.object(
                saml,
                'resolve_or_create_user',
                new=AsyncMock(side_effect=UserLimitReachedError(1)),
            )
        )
        for p in _patch_auth_process_response(
            name_id='ext-3', attributes={'mail': ['user@example.com']}
        ):
            stack.enter_context(p)
        response = client.post(
            '/saml/1/acs',
            data={'SAMLResponse': 'bogus', 'RelayState': relay_state},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers['location'].endswith('/login?user_limit_reached=true')
    assert 'openhands_auth' not in response.cookies


@pytest.fixture
async def signed_login(app, async_session_maker):
    provider = await make_provider(async_session_maker)
    with (
        freeze_time('2026-11-20T18:00:00Z', real_asyncio=True),
        patch('storage.auth_provider_store.a_session_maker', async_session_maker),
        patch('storage.saml_replay_store.a_session_maker', async_session_maker),
    ):
        async with SignedLogin(app, provider) as login:
            yield login


def assert_success(response):
    assert response.status_code == 302, response.text
    assert 'openhands_auth=' in response.headers.get('set-cookie', '')


def assert_invalid(response):
    assert response.status_code == 400, response.text
    assert response.json()['detail'] == 'Invalid SAML response'
    assert 'set-cookie' not in response.headers


@pytest.mark.parametrize('variant', ['P1', 'P2', 'P3', 'P4', 'P5'])
async def test_signed_positive(signed_login, variant):
    login = signed_login
    options = {}
    if variant == 'P2':
        options['envelope'] = False
    if variant == 'P4':
        options['sign_response'] = True
    if variant in ('P3', 'P5'):
        second = (
            {'InResponseTo': login.request_id, 'NotOnOrAfter': stamp(1200)}
            if variant == 'P3'
            else {'Method': 'urn:oasis:names:tc:SAML:2.0:cm:holder-of-key'}
        )
        options['confirmations'] = [
            {'InResponseTo': login.request_id, 'NotOnOrAfter': stamp(900)},
            second,
        ]
    assert_success(await login.post(**options))


async def test_signed_R2_sequential_replay(signed_login):
    assert_success(await signed_login.post())
    assert_invalid(await signed_login.post())


async def test_signed_R3_concurrent_replay(signed_login):
    login = signed_login
    data = {
        'RelayState': login.relay,
        'SAMLResponse': signed_response(login.provider.id, login.request_id),
    }
    responses = await bounded(
        asyncio.gather(*(login.client.post(login.path, data=data) for _ in range(5)))
    )
    assert [r.status_code for r in responses].count(302) == 1
    for response in responses:
        if response.status_code == 302:
            assert_success(response)
        else:
            assert_invalid(response)


async def test_signed_R4_request_reuse(signed_login):
    assert_success(await signed_login.post())
    assert_invalid(await signed_login.post(assertion_id='_different'))


@pytest.mark.parametrize('case', ['a', 'b', 'c', 'd', 'e'])
async def test_signed_R5_binding(signed_login, case):
    login = signed_login
    bound = {'InResponseTo': login.request_id, 'NotOnOrAfter': stamp(900)}
    missing = {'NotOnOrAfter': stamp(900)}
    wrong = {**bound, 'InResponseTo': '_wrong'}
    confirmations = {
        'a': [missing],
        'b': [wrong],
        'c': [wrong],
        'd': [bound, wrong],
        'e': [bound, missing],
    }[case]
    assert_invalid(
        await login.post(envelope=case not in ('a', 'b'), confirmations=confirmations)
    )
    assert_success(await login.post(assertion_id='_valid'))


async def test_signed_R10_bad_signature_does_not_consume(signed_login):
    assert_invalid(await signed_login.post(bad_signature=True))
    assert_success(await signed_login.post())


@pytest.mark.parametrize('case', ['a', 'b', 'c'])
async def test_signed_R6_expiry_policy(signed_login, case):
    login = signed_login
    bound = {'InResponseTo': login.request_id, 'NotOnOrAfter': stamp(900)}
    second = {'InResponseTo': login.request_id}
    if case == 'c':
        second['NotOnOrAfter'] = 'not-a-timestamp'
    assert_invalid(
        await login.post(confirmations=[second] if case == 'a' else [bound, second])
    )
    assert_success(await login.post())


@pytest.mark.parametrize(
    'issued_at,detail',
    [
        (-601, 'Expired RelayState'),
        (61, 'Expired RelayState'),
        ('nan', 'Invalid or tampered RelayState'),
        ('inf', 'Invalid or tampered RelayState'),
        ('-inf', 'Invalid or tampered RelayState'),
        (None, 'Invalid or tampered RelayState'),
    ],
)
async def test_signed_R7_relay_age(signed_login, issued_at, detail):
    import time

    login = signed_login
    payload = {'redirect_url': '/', 'request_id': login.request_id}
    if issued_at is not None:
        payload['issued_at'] = (
            time.time() + issued_at if isinstance(issued_at, int) else float(issued_at)
        )
    login.relay = provider_identity.encrypt_state(payload)
    response = await login.post()
    assert response.status_code == 400
    assert response.json()['detail'] == detail
    assert 'set-cookie' not in response.headers


async def test_signed_R8_consume_failure(signed_login):
    from sqlalchemy.exc import SQLAlchemyError

    with patch(
        'storage.saml_replay_store.SamlReplayStore.consume',
        new=AsyncMock(side_effect=SQLAlchemyError('injected')),
    ):
        response = await signed_login.post()
    assert response.status_code == 500
    assert 'set-cookie' not in response.headers
    assert_success(await signed_login.post())


async def test_signed_R9_user_failure_keeps_consumed(signed_login):
    with patch(
        'server.routes.saml.resolve_or_create_user',
        new=AsyncMock(side_effect=RuntimeError('injected')),
    ):
        response = await signed_login.post()
    assert response.status_code == 500
    assert 'set-cookie' not in response.headers
    assert_invalid(await signed_login.post())


async def test_signed_cleanup_failure_does_not_fail_login(signed_login):
    with patch(
        'storage.saml_replay_store.SamlReplayStore.delete_expired',
        new=AsyncMock(side_effect=RuntimeError('injected')),
    ):
        assert_success(await signed_login.post())
    assert_invalid(await signed_login.post())


async def test_signed_missing_assertion_id(signed_login):
    # Guard against a toolkit regression that authenticates without an assertion ID.
    with patch.object(
        saml.OneLogin_Saml2_Auth, 'get_last_assertion_id', return_value=None
    ):
        assert_invalid(await signed_login.post())
    assert_success(await signed_login.post())


async def test_signed_R11_provider_namespace(signed_login, app, async_session_maker):
    first = signed_login
    assert_success(await first.post())
    provider = await make_provider(async_session_maker)
    async with SignedLogin(app, provider) as second:
        second.request_id = first.request_id
        payload = provider_identity.decrypt_state(first.relay)
        second.relay = provider_identity.encrypt_state(payload)
        assert_success(await second.post(assertion_id='_assertion'))


@pytest.mark.parametrize('expired', [False, True])
async def test_signed_R12_cleanup(signed_login, async_session_maker, expired):
    from datetime import datetime, timezone

    from storage.saml_replay_store import SamlReplayStore
    from tests.unit.storage.test_saml_replay_store import rows

    login = signed_login
    assert_success(await login.post())
    stored = await rows(async_session_maker)
    assert len(stored) == 2
    deadline = stored[0]['expires_at'].replace(tzinfo=timezone.utc).timestamp()
    import time

    target = deadline + 1 if expired else time.time() + 60
    with freeze_time(datetime.fromtimestamp(target, timezone.utc), real_asyncio=True):
        await bounded(
            SamlReplayStore.delete_expired(
                datetime.fromtimestamp(target, timezone.utc).replace(tzinfo=None)
            )
        )
        assert len(await rows(async_session_maker)) == (0 if expired else 2)
        response = await login.post()
        assert response.status_code == 400
        assert response.json()['detail'] == (
            'Expired RelayState' if expired else 'Invalid SAML response'
        )
        assert 'set-cookie' not in response.headers


async def test_signed_P3_retains_later_bearer_expiry(signed_login, async_session_maker):
    from datetime import datetime, timezone

    from tests.unit.storage.test_saml_replay_store import rows

    login = signed_login
    later = stamp(1500)
    assert_success(
        await login.post(
            confirmations=[
                {'InResponseTo': login.request_id, 'NotOnOrAfter': stamp(900)},
                {'InResponseTo': login.request_id, 'NotOnOrAfter': later},
            ]
        )
    )
    expected = datetime.fromtimestamp(
        OneLogin_Saml2_Utils.parse_SAML_to_time(later) + 300, timezone.utc
    ).replace(tzinfo=None)
    assert {row['expires_at'] for row in await rows(async_session_maker)} == {expected}


@pytest.fixture(autouse=True)
def adapter_replay_database(request, session_maker, async_session_maker):
    """Mocked toolkit adapter tests still consume IDs in real PostgreSQL."""
    if 'signed' in request.node.name:
        yield
        return
    from storage.auth_provider import AuthProvider

    provider = _fake_saml_provider()
    with session_maker() as session:
        session.add(AuthProvider(id=1, title='Adapter IdP', details=provider.details))
        session.commit()
    with patch('storage.saml_replay_store.a_session_maker', async_session_maker):
        yield


@pytest.mark.parametrize('offset', [-600, 60])
async def test_signed_relay_age_boundary_is_accepted(signed_login, offset):
    login = signed_login
    payload = provider_identity.decrypt_state(login.relay)
    payload['issued_at'] = time.time() + offset
    login.relay = provider_identity.encrypt_state(payload)
    assert_success(await login.post())


async def test_signed_retention_covers_relay_deadline(
    signed_login, async_session_maker
):
    from datetime import datetime, timezone

    from tests.unit.storage.test_saml_replay_store import rows

    login = signed_login
    assert_success(
        await login.post(
            confirmations=[
                {'InResponseTo': login.request_id, 'NotOnOrAfter': stamp(60)},
            ]
        )
    )
    expected = datetime.fromtimestamp(
        time.time() + 600 + 60 + 300, timezone.utc
    ).replace(tzinfo=None)
    assert {row['expires_at'] for row in await rows(async_session_maker)} == {expected}


async def test_signed_empty_request_id_is_rejected(signed_login):
    login = signed_login
    payload = provider_identity.decrypt_state(login.relay)
    payload['request_id'] = ''
    login.relay = provider_identity.encrypt_state(payload)
    assert_invalid(await login.post())


async def test_signed_query_ignores_unsigned_confirmation_decoy(signed_login):
    login = signed_login
    # Unsigned XML must neither supply nor invalidate signed request binding.
    assert_success(
        await login.post(
            unsigned_confirmation={
                'InResponseTo': '_wrong',
                'NotOnOrAfter': stamp(900),
            }
        )
    )


async def test_signed_query_does_not_use_unsigned_binding(signed_login):
    login = signed_login
    assert_invalid(
        await login.post(
            envelope=False,
            confirmations=[{'InResponseTo': '_wrong', 'NotOnOrAfter': stamp(900)}],
            unsigned_confirmation={
                'InResponseTo': login.request_id,
                'NotOnOrAfter': stamp(900),
            },
        )
    )
    assert_success(await login.post())


async def test_signed_replay_after_another_login_is_rejected(signed_login, app):
    first = signed_login
    assert_success(await first.post())
    async with SignedLogin(app, first.provider) as second:
        assert second.request_id != first.request_id
        assert_success(await second.post(assertion_id='_assertion_B'))
    assert_invalid(await first.post())
