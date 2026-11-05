"""SAML 2.0 SP-initiated login, ACS, and SP metadata routes.

Plugs SAML into the OAuth v2 login surface (``server.routes.oauth_v2``) as a
third provider kind alongside OAuth2 and the integrated password IDP:

- ``GET /saml/{provider_id}/login`` -- SP-initiated start. Builds a signed
  AuthnRequest and redirects the browser to the IdP's SSO URL.
- ``POST /saml/{provider_id}/acs`` -- Assertion Consumer Service: the IdP's
  auto-submitting form posts the SAMLResponse here. Validates it, resolves
  or creates the user (``server.auth.provider_identity.
  resolve_or_create_user``), and sets the same ``openhands_auth`` cookie
  every other IDP kind sets.
- ``GET /saml/{provider_id}/metadata`` -- unauthenticated SP metadata
  document (entity id, ACS URL, ``sp_x509_cert``) for the admin/IdP side of
  the trust relationship.

``/oauth/{provider_id}/login`` redirects here for a SAML-kind provider;
``/oauth/{provider_id}/callback`` 404s one (SAML's ACS is POST, not GET) --
see that module's docstring for the shared-kind dispatch this plugs into.

**Only SP-initiated login is supported** -- a ``POST`` to the ACS with no
(or an invalid) ``RelayState`` is rejected outright, rather than accepted as
an unsolicited, IdP-initiated response. IdP-initiated SSO is a real SAML
mode some IdPs default to, but it trades away ``InResponseTo`` replay
protection entirely; supporting it would be a deliberate, separately
reviewed addition, not a side effect of being lenient here.

**RelayState, not a cookie, carries state across the IdP redirect.** The
IdP's ACS POST is a cross-site top-level navigation, and
``SameSite=Lax/Strict`` cookies (the only values ``get_cookie_samesite()``
ever returns) are not sent on cross-site POSTs -- so this flow never reads a
cookie set before redirecting to the IdP, only ever *sets* one at the end of
ACS (same as the OAuth callback). Everything the ACS needs back -- the
post-login redirect URL and the original AuthnRequest's id (for
``InResponseTo`` validation) -- travels instead in an encrypted
``RelayState`` blob built with ``server.auth.provider_identity.
encrypt_state``/``decrypt_state``, the same helper OAuth's ``state`` uses.
RelayState is built by hand (not via ``OneLogin_Saml2_Auth.login()``)
because the request's own id -- needed inside the blob -- only exists once
the ``AuthnRequest`` has been constructed, which ``login()`` does
internally, after it has already picked a ``RelayState``.
"""

from __future__ import annotations

import time
from urllib.parse import quote, urlparse

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.settings import OneLogin_Saml2_Settings
from pydantic import BaseModel

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.provider_identity import (
    decrypt_state,
    encrypt_state,
    get_provider_or_404,
    require_enabled_provider,
    resolve_or_create_user,
    set_oauth_v2_cookie,
)
from server.utils.url_utils import get_web_url
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import SamlProviderDetails
from storage.user_store import UserStore

saml_router = APIRouter(prefix='/saml', tags=['SAML'])

_REDIRECT_BINDING = 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect'
_POST_BINDING = 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST'
_UNSPECIFIED_NAME_ID_FORMAT = 'urn:oasis:names:tc:SAML:2.0:nameid-format:unspecified'
_EMAIL_NAME_ID_FORMAT = 'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress'


class SamlRelayState(BaseModel):
    """The encrypted ``RelayState`` payload round-tripped through the IdP.

    SAML's spec nominally recommends RelayState stay under ~80 bytes; real
    IdPs are generally fine with the few hundred bytes this encrypted blob
    comes out to (``redirect_url`` is the only unbounded field). If a
    specific IdP truncates or rejects it, the fix is a short opaque token
    keyed to a short-lived server-side row, not a redesign of this flow.
    """

    redirect_url: str = '/'
    request_id: str
    nonce: str = ''


def _saml_details(provider: AuthProvider) -> SamlProviderDetails:
    """Narrow ``provider.details`` to ``SamlProviderDetails``.

    Every caller runs after ``_get_saml_provider``'s kind check, so this
    should never actually mismatch -- documents the invariant rather than
    silently reaching into the wrong subclass's fields.
    """
    assert isinstance(provider.details, SamlProviderDetails)
    return provider.details


def _sp_entity_id(web_url: str, provider_id: int) -> str:
    return f'{web_url}/saml/{provider_id}/metadata'


def _acs_url(web_url: str, provider_id: int) -> str:
    return f'{web_url}/saml/{provider_id}/acs'


