"""Real signed/encrypted SAML protocol fixtures; no signature verifier mocks."""

import base64
import copy
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from onelogin.saml2.constants import OneLogin_Saml2_Constants as C
from onelogin.saml2.response import OneLogin_Saml2_Response
from onelogin.saml2.utils import OneLogin_Saml2_Utils

from server.auth.native_password import NativeAuthError
from server.auth.saml_config import PERSISTENT_NAME_ID, SamlSettings, get_saml_settings
from server.auth.saml_types import SamlElement
from server.services.native_saml_service import verify_saml_response
from tests.unit.server.auth.native_test_types import (
    SigningMaterial,
    XmlElement,
    present,
)

NS = {'samlp': C.NS_SAMLP, 'saml': C.NS_SAML, 'ds': C.NS_DS}


@pytest.fixture(scope='module')
def signing_material() -> SigningMaterial:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'SAML fixture')])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        cert.public_bytes(serialization.Encoding.PEM).decode(),
    )


@pytest.fixture
def saml_settings(signing_material: SigningMaterial) -> SamlSettings:
    return SamlSettings(
        'company',
        'Company SSO',
        'https://idp.example.test/issuer',
        'https://idp.example.test/login',
        'https://native.example.com/api/auth/saml/metadata',
        'https://native.example.com/api/auth/saml/acs',
        (signing_material[1],),
        allow_jit=True,
    )


def _time(delta: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=delta)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _element(
    parent: XmlElement, ns: str, tag: str, text: str | None = None, **attrs: str
) -> XmlElement:
    node: XmlElement = etree.SubElement(parent, f'{{{ns}}}{tag}', **attrs)
    node.text = text
    return node


def signed_response(
    config: SamlSettings,
    material: SigningMaterial,
    request_id: str = '_request',
    *,
    before_sign: Callable[[XmlElement], None] | None = None,
    after_sign: Callable[[XmlElement], None] | None = None,
    sign_assertion: bool = True,
    sign_response: bool = False,
    assertion_id: str | None = None,
    email: str = 'saml@example.test',
    encryption: bool = False,
    before_encryption: Callable[[XmlElement], None] | None = None,
) -> str:
    root: XmlElement = etree.Element(
        f'{{{C.NS_SAMLP}}}Response',
        nsmap=NS,
        ID=f'_response_{uuid4().hex}',
        Version='2.0',
        IssueInstant=_time(),
        Destination=config.acs_url,
        InResponseTo=request_id,
    )
    _element(root, C.NS_SAML, 'Issuer', config.issuer)
    status = _element(root, C.NS_SAMLP, 'Status')
    _element(status, C.NS_SAMLP, 'StatusCode', Value=C.STATUS_SUCCESS)
    assertion = _element(
        root,
        C.NS_SAML,
        'Assertion',
        ID=assertion_id or f'_assertion_{uuid4().hex}',
        Version='2.0',
        IssueInstant=_time(),
    )
    _element(assertion, C.NS_SAML, 'Issuer', config.issuer)
    subject = _element(assertion, C.NS_SAML, 'Subject')
    _element(
        subject,
        C.NS_SAML,
        'NameID',
        'stable-user-739',
        Format=PERSISTENT_NAME_ID,
        NameQualifier=config.issuer,
        SPNameQualifier=config.entity_id,
    )
    confirmation = _element(
        subject, C.NS_SAML, 'SubjectConfirmation', Method=C.CM_BEARER
    )
    _element(
        confirmation,
        C.NS_SAML,
        'SubjectConfirmationData',
        Recipient=config.acs_url,
        InResponseTo=request_id,
        NotOnOrAfter=_time(240),
    )
    conditions = _element(
        assertion,
        C.NS_SAML,
        'Conditions',
        NotBefore=_time(-30),
        NotOnOrAfter=_time(240),
    )
    audience = _element(conditions, C.NS_SAML, 'AudienceRestriction')
    _element(audience, C.NS_SAML, 'Audience', config.entity_id)
    authn = _element(
        assertion,
        C.NS_SAML,
        'AuthnStatement',
        AuthnInstant=_time(),
        SessionIndex='_session',
    )
    context = _element(authn, C.NS_SAML, 'AuthnContext')
    _element(context, C.NS_SAML, 'AuthnContextClassRef', C.AC_PASSWORD_PROTECTED)
    attrs = _element(assertion, C.NS_SAML, 'AttributeStatement')
    attribute = _element(attrs, C.NS_SAML, 'Attribute', Name=config.email_attribute)
    _element(attribute, C.NS_SAML, 'AttributeValue', email)
    if before_sign:
        before_sign(root)
    if sign_assertion:
        signed = OneLogin_Saml2_Utils.add_sign(
            etree.tostring(assertion),
            material[0],
            material[1],
            sign_algorithm=C.RSA_SHA256,
            digest_algorithm=C.SHA256,
        )
        root.replace(assertion, etree.fromstring(signed))
    if encryption:
        if before_encryption:
            before_encryption(root)
        encrypt_assertion(root, config.certificate)
    if sign_response:
        root = etree.fromstring(
            OneLogin_Saml2_Utils.add_sign(
                etree.tostring(root),
                material[0],
                material[1],
                sign_algorithm=C.RSA_SHA256,
                digest_algorithm=C.SHA256,
            )
        )
    if after_sign:
        after_sign(root)
    return base64.b64encode(etree.tostring(root)).decode()


