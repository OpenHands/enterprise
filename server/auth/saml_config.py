"""Operator-owned SAML trust. No discovery or request-supplied metadata URLs."""

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from server.auth.auth_config import get_native_auth_settings, parse_enable_keycloak
from server.auth.saml_types import SamlToolkitSettings

SAML_PREFIX = '/api/auth/saml'
PERSISTENT_NAME_ID = 'urn:oasis:names:tc:SAML:2.0:nameid-format:persistent'


@dataclass(frozen=True)
class SamlSettings:
    connection_id: str
    name: str
    issuer: str
    sso_url: str
    entity_id: str
    acs_url: str
    certificates: tuple[str, ...] = field(repr=False)
    email_attribute: str = 'email'
    certificate: str = field(default='', repr=False)
    private_key: str = field(default='', repr=False)
    sign_requests: bool = False
    require_encrypted_assertions: bool = False
    allow_jit: bool = False

    def toolkit_settings(self) -> SamlToolkitSettings:
        return {
            'strict': True,
            'debug': False,
            'sp': {
                'entityId': self.entity_id,
                'assertionConsumerService': {
                    'url': self.acs_url,
                    'binding': 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST',
                },
                'NameIDFormat': PERSISTENT_NAME_ID,
                'x509cert': self.certificate,
                'privateKey': self.private_key,
            },
            'idp': {
                'entityId': self.issuer,
                'singleSignOnService': {
                    'url': self.sso_url,
                    'binding': 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect',
                },
                'x509certMulti': {'signing': list(self.certificates)},
            },
            'security': {
                'authnRequestsSigned': self.sign_requests,
                'wantAssertionsSigned': True,
                'wantMessagesSigned': False,
                'wantAssertionsEncrypted': self.require_encrypted_assertions,
                'wantNameId': True,
                'wantAttributeStatement': True,
                'requestedAuthnContext': False,
                'rejectUnsolicitedResponsesWithInResponseTo': True,
                'rejectDeprecatedAlgorithm': True,
                'signatureAlgorithm': 'http://www.w3.org/2001/04/xmldsig-more#rsa-sha256',
                'digestAlgorithm': 'http://www.w3.org/2001/04/xmlenc#sha256',
            },
        }


def _read(name: str) -> str:
    path = os.getenv(name)
    if not path:
        raise ValueError(f'{name} must name a mounted PEM file')
    try:
        value = Path(path).read_text()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f'{name} cannot be read') from exc
    if len(value) > 65536:
        raise ValueError(f'{name} is too large')
    return value


def _certificates(value: str) -> tuple[str, ...]:
    try:
        certs = x509.load_pem_x509_certificates(value.encode())
        if not 1 <= len(certs) <= 8:
            raise ValueError('Expected 1 to 8 certificates')
        for cert in certs:
            key = cert.public_key()
            if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
                raise ValueError('SAML signing certificates require RSA 2048 or better')
        return tuple(
            cert.public_bytes(serialization.Encoding.PEM).decode() for cert in certs
        )
    except ValueError as exc:
        raise ValueError(
            'SAML certificate file must contain trusted RSA PEM certificates'
        ) from exc


