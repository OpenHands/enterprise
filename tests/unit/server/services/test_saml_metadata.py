"""Unit tests for ``server.services.saml_metadata``.

Covers XML parsing of a SAML IdP metadata document (``parse_saml_idp_metadata``)
and the server-side fetch wrapper (``fetch_saml_idp_metadata``), including its
https-only, response-size, and HTTP-error guards.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from server.services.saml_metadata import (
    MAX_METADATA_BYTES,
    SamlMetadataFetchError,
    fetch_saml_idp_metadata,
    parse_saml_idp_metadata,
)

_ENTITY_ID = 'https://idp.example.com/realms/allhands'
_SSO_URL = 'https://idp.example.com/realms/allhands/protocol/saml'
_CERT_BODY = 'MIIDUjCCAjqgAwIBAgIUtest1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij=='

_FULL_METADATA = f"""<?xml version="1.0" encoding="UTF-8"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
    entityID="{_ENTITY_ID}">
  <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:KeyDescriptor use="signing">
      <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
        <ds:X509Data>
          <ds:X509Certificate>{_CERT_BODY}</ds:X509Certificate>
        </ds:X509Data>
      </ds:KeyInfo>
    </md:KeyDescriptor>
    <md:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location="{_SSO_URL}"/>
    <md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified</md:NameIDFormat>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
        Location="{_SSO_URL}"/>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location="{_SSO_URL}"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>
"""


class TestParseSamlIdpMetadata:
    def test_parses_entity_id_sso_slo_cert_and_name_id_format(self):
        result = parse_saml_idp_metadata(_FULL_METADATA.encode())

        assert result.idp_entity_id == _ENTITY_ID
        assert result.sso_url == _SSO_URL
        assert result.slo_url == _SSO_URL
        assert (
            result.name_id_format
            == 'urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified'
        )
        assert result.idp_x509_cert is not None
        assert result.idp_x509_cert.startswith('-----BEGIN CERTIFICATE-----\n')
        assert result.idp_x509_cert.endswith('\n-----END CERTIFICATE-----')
        # Round-trips back to the metadata's bare base64 once PEM wrapping
        # (headers + 64-char line folding) is undone.
        body = (
            result.idp_x509_cert.removeprefix('-----BEGIN CERTIFICATE-----\n')
            .removesuffix('\n-----END CERTIFICATE-----')
            .replace('\n', '')
        )
        assert body == _CERT_BODY

    def test_prefers_redirect_binding_over_post(self):
        result = parse_saml_idp_metadata(_FULL_METADATA.encode())
        assert result.sso_binding == 'redirect'

    def test_falls_back_to_post_binding_when_no_redirect(self):
        xml = f"""<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
            entityID="{_ENTITY_ID}">
          <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
            <md:SingleSignOnService
                Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
                Location="{_SSO_URL}"/>
          </md:IDPSSODescriptor>
        </md:EntityDescriptor>"""
        result = parse_saml_idp_metadata(xml.encode())
        assert result.sso_binding == 'post'
        assert result.sso_url == _SSO_URL

    def test_entities_descriptor_wrapper_finds_the_idp_entity(self):
        xml = f"""<md:EntitiesDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata">
          <md:EntityDescriptor entityID="https://sp.example.com">
            <md:SPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol"/>
          </md:EntityDescriptor>
          <md:EntityDescriptor entityID="{_ENTITY_ID}">
            <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
              <md:SingleSignOnService
                  Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
                  Location="{_SSO_URL}"/>
            </md:IDPSSODescriptor>
          </md:EntityDescriptor>
        </md:EntitiesDescriptor>"""
        result = parse_saml_idp_metadata(xml.encode())
        assert result.idp_entity_id == _ENTITY_ID
        assert result.sso_url == _SSO_URL

    def test_missing_idp_sso_descriptor_raises(self):
        xml = f"""<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
            entityID="{_ENTITY_ID}">
          <md:SPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol"/>
        </md:EntityDescriptor>"""
        with pytest.raises(SamlMetadataFetchError, match='IDPSSODescriptor'):
            parse_saml_idp_metadata(xml.encode())

    def test_malformed_xml_raises(self):
        with pytest.raises(SamlMetadataFetchError, match='Could not parse'):
            parse_saml_idp_metadata(b'not xml at all <<<')

    def test_no_key_descriptor_leaves_cert_none(self):
        xml = f"""<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
            entityID="{_ENTITY_ID}">
          <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
            <md:SingleSignOnService
                Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
                Location="{_SSO_URL}"/>
          </md:IDPSSODescriptor>
        </md:EntityDescriptor>"""
        result = parse_saml_idp_metadata(xml.encode())
        assert result.idp_x509_cert is None


class TestFetchSamlIdpMetadata:
    @pytest.mark.asyncio
    async def test_rejects_non_https_url(self):
        with pytest.raises(SamlMetadataFetchError, match='https://'):
            await fetch_saml_idp_metadata('http://idp.example.com/metadata')

    @pytest.mark.asyncio
    async def test_fetches_and_parses_on_success(self):
        response = MagicMock()
        response.content = _FULL_METADATA.encode()
        response.raise_for_status = MagicMock()
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=response)
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=False)

        with patch(
            'server.services.saml_metadata.httpx.AsyncClient',
            return_value=client_mock,
        ):
            result = await fetch_saml_idp_metadata('https://idp.example.com/metadata')

        assert result.idp_entity_id == _ENTITY_ID
        client_mock.get.assert_awaited_once_with('https://idp.example.com/metadata')

    @pytest.mark.asyncio
    async def test_oversized_response_raises(self):
        response = MagicMock()
        response.content = b'x' * (MAX_METADATA_BYTES + 1)
        response.raise_for_status = MagicMock()
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(return_value=response)
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=False)

        with patch(
            'server.services.saml_metadata.httpx.AsyncClient',
            return_value=client_mock,
        ):
            with pytest.raises(SamlMetadataFetchError, match='too large'):
                await fetch_saml_idp_metadata('https://idp.example.com/metadata')

    @pytest.mark.asyncio
    async def test_http_error_raises_fetch_error(self):
        client_mock = AsyncMock()
        client_mock.get = AsyncMock(
            side_effect=httpx.ConnectError('connection refused')
        )
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=False)

        with patch(
            'server.services.saml_metadata.httpx.AsyncClient',
            return_value=client_mock,
        ):
            with pytest.raises(SamlMetadataFetchError, match='Failed to fetch'):
                await fetch_saml_idp_metadata('https://idp.example.com/metadata')
