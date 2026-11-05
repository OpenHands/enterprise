"""Fetch and parse a SAML 2.0 IdP metadata document.

Backs the "Metadata URL" field in the admin SAML provider form
(``server.routes.oauth_providers_admin.preview_saml_metadata``): instead of an
admin hand-copying the entity ID, SSO/SLO URLs, and signing certificate out of
an IdP's metadata XML, they paste the metadata URL and this fetches + parses
it server-side. The fetch happens here rather than in the browser so it
sidesteps CORS on third-party IdP metadata endpoints and keeps the outbound
request behind the ``MANAGE_OAUTH_PROVIDERS`` permission (no browser-originated
SSRF-style fetch of an admin-supplied URL).
"""

from __future__ import annotations

import textwrap

import httpx
from defusedxml import ElementTree as DefusedElementTree
from pydantic import BaseModel

from openhands.app_server.utils.http_session import httpx_verify_option
from openhands.app_server.utils.logger import openhands_logger as logger

SAML_METADATA_HTTP_TIMEOUT = 15.0
# Guards against a slow/malicious endpoint streaming an unbounded response --
# real IdP metadata documents are a few KB to a few hundred KB.
MAX_METADATA_BYTES = 5 * 1024 * 1024

_MD_NS = 'urn:oasis:names:tc:SAML:2.0:metadata'
_DS_NS = 'http://www.w3.org/2000/09/xmldsig#'
_NAMESPACES = {'md': _MD_NS, 'ds': _DS_NS}

_REDIRECT_BINDING = 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect'
_POST_BINDING = 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST'


class SamlMetadataFetchError(Exception):
    """The metadata URL could not be fetched or did not parse as IdP metadata."""


class SamlIdpMetadata(BaseModel):
    """The subset of an IdP's metadata document the SAML form can prefill.

    Mirrors the matching fields on ``storage.auth_provider_details.
    SamlProviderDetails`` -- any of these may be ``None`` if the metadata
    document didn't declare them (e.g. no ``SingleLogoutService``).
    """

    idp_entity_id: str | None = None
    sso_url: str | None = None
    sso_binding: str | None = None
    slo_url: str | None = None
    idp_x509_cert: str | None = None
    name_id_format: str | None = None


async def fetch_saml_idp_metadata(metadata_url: str) -> SamlIdpMetadata:
    """Fetch ``metadata_url`` and parse it as a SAML 2.0 IdP metadata document.

    Raises ``SamlMetadataFetchError`` on any failure (bad scheme, network
    error, oversized response, unparseable/non-IdP XML) with a message safe
    to return to the admin triggering the request.
    """
    if not metadata_url.startswith('https://'):
        raise SamlMetadataFetchError('Metadata URL must start with https://')
    try:
        async with httpx.AsyncClient(
            verify=httpx_verify_option(), timeout=SAML_METADATA_HTTP_TIMEOUT
        ) as client:
            response = await client.get(metadata_url)
            response.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning(
            'saml_metadata:fetch_failed',
            extra={'metadata_url': metadata_url, 'error': str(e)},
        )
        raise SamlMetadataFetchError(f'Failed to fetch metadata URL: {e}') from e
    if len(response.content) > MAX_METADATA_BYTES:
        raise SamlMetadataFetchError('Metadata document is too large')
    return parse_saml_idp_metadata(response.content)


def parse_saml_idp_metadata(xml_bytes: bytes) -> SamlIdpMetadata:
    """Parse a SAML metadata XML document's ``IDPSSODescriptor``.

    Uses ``defusedxml`` (not the stdlib ``xml.etree``) since this parses
    content fetched from an admin-supplied, potentially untrusted URL --
    this guards against XXE/billion-laughs payloads in a malicious or
    compromised "IdP" endpoint.
    """
    try:
        root = DefusedElementTree.fromstring(xml_bytes)
    except Exception as e:
        raise SamlMetadataFetchError(f'Could not parse metadata XML: {e}') from e

    entity = root
    if root.tag == f'{{{_MD_NS}}}EntitiesDescriptor':
        # A metadata document may describe several entities -- use the first
        # one that is actually an IdP.
        found = root.find('.//md:EntityDescriptor[md:IDPSSODescriptor]', _NAMESPACES)
        if found is None:
            raise SamlMetadataFetchError(
                'No IdP entity descriptor found in metadata document'
            )
        entity = found

    idp_sso_descriptor = entity.find('md:IDPSSODescriptor', _NAMESPACES)
    if idp_sso_descriptor is None:
        raise SamlMetadataFetchError(
            "Metadata document has no IDPSSODescriptor -- is this the IdP's "
            "metadata URL (not the SP's)?"
        )

    sso_url, sso_binding = _pick_endpoint(idp_sso_descriptor, 'SingleSignOnService')
    slo_url, _ = _pick_endpoint(idp_sso_descriptor, 'SingleLogoutService')
    name_id_format_el = idp_sso_descriptor.find('md:NameIDFormat', _NAMESPACES)

    return SamlIdpMetadata(
        idp_entity_id=entity.get('entityID'),
        sso_url=sso_url,
        sso_binding=sso_binding,
        slo_url=slo_url,
        idp_x509_cert=_find_signing_certificate(idp_sso_descriptor),
        name_id_format=(
            name_id_format_el.text.strip()
            if name_id_format_el is not None and name_id_format_el.text
            else None
        ),
    )


def _pick_endpoint(descriptor, tag: str) -> tuple[str | None, str | None]:
    """Pick the HTTP-Redirect endpoint for ``tag`` if present, else HTTP-POST.

    IdPs commonly advertise both bindings for the same service; redirect is
    the simpler/more common default for ``SamlProviderDetails.sso_binding``.
    """
    by_binding = {
        service.get('Binding'): service.get('Location')
        for service in descriptor.findall(f'md:{tag}', _NAMESPACES)
    }
    if _REDIRECT_BINDING in by_binding:
        return by_binding[_REDIRECT_BINDING], 'redirect'
    if _POST_BINDING in by_binding:
        return by_binding[_POST_BINDING], 'post'
    return None, None


def _find_signing_certificate(idp_sso_descriptor) -> str | None:
    """Find the IdP's signing certificate, preferring an explicit
    ``use="signing"`` ``KeyDescriptor`` but falling back to one with no
    ``use`` attribute (valid for both signing and encryption)."""
    for key_descriptor in idp_sso_descriptor.findall('md:KeyDescriptor', _NAMESPACES):
        if key_descriptor.get('use') not in (None, 'signing'):
            continue
        cert_el = key_descriptor.find(
            'ds:KeyInfo/ds:X509Data/ds:X509Certificate', _NAMESPACES
        )
        if cert_el is not None and cert_el.text:
            return _to_pem(cert_el.text)
    return None


def _to_pem(base64_cert: str) -> str:
    """Metadata documents embed the certificate as bare base64 -- wrap it
    back into the PEM form ``SamlProviderDetails.idp_x509_cert`` expects."""
    body = ''.join(base64_cert.split())
    wrapped = '\n'.join(textwrap.wrap(body, 64))
    return f'-----BEGIN CERTIFICATE-----\n{wrapped}\n-----END CERTIFICATE-----'