def encrypt_assertion(root: XmlElement, certificate: str) -> None:
    import xmlsec

    assertion = root.find('saml:Assertion', NS)
    template = xmlsec.template.encrypted_data_create(
        root,
        xmlsec.constants.TransformAes256Cbc,
        type=xmlsec.constants.TypeEncElement,
        ns='xenc',
    )
    xmlsec.template.encrypted_data_ensure_cipher_value(template)
    info = xmlsec.template.encrypted_data_ensure_key_info(template, ns='ds')
    encrypted_key = xmlsec.template.add_encrypted_key(
        info, xmlsec.constants.TransformRsaOaep
    )
    xmlsec.template.encrypted_data_ensure_cipher_value(encrypted_key)
    manager = xmlsec.KeysManager()
    manager.add_key(
        xmlsec.Key.from_memory(certificate, xmlsec.constants.KeyDataFormatCertPem, None)
    )
    context = xmlsec.EncryptionContext(manager)
    context.key = xmlsec.Key.generate(
        xmlsec.constants.KeyDataAes, 256, xmlsec.constants.KeyDataTypeSession
    )
    encrypted = context.encrypt_xml(template, assertion)
    root.remove(encrypted)
    wrapper = _element(root, C.NS_SAML, 'EncryptedAssertion')
    wrapper.append(encrypted)


def _change(
    path: str, attribute: str, value: str | None
) -> Callable[[XmlElement], None]:
    def edit(root: XmlElement) -> None:
        node = root if path == '.' else root.find(path, NS)
        if attribute == 'text':
            present(node).text = value
        elif value is None:
            present(node).attrib.pop(attribute, None)
        else:
            present(node).set(attribute, value)

    return edit


def test_real_signed_assertion(
    saml_settings: SamlSettings, signing_material: SigningMaterial
) -> None:
    result = verify_saml_response(
        saml_settings, signed_response(saml_settings, signing_material), '_request'
    )
    assert result.email == 'saml@example.test'
    assert len(result.subject) == 64
    assert result.assertion_id != result.message_id