def _require_configured(details: SamlProviderDetails, provider_id: int) -> None:
    if not details.sp_private_key or not details.sp_x509_cert:
        # _ensure_sp_keypair (server.routes.oauth_providers_admin) generates
        # this unconditionally the moment a row becomes SamlProviderDetails,
        # so a missing key here means that invariant was bypassed some other
        # way (e.g. a direct DB write) -- our bug/data problem, not the
        # caller's, hence 500 rather than 400.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f'SAML provider {provider_id} has no SP keypair generated',
        )
    if not details.idp_entity_id or not details.sso_url or not details.idp_x509_cert:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f'SAML provider {provider_id} is not fully configured '
                '(missing IdP entity id, SSO URL, or certificate)'
            ),
        )


def _build_settings_dict(
    web_url: str, provider_id: int, details: SamlProviderDetails
) -> dict:
    """Build the python3-saml settings dict for ``provider_id``.

    One SP identity (entity id + ACS URL + keypair) per provider row, not
    one shared identity for the whole deployment -- matches
    ``SamlProviderDetails.sp_private_key``/``sp_x509_cert`` already being
    generated per row, so each configured IdP sees its own, independent SP
    identity (revoking trust with one IdP never touches another).
    """
    assert details.sp_private_key is not None
    assert details.sp_x509_cert is not None

    sso_binding = (
        _REDIRECT_BINDING if details.sso_binding == 'redirect' else _POST_BINDING
    )
    idp: dict = {
        'entityId': details.idp_entity_id,
        'singleSignOnService': {
            'url': details.sso_url,
            'binding': sso_binding,
        },
        'x509cert': details.idp_x509_cert,
    }
    if details.slo_url:
        idp['singleLogoutService'] = {'url': details.slo_url, 'binding': sso_binding}

    return {
        'strict': True,
        'debug': False,
        'sp': {
            'entityId': _sp_entity_id(web_url, provider_id),
            'assertionConsumerService': {
                'url': _acs_url(web_url, provider_id),
                'binding': _POST_BINDING,
            },
            'NameIDFormat': details.name_id_format or _UNSPECIFIED_NAME_ID_FORMAT,
            'x509cert': details.sp_x509_cert,
            'privateKey': details.sp_private_key.get_secret_value(),
        },
        'idp': idp,
        'security': {
            'authnRequestsSigned': True,
            'wantAssertionsSigned': True,
            'wantMessagesSigned': False,
            'wantNameIdEncrypted': False,
        },
    }


def _build_request_data(request: Request, web_url: str, post_data: dict) -> dict:
    """Build the framework-agnostic ``request_data`` dict python3-saml wants.

    Derived from ``get_web_url(request)``, not ``request.url`` directly --
    this app may sit behind a TLS-terminating reverse proxy/load balancer
    that ``request.url`` doesn't see through (``config.web_url`` is the
    override for exactly that case; see ``server.utils.url_utils``). SAML
    validates the signed request/response against what we tell it our own
    host is, so getting the externally-visible scheme/host wrong here would
    break signature validation, not just logging.
    """
    parsed = urlparse(web_url)
    return {
        'https': 'on' if parsed.scheme == 'https' else 'off',
        'http_host': parsed.netloc,
        'script_name': request.url.path,
        'get_data': dict(request.query_params),
        'post_data': post_data,
    }


async def _get_saml_provider(provider_id: int) -> AuthProvider:
    provider = await get_provider_or_404(provider_id)
    if provider.kind != SamlProviderDetails.__name__:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Provider {provider_id} is not a SAML provider',
        )
    return provider


def _extract_mapped_attribute(
    attributes: dict, attribute_mapping: dict, field: str
) -> str | None:
    attr_name = attribute_mapping.get(field)
    if not attr_name:
        return None
    values = attributes.get(attr_name)
    return values[0] if values else None


@saml_router.get('/{provider_id}/login')
async def saml_login(request: Request, provider_id: int, redirect_url: str = ''):
    """SP-initiated login: redirect to the IdP with a signed AuthnRequest."""
    provider = await _get_saml_provider(provider_id)
    require_enabled_provider(provider)
    details = _saml_details(provider)
    _require_configured(details, provider_id)

    web_url = get_web_url(request)
    settings_dict = _build_settings_dict(web_url, provider_id, details)
    request_data = _build_request_data(request, web_url, post_data={})
    auth = OneLogin_Saml2_Auth(request_data, old_settings=settings_dict)

    # Built by hand rather than auth.login() -- see the module docstring.
    authn_request = auth.authn_request_class(auth.get_settings())
    relay_state = encrypt_state(
        SamlRelayState(
            redirect_url=redirect_url or '/',
            request_id=authn_request.get_id(),
            nonce=str(time.time()),
        ).model_dump()
    )
    parameters = {
        'SAMLRequest': authn_request.get_request(),
        'RelayState': relay_state,
    }
    security = auth.get_settings().get_security_data()
    if security.get('authnRequestsSigned', False):
        auth.add_request_signature(parameters, security['signatureAlgorithm'])
    redirect_target = auth.redirect_to(auth.get_sso_url(), parameters)
    return RedirectResponse(redirect_target, status_code=302)


