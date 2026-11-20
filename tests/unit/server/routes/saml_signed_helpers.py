"""Synthetic signed IdP responses. Never uses real assertions or credentials."""

import asyncio
import base64
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from lxml import etree
from onelogin.saml2.utils import OneLogin_Saml2_Utils

from server.auth import provider_identity
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import SamlProviderDetails, generate_sp_keypair

SAML = 'urn:oasis:names:tc:SAML:2.0:assertion'
SAMLP = 'urn:oasis:names:tc:SAML:2.0:protocol'
BEARER = 'urn:oasis:names:tc:SAML:2.0:cm:bearer'
IDP_KEY, IDP_CERT = generate_sp_keypair()


async def bounded(awaitable):
    return await asyncio.wait_for(awaitable, 10)


def stamp(seconds=0):
    return datetime.fromtimestamp(time.time() + seconds, timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ'
    )


def signed_response(
    provider_id,
    request_id,
    *,
    assertion_id='_assertion',
    envelope=True,
    confirmations=None,
    sign_response=False,
    bad_signature=False,
    unsigned_confirmation=None,
):
    acs = f'https://sp.example.com/saml/{provider_id}/acs'
    response = etree.Element(
        f'{{{SAMLP}}}Response',
        nsmap={'samlp': SAMLP, 'saml': SAML},
        ID='_response',
        Version='2.0',
        IssueInstant=stamp(),
        Destination=acs,
    )
    if envelope:
        response.set('InResponseTo', request_id)

    def child(parent, name, text=None, **attrs):
        node = etree.SubElement(parent, name, **attrs)
        node.text = text
        return node

    child(response, f'{{{SAML}}}Issuer', 'https://idp.example.com/entity')
    if unsigned_confirmation is not None:
        extension = child(response, f'{{{SAMLP}}}Extensions')
        unsigned_subject = child(extension, f'{{{SAML}}}Subject')
        confirmation = child(
            unsigned_subject, f'{{{SAML}}}SubjectConfirmation', Method=BEARER
        )
        child(
            confirmation,
            f'{{{SAML}}}SubjectConfirmationData',
            Recipient=acs,
            **unsigned_confirmation,
        )
    status = child(response, f'{{{SAMLP}}}Status')
    child(
        status,
        f'{{{SAMLP}}}StatusCode',
        Value='urn:oasis:names:tc:SAML:2.0:status:Success',
    )
    assertion = child(
        response,
        f'{{{SAML}}}Assertion',
        ID=assertion_id,
        Version='2.0',
        IssueInstant=stamp(),
    )
    child(assertion, f'{{{SAML}}}Issuer', 'https://idp.example.com/entity')
    subject = child(assertion, f'{{{SAML}}}Subject')
    child(subject, f'{{{SAML}}}NameID', 'synthetic@example.com')
    if confirmations is None:
        confirmations = [{'InResponseTo': request_id, 'NotOnOrAfter': stamp(900)}]
    for data in confirmations:
        attrs = dict(data)
        method = attrs.pop('Method', BEARER)
        sc = child(
            subject,
            f'{{{SAML}}}SubjectConfirmation',
            **({'Method': method} if method else {}),
        )
        child(sc, f'{{{SAML}}}SubjectConfirmationData', **{'Recipient': acs, **attrs})
    conditions = child(
        assertion,
        f'{{{SAML}}}Conditions',
        NotBefore=stamp(-60),
        NotOnOrAfter=stamp(3600),
    )
    audience = child(conditions, f'{{{SAML}}}AudienceRestriction')
    child(
        audience,
        f'{{{SAML}}}Audience',
        f'https://sp.example.com/saml/{provider_id}/metadata',
    )
    statement = child(
        assertion,
        f'{{{SAML}}}AuthnStatement',
        AuthnInstant=stamp(),
        SessionIndex='_session',
    )
    context = child(statement, f'{{{SAML}}}AuthnContext')
    child(
        context,
        f'{{{SAML}}}AuthnContextClassRef',
        'urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport',
    )
    attrs = child(assertion, f'{{{SAML}}}AttributeStatement')
    attr = child(attrs, f'{{{SAML}}}Attribute', Name='mail')
    child(attr, f'{{{SAML}}}AttributeValue', 'synthetic@example.com')
    signed = OneLogin_Saml2_Utils.add_sign(etree.tostring(assertion), IDP_KEY, IDP_CERT)
    response.replace(assertion, etree.fromstring(signed))
    if bad_signature:
        response.find(f'.//{{{SAML}}}NameID').text = 'tampered@example.com'
    xml = etree.tostring(response)
    if sign_response:
        xml = OneLogin_Saml2_Utils.add_sign(xml, IDP_KEY, IDP_CERT)
    return base64.b64encode(xml).decode()


async def make_provider(session_maker):
    async with session_maker() as session:
        provider = AuthProvider(
            title='Synthetic IdP',
            is_idp=True,
            enabled=True,
            details=SamlProviderDetails(
                idp_entity_id='https://idp.example.com/entity',
                sso_url='https://idp.example.com/sso',
                idp_x509_cert=IDP_CERT,
                sp_private_key=IDP_KEY,
                sp_x509_cert=IDP_CERT,
                attribute_mapping={'email': 'mail'},
            ),
        )
        session.add(provider)
        await bounded(session.commit())
        await bounded(session.refresh(provider))
        return provider


class SignedLogin:
    def __init__(self, app, provider):
        self.provider = provider
        self.path = f'/saml/{provider.id}/acs'
        self.client = AsyncClient(
            transport=ASGITransport(app, raise_app_exceptions=False),
            base_url='https://sp.example.com',
            follow_redirects=False,
        )
        self.stack = ExitStack()

    async def __aenter__(self):
        user = MagicMock(id=str(uuid4()), accepted_tos='2026-01-01')
        self.stack.enter_context(
            patch(
                'server.routes.saml.resolve_or_create_user',
                new=AsyncMock(return_value=user.id),
            )
        )
        for name, value in [
            ('is_user_disabled', False),
            ('get_user_by_id', user),
            ('grant_super_admin_if_listed', None),
        ]:
            self.stack.enter_context(
                patch(
                    f'server.routes.saml.UserStore.{name}',
                    new=AsyncMock(return_value=value),
                )
            )
        await self.client.__aenter__()
        login = await bounded(self.client.get(f'/saml/{self.provider.id}/login'))
        from urllib.parse import parse_qs, urlparse

        self.relay = parse_qs(urlparse(login.headers['location']).query)['RelayState'][
            0
        ]
        self.request_id = provider_identity.decrypt_state(self.relay)['request_id']
        self.response = signed_response(self.provider.id, self.request_id)
        return self

    async def __aexit__(self, *args):
        self.stack.close()
        await self.client.__aexit__(*args)

    async def post(self, **options):
        return await bounded(
            self.client.post(
                self.path,
                data={
                    'RelayState': self.relay,
                    'SAMLResponse': (
                        signed_response(self.provider.id, self.request_id, **options)
                        if options
                        else self.response
                    ),
                },
            )
        )