@pytest.mark.parametrize(
    ('path', 'attribute', 'value'),
    [
        ('.', 'Destination', 'https://native.example.com/api/auth/saml/acs/evil'),
        ('.', 'Destination', None),
        ('.', 'InResponseTo', '_wrong'),
        ('.', 'InResponseTo', None),
        ('saml:Issuer', 'text', 'https://evil.test'),
        ('saml:Assertion/saml:Issuer', 'text', 'https://evil.test'),
        (
            'saml:Assertion/saml:Conditions/saml:AudienceRestriction/saml:Audience',
            'text',
            'https://evil.test',
        ),
        (
            'saml:Assertion/saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData',
            'Recipient',
            'https://evil.test/?https://native.example.com/api/auth/saml/acs',
        ),
        (
            'saml:Assertion/saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData',
            'Recipient',
            None,
        ),
        (
            'saml:Assertion/saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData',
            'InResponseTo',
            '_wrong',
        ),
        (
            'saml:Assertion/saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData',
            'InResponseTo',
            None,
        ),
        ('saml:Assertion/saml:Subject/saml:SubjectConfirmation', 'Method', None),
        ('saml:Assertion/saml:Subject/saml:NameID', 'Format', C.NAMEID_TRANSIENT),
        ('saml:Assertion/saml:Subject/saml:NameID', 'Format', None),
        (
            'saml:Assertion/saml:Subject/saml:NameID',
            'NameQualifier',
            'https://evil.test',
        ),
        (
            'saml:Assertion/saml:Subject/saml:NameID',
            'SPNameQualifier',
            'https://evil.test',
        ),
        ('saml:Assertion/saml:Conditions', 'NotOnOrAfter', '2001-01-01T00:00:00Z'),
        ('saml:Assertion/saml:Conditions', 'NotOnOrAfter', None),
        ('saml:Assertion/saml:Conditions', 'NotBefore', '2099-01-01T00:00:00Z'),
        ('saml:Assertion', 'IssueInstant', '2001-01-01T00:00:00Z'),
        ('saml:Assertion/saml:AuthnStatement', 'AuthnInstant', '2099-01-01T00:00:00Z'),
        (
            'saml:Assertion/saml:AuthnStatement',
            'SessionNotOnOrAfter',
            '2001-01-01T00:00:00Z',
        ),
    ],
)
def test_reject_signed_invalid_profile(
    saml_settings: SamlSettings,
    signing_material: SigningMaterial,
    path: str,
    attribute: str,
    value: str | None,
) -> None:
    encoded = signed_response(
        saml_settings, signing_material, before_sign=_change(path, attribute, value)
    )
    with pytest.raises(NativeAuthError):
        verify_saml_response(saml_settings, encoded, '_request')


def test_wrong_cert_tampered_and_signed_wrapper_rejected(
    saml_settings: SamlSettings, signing_material: SigningMaterial
) -> None:
    for encoded in (
        signed_response(
            saml_settings, signing_material, sign_assertion=False, sign_response=True
        ),
        signed_response(
            saml_settings,
            signing_material,
            after_sign=_change(
                'saml:Assertion/saml:Subject/saml:NameID', 'text', 'attacker'
            ),
        ),
        signed_response(
            saml_settings,
            signing_material,
            after_sign=lambda root: root.append(
                copy.deepcopy(present(root.find('saml:Assertion', NS)))
            ),
        ),
    ):
        with pytest.raises(NativeAuthError):
            verify_saml_response(saml_settings, encoded, '_request')
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encoded = signed_response(
        saml_settings,
        (
            other_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
            signing_material[1],
        ),
    )
    with pytest.raises(NativeAuthError):
        verify_saml_response(saml_settings, encoded, '_request')


@pytest.mark.parametrize(
    ('path', 'algorithm'),
    [
        ('.//ds:Transform', 'http://www.w3.org/TR/1999/REC-xslt-19991116'),
        ('.//ds:Transform', 'http://www.w3.org/TR/1999/REC-xpath-19991116'),
        ('.//ds:SignatureMethod', C.RSA_SHA1),
        ('.//ds:DigestMethod', C.SHA1),
    ],
)
def test_transform_and_legacy_algorithm_policy(
    saml_settings: SamlSettings,
    signing_material: SigningMaterial,
    path: str,
    algorithm: str,
) -> None:
    encoded = signed_response(
        saml_settings,
        signing_material,
        after_sign=_change(path, 'Algorithm', algorithm),
    )
    with pytest.raises(NativeAuthError):
        verify_saml_response(saml_settings, encoded, '_request')