@lru_cache(maxsize=1)
def get_saml_settings() -> SamlSettings | None:
    if not parse_enable_keycloak(os.getenv('NATIVE_SAML_ENABLED', 'false')):
        return None
    connection = os.getenv('NATIVE_SAML_CONNECTION_ID', 'saml')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', connection):
        raise ValueError('NATIVE_SAML_CONNECTION_ID must be a stable identifier')
    native = get_native_auth_settings()
    origin = native.app_origin
    issuer = os.getenv('NATIVE_SAML_IDP_ENTITY_ID', '')
    entity = os.getenv('NATIVE_SAML_SP_ENTITY_ID', f'{origin}{SAML_PREFIX}/metadata')
    for name, value in (
        ('NATIVE_SAML_IDP_ENTITY_ID', issuer),
        ('NATIVE_SAML_SP_ENTITY_ID', entity),
    ):
        if not value or len(value) > 512 or not urlsplit(value).scheme:
            raise ValueError(
                f'{name} must be an absolute identifier of at most 512 characters'
            )
    sso = os.getenv('NATIVE_SAML_IDP_SSO_URL', '')
    parsed = urlsplit(sso)
    app_origin = urlsplit(origin)
    loopback_hosts = ('localhost', '127.0.0.1', '::1')
    insecure_localhost = (
        native.allow_insecure_localhost
        and app_origin.scheme == 'http'
        and app_origin.hostname in loopback_hosts
        and parsed.scheme == 'http'
        and parsed.hostname in loopback_hosts
    )
    if (
        (parsed.scheme != 'https' and not insecure_localhost)
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
    ):
        raise ValueError(
            'NATIVE_SAML_IDP_SSO_URL must be a trusted HTTPS URL without query '
            '(or explicitly enabled localhost HTTP with a localhost HTTP app origin)'
        )
    parsed.port
    certs = _certificates(_read('NATIVE_SAML_IDP_CERT_FILE'))
    certificate, key = '', ''
    if os.getenv('NATIVE_SAML_SP_CERT_FILE') or os.getenv('NATIVE_SAML_SP_KEY_FILE'):
        sp_certs = _certificates(_read('NATIVE_SAML_SP_CERT_FILE'))
        if len(sp_certs) != 1:
            raise ValueError(
                'NATIVE_SAML_SP_CERT_FILE must contain exactly one certificate'
            )
        certificate = sp_certs[0]
        key = _read('NATIVE_SAML_SP_KEY_FILE')
        try:
            private = serialization.load_pem_private_key(key.encode(), password=None)
            public = x509.load_pem_x509_certificate(certificate.encode()).public_key()
            if (
                not isinstance(private, rsa.RSAPrivateKey)
                or not isinstance(public, rsa.RSAPublicKey)
                or private.public_key().public_numbers() != public.public_numbers()
            ):
                raise ValueError('Mismatched key')
        except (ValueError, TypeError) as exc:
            raise ValueError('SAML SP private key must match its certificate') from exc
    signed = parse_enable_keycloak(os.getenv('NATIVE_SAML_SIGN_REQUESTS', 'false'))
    encrypted = parse_enable_keycloak(
        os.getenv('NATIVE_SAML_REQUIRE_ENCRYPTED_ASSERTIONS', 'false')
    )
    if (signed or encrypted) and not key:
        raise ValueError(
            'SAML signing/encryption requires the SP certificate and private key'
        )
    name = os.getenv('NATIVE_SAML_DISPLAY_NAME', 'SSO').strip()
    attribute = os.getenv('NATIVE_SAML_EMAIL_ATTRIBUTE', 'email').strip()
    if not name or len(name) > 100 or not attribute or len(attribute) > 512:
        raise ValueError('Invalid SAML display name or email attribute')
    settings = SamlSettings(
        connection,
        name,
        issuer,
        sso,
        entity,
        f'{origin}{SAML_PREFIX}/acs',
        certs,
        attribute,
        certificate,
        key,
        signed,
        encrypted,
        parse_enable_keycloak(os.getenv('NATIVE_SAML_JIT_ENABLED', 'true')),
    )
    # Fail startup if the configured XML/security backend cannot be imported.
    from onelogin.saml2.settings import OneLogin_Saml2_Settings

    toolkit = OneLogin_Saml2_Settings(settings.toolkit_settings())
    if toolkit.validate_metadata(toolkit.get_sp_metadata()):
        raise ValueError('Invalid SAML service provider metadata')
    return settings


if __name__ == '__main__':
    # Deployment capability/configuration gate, deliberately without DB access.
    from server.auth.auth_config import ENABLE_KEYCLOAK

    if not ENABLE_KEYCLOAK:
        get_saml_settings()