@saml_router.post('/{provider_id}/acs')
async def saml_acs(request: Request, provider_id: int):
    """Assertion Consumer Service: the IdP's auto-submitting POST target."""
    provider = await _get_saml_provider(provider_id)
    require_enabled_provider(provider)
    details = _saml_details(provider)
    _require_configured(details, provider_id)

    form = await request.form()
    saml_response = form.get('SAMLResponse')
    if not saml_response:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail='Missing SAMLResponse'
        )

    relay_state_raw = form.get('RelayState')
    if not relay_state_raw:
        # No unsolicited (IdP-initiated) SSO -- see the module docstring.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Missing RelayState; IdP-initiated SSO is not supported',
        )
    try:
        relay_state = SamlRelayState(**decrypt_state(str(relay_state_raw)))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid or tampered RelayState',
        ) from exc

    web_url = get_web_url(request)
    settings_dict = _build_settings_dict(web_url, provider_id, details)
    request_data = _build_request_data(
        request, web_url, post_data={'SAMLResponse': str(saml_response)}
    )
    auth = OneLogin_Saml2_Auth(request_data, old_settings=settings_dict)
    auth.process_response(request_id=relay_state.request_id)

    errors = auth.get_errors()
    if errors or not auth.is_authenticated():
        logger.warning(
            'saml_acs:invalid_response provider=%s errors=%s reason=%s',
            provider_id,
            errors,
            auth.get_last_error_reason(),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid SAML response'
        )

    name_id = auth.get_nameid()
    attributes = auth.get_attributes()
    if details.attribute_mapping.get('sub'):
        subject = _extract_mapped_attribute(
            attributes, details.attribute_mapping, 'sub'
        )
        if not subject:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='SAML assertion is missing the attribute mapped to sub',
            )
    elif name_id:
        subject = name_id
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='SAML assertion has no NameID',
        )

    email = _extract_mapped_attribute(
        attributes, details.attribute_mapping, 'email'
    ) or (
        name_id
        if (details.name_id_format or _UNSPECIFIED_NAME_ID_FORMAT)
        == _EMAIL_NAME_ID_FORMAT
        else None
    )
    userinfo = {
        'sub': subject,
        'email': email,
        # The IDP is the source of truth: SAML has no equivalent of OAuth's
        # email_verified=false -- if it asserts an email at all (a mapped
        # attribute, or an email-typed NameID), it's trusted.
        'email_verified': bool(email),
    }
    user_id = await resolve_or_create_user(provider, userinfo)
    if await UserStore.is_user_disabled(user_id):
        return RedirectResponse(
            f'{web_url}/login?account_disabled=true', status_code=302
        )

    # Mirror oauth_v2_callback: derive accepted_tos from the user row and
    # detour through the TOS page when it hasn't been accepted yet.
    user = await UserStore.get_user_by_id(user_id)
    has_accepted_tos = user is not None and user.accepted_tos is not None
    redirect_url = relay_state.redirect_url or '/'
    if not has_accepted_tos:
        encoded_redirect_url = quote(redirect_url, safe='')
        redirect_url = f'{web_url}/accept-tos?redirect_url={encoded_redirect_url}'

    response = RedirectResponse(redirect_url, status_code=302)
    set_oauth_v2_cookie(
        request=request,
        response=response,
        user_id=user_id,
        # SAML has no OAuth-style access/refresh token to track expiry for.
        access_token_expires_at=None,
        accepted_tos=has_accepted_tos,
        idp_provider_id=provider.id,
    )
    return response


@saml_router.get('/{provider_id}/metadata')
async def saml_metadata(request: Request, provider_id: int):
    """Unauthenticated SP metadata document for this provider's IdP side."""
    provider = await _get_saml_provider(provider_id)
    details = _saml_details(provider)
    _require_configured(details, provider_id)

    web_url = get_web_url(request)
    settings_dict = _build_settings_dict(web_url, provider_id, details)
    settings_obj = OneLogin_Saml2_Settings(settings_dict, sp_validation_only=True)
    metadata = settings_obj.get_sp_metadata()
    errors = settings_obj.validate_metadata(metadata)
    if errors:
        logger.error('saml_metadata:invalid provider=%s errors=%s', provider_id, errors)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Could not build valid SP metadata',
        )
    return Response(content=metadata, media_type='application/samlmetadata+xml')