def test_external_reference_entities_and_oversize(
    saml_settings: SamlSettings, signing_material: SigningMaterial
) -> None:
    encoded = signed_response(
        saml_settings,
        signing_material,
        after_sign=_change('.//ds:Reference', 'URI', 'https://evil.test/assertion'),
    )
    for invalid in (
        encoded,
        base64.b64encode(
            b'<!DOCTYPE x [<!ENTITY boom SYSTEM "file:///etc/passwd">]><x>&boom;</x>'
        ).decode(),
        base64.b64encode(b'x' * (256 * 1024 + 1)).decode(),
        'not base64',
    ):
        with pytest.raises(NativeAuthError):
            verify_saml_response(saml_settings, invalid, '_request')


def test_real_encrypted_signed_assertion(
    saml_settings: SamlSettings, signing_material: SigningMaterial
) -> None:
    config = replace(
        saml_settings,
        certificate=signing_material[1],
        private_key=signing_material[0],
        require_encrypted_assertions=True,
    )
    encoded = signed_response(config, signing_material, encryption=True)
    assert (
        verify_saml_response(config, encoded, '_request').email == 'saml@example.test'
    )
    with pytest.raises(NativeAuthError):
        verify_saml_response(
            config, signed_response(config, signing_material), '_request'
        )
    with pytest.raises(NativeAuthError):
        verify_saml_response(replace(config, private_key=''), encoded, '_request')


def test_signed_authentication_age_preserved_and_fresh_request_enforced(
    saml_settings: SamlSettings, signing_material: SigningMaterial
) -> None:
    encoded = signed_response(
        saml_settings,
        signing_material,
        before_sign=_change(
            'saml:Assertion/saml:AuthnStatement', 'AuthnInstant', '2001-01-01T00:00:00Z'
        ),
    )
    assert (
        verify_saml_response(saml_settings, encoded, '_request').auth_time.year == 2001
    )
    with pytest.raises(NativeAuthError):
        verify_saml_response(
            saml_settings,
            encoded,
            '_request',
            require_authenticated_after=datetime.now(UTC),
        )


@pytest.mark.parametrize('subject_seconds', [240, 3600, 7200])
def test_long_idp_assertion_lifetime_does_not_change_browser_window(
    saml_settings: SamlSettings, signing_material: SigningMaterial, subject_seconds: int
) -> None:
    def lifetimes(root: XmlElement) -> None:
        _change('saml:Assertion/saml:Conditions', 'NotOnOrAfter', _time(3600))(root)
        _change(
            'saml:Assertion/saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData',
            'NotOnOrAfter',
            _time(subject_seconds),
        )(root)

    result = verify_saml_response(
        saml_settings,
        signed_response(saml_settings, signing_material, before_sign=lifetimes),
        '_request',
    )
    now = datetime.now(UTC)
    assert result.expires_at <= now + timedelta(seconds=min(3600, subject_seconds))
    assert result.replay_expires_at > now + timedelta(
        seconds=max(3600, subject_seconds) + 55
    )


def test_encrypted_external_references_rejected_before_decryption(
    saml_settings: SamlSettings,
    signing_material: SigningMaterial,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(
        saml_settings, certificate=signing_material[1], private_key=signing_material[0]
    )
    decrypted = []
    original = OneLogin_Saml2_Response._decrypt_assertion

    def tracked_decrypt(
        self: OneLogin_Saml2_Response, document: SamlElement
    ) -> SamlElement:
        decrypted.append(True)
        return original(self, document)

    monkeypatch.setattr(OneLogin_Saml2_Response, '_decrypt_assertion', tracked_decrypt)
    for tag, namespace in (
        ('CipherReference', C.NS_XENC),
        ('RetrievalMethod', C.NS_DS),
    ):

        def add_external_reference(
            root: XmlElement, namespace: str = namespace, tag: str = tag
        ) -> None:
            _element(
                root, namespace, tag, URI='https://untrusted.example.test/external'
            )

        encoded = signed_response(
            config,
            signing_material,
            encryption=True,
            after_sign=add_external_reference,
        )
        with pytest.raises(NativeAuthError):
            verify_saml_response(config, encoded, '_request')
    assert decrypted == []


def test_encrypted_xslt_rejected_before_signature_verification(
    saml_settings: SamlSettings,
    signing_material: SigningMaterial,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(
        saml_settings, certificate=signing_material[1], private_key=signing_material[0]
    )
    verified = []
    original = OneLogin_Saml2_Utils.validate_sign

    def tracked_verify(
        xml: str | bytes | SamlElement,
        cert: str | None = None,
        fingerprint: str | None = None,
        fingerprintalg: str = 'sha1',
        validatecert: bool = False,
        debug: bool = False,
        xpath: str | None = None,
        multicerts: list[str] | None = None,
        raise_exceptions: bool = False,
    ) -> bool:
        verified.append(True)
        return original(
            xml,
            cert,
            fingerprint,
            fingerprintalg,
            validatecert,
            debug,
            xpath,
            multicerts,
            raise_exceptions=raise_exceptions,
        )

    monkeypatch.setattr(OneLogin_Saml2_Utils, 'validate_sign', tracked_verify)
    encoded = signed_response(
        config,
        signing_material,
        encryption=True,
        before_encryption=_change(
            './/ds:Transform',
            'Algorithm',
            'http://www.w3.org/TR/1999/REC-xslt-19991116',
        ),
    )
    with pytest.raises(NativeAuthError):
        verify_saml_response(config, encoded, '_request')
    assert verified == []
    assert verify_saml_response(
        config, signed_response(config, signing_material, encryption=True), '_request'
    )
    assert verified


def test_config_flags_metadata_and_signed_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signing_material: SigningMaterial
) -> None:
    from onelogin.saml2.auth import OneLogin_Saml2_Auth
    from onelogin.saml2.settings import OneLogin_Saml2_Settings

    from server.auth.auth_config import get_native_auth_settings
    from server.services.native_saml_service import _request_data

    cert = tmp_path / 'cert.pem'
    key = tmp_path / 'key.pem'
    cert.write_text(signing_material[1])
    key.write_text(signing_material[0])
    for name, value in {
        'NATIVE_AUTH_APP_ORIGIN': 'https://native.example.com',
        'NATIVE_SAML_ENABLED': '1',
        'NATIVE_SAML_IDP_ENTITY_ID': 'https://idp.example.test/issuer',
        'NATIVE_SAML_IDP_SSO_URL': 'https://idp.example.test/login',
        'NATIVE_SAML_IDP_CERT_FILE': str(cert),
        'NATIVE_SAML_SP_CERT_FILE': str(cert),
        'NATIVE_SAML_SP_KEY_FILE': str(key),
        'NATIVE_SAML_SIGN_REQUESTS': '1',
    }.items():
        monkeypatch.setenv(name, value)
    get_native_auth_settings.cache_clear()
    get_saml_settings.cache_clear()
    try:
        config = get_saml_settings()
        assert config and config.sign_requests and config.allow_jit
        toolkit = OneLogin_Saml2_Settings(config.toolkit_settings())
        metadata = toolkit.get_sp_metadata()
        assert toolkit.validate_metadata(metadata) == []
        assert config.acs_url.encode() in metadata
        auth = OneLogin_Saml2_Auth(
            _request_data(config), old_settings=config.toolkit_settings()
        )
        raw_query = urlsplit(auth.login(return_to='test')).query
        query = parse_qs(raw_query)
        assert query['SigAlg'] == [C.RSA_SHA256]
        assert query['Signature']
        from cryptography.hazmat.primitives.asymmetric import padding

        public_key = x509.load_pem_x509_certificate(
            signing_material[1].encode()
        ).public_key()
        assert isinstance(public_key, rsa.RSAPublicKey)
        public_key.verify(
            base64.b64decode(query['Signature'][0]),
            '&'.join(
                piece
                for name in ('SAMLRequest', 'RelayState', 'SigAlg')
                for piece in raw_query.split('&')
                if piece.startswith(name + '=')
            ).encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        monkeypatch.setenv('NATIVE_SAML_JIT_ENABLED', '0')
        monkeypatch.setenv('NATIVE_SAML_REQUIRE_ENCRYPTED_ASSERTIONS', '1')
        get_saml_settings.cache_clear()
        config = get_saml_settings()
        assert config and not config.allow_jit and config.require_encrypted_assertions
        cert.write_text('not a certificate')
        get_saml_settings.cache_clear()
        with pytest.raises(ValueError, match='certificate'):
            get_saml_settings()
    finally:
        get_saml_settings.cache_clear()
        get_native_auth_settings.cache_clear()


@pytest.mark.parametrize('flag', ['true', '1'])
@pytest.mark.parametrize('app_host', ['localhost', '127.0.0.1', '[::1]'])
@pytest.mark.parametrize('idp_host', ['localhost', '127.0.0.1', '[::1]'])
def test_saml_config_allows_explicit_local_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signing_material: SigningMaterial,
    flag: str,
    app_host: str,
    idp_host: str,
) -> None:
    from server.auth.auth_config import get_native_auth_settings

    cert = tmp_path / 'idp.pem'
    cert.write_text(signing_material[1])
    app_origin = f'http://{app_host}:13000'
    sso_url = f'http://{idp_host}:4000/saml/sso'
    for name, value in {
        'NATIVE_AUTH_APP_ORIGIN': app_origin,
        'NATIVE_AUTH_ALLOW_INSECURE_LOCALHOST': flag,
        'NATIVE_SAML_ENABLED': 'true',
        'NATIVE_SAML_IDP_ENTITY_ID': 'urn:test:local-idp',
        'NATIVE_SAML_IDP_SSO_URL': sso_url,
        'NATIVE_SAML_IDP_CERT_FILE': str(cert),
    }.items():
        monkeypatch.setenv(name, value)
    get_native_auth_settings.cache_clear()
    get_saml_settings.cache_clear()
    try:
        config = get_saml_settings()
        assert config is not None
        assert config.sso_url == sso_url
        assert config.acs_url == f'{app_origin}/api/auth/saml/acs'
        assert config.toolkit_settings()['security']['wantAssertionsSigned'] is True
        assert config.toolkit_settings()['sp']['NameIDFormat'] == PERSISTENT_NAME_ID
    finally:
        get_saml_settings.cache_clear()
        get_native_auth_settings.cache_clear()


@pytest.mark.parametrize(
    'app_origin,flag,sso_url',
    [
        ('http://localhost:13000', 'false', 'http://localhost:4000/saml/sso'),
        ('http://localhost:13000', '0', 'http://localhost:4000/saml/sso'),
        ('https://native.example.com', 'true', 'http://localhost:4000/saml/sso'),
        ('http://native.example.com', 'true', 'http://localhost:4000/saml/sso'),
        ('https://localhost:13000', 'true', 'http://localhost:4000/saml/sso'),
        *[
            ('http://localhost:13000', 'true', sso_url)
            for sso_url in (
                'http://idp.example.com/saml/sso',
                'http://localhost.example.com/saml/sso',
                'http://127.0.0.2:4000/saml/sso',
                'http://0.0.0.0:4000/saml/sso',
                'http://localhost.:4000/saml/sso',
                'http://user@localhost:4000/saml/sso',
                'http://user:password@localhost:4000/saml/sso',
                'http://localhost:4000/saml/sso?redirect=elsewhere',
                'http://localhost:4000/saml/sso#fragment',
                'http://localhost:invalid/saml/sso',
                'http://localhost:65536/saml/sso',
                'ftp://localhost:4000/saml/sso',
                '//localhost:4000/saml/sso',
                'https://user@idp.example.com/saml/sso',
                'https://idp.example.com/saml/sso?redirect=elsewhere',
                'https://idp.example.com/saml/sso#fragment',
            )
        ],
    ],
)
def test_saml_config_rejects_unsafe_idp_urls(
    monkeypatch: pytest.MonkeyPatch, app_origin: str, flag: str, sso_url: str
) -> None:
    from server.auth.auth_config import get_native_auth_settings

    monkeypatch.setenv('NATIVE_SAML_ENABLED', '1')
    monkeypatch.setenv('NATIVE_AUTH_APP_ORIGIN', app_origin)
    monkeypatch.setenv('NATIVE_AUTH_ALLOW_INSECURE_LOCALHOST', flag)
    monkeypatch.setenv('NATIVE_SAML_IDP_ENTITY_ID', 'urn:test:local-idp')
    monkeypatch.setenv('NATIVE_SAML_IDP_SSO_URL', sso_url)
    get_native_auth_settings.cache_clear()
    get_saml_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match='NATIVE_|Port'):
            get_saml_settings()
    finally:
        get_saml_settings.cache_clear()
        get_native_auth_settings.cache_clear()


def test_disabled_saml_imports_no_xmlsec_and_constructs_no_keycloak_clients() -> None:
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(
            (
                'KEYCLOAK_',
                'OH_',
                'OPENHANDS_',
                'NATIVE_',
                'GITHUB_APP_',
                'GITLAB_APP_',
                'BITBUCKET_APP_',
            )
        )
    }
    env.update(
        ENABLE_KEYCLOAK='false',
        NATIVE_AUTH_APP_ORIGIN='https://native.example.com',
        NATIVE_SAML_ENABLED='false',
    )
    code = """
import importlib.abc
from importlib.machinery import ModuleSpec
from collections.abc import Sequence
from types import ModuleType
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None) -> ModuleSpec | None:
        if fullname.split('.')[0] in ('xmlsec', 'onelogin'):
            raise ImportError('Disabled authentication dependency imported: ' + fullname)
sys.meta_path.insert(0, Block())
from unittest.mock import patch
with patch('keycloak.keycloak_admin.KeycloakAdmin', side_effect=AssertionError('Keycloak initialized')), patch('keycloak.keycloak_openid.KeycloakOpenID', side_effect=AssertionError('Keycloak initialized')):
    import saas_server
from server.config import get_auth_capabilities
assert get_auth_capabilities()['login_methods'] == ['password']
assert 'xmlsec' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, '-c', code],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-5000:]


def test_keycloak_capabilities_ignore_invalid_saml_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server import config

    monkeypatch.setattr(config, 'ENABLE_KEYCLOAK', True)
    monkeypatch.setenv('NATIVE_SAML_ENABLED', 'bad value')
    config.validate_native_auth_configuration()
    assert config.get_auth_capabilities([])['login_methods'] == []


@pytest.mark.asyncio
async def test_both_capability_endpoints_advertise_same_saml(
    saml_settings: SamlSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock, patch

    from openhands.app_server.web_client.default_web_client_config_injector import (
        DefaultWebClientConfigInjector,
    )
    from server import config
    from server.auth import auth_config, saml_config

    monkeypatch.setattr(config, 'ENABLE_KEYCLOAK', False)
    monkeypatch.setattr(auth_config, 'ENABLE_KEYCLOAK', False)
    monkeypatch.setattr(saml_config, 'get_saml_settings', lambda: saml_settings)
    with (
        patch.object(config.SaaSServerConfig, '_get_app_slug', return_value=None),
        patch(
            'openhands.app_server.web_client.default_web_client_config_injector._get_db_feature_flags',
            new=AsyncMock(return_value={}),
        ),
        patch(
            'openhands.app_server.web_client.default_web_client_config_injector._resolve_flag',
            new=AsyncMock(return_value=False),
        ),
    ):
        legacy = config.SaaSServerConfig().get_config()
        modern = await DefaultWebClientConfigInjector().get_web_client_config()
    assert legacy['login_methods'] == modern.login_methods == ['password', 'saml']
    assert (
        legacy['saml']
        == modern.saml
        == {'connection_id': 'company', 'name': 'Company SSO'}
    )
