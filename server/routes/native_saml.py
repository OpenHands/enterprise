"""SP-initiated SAML routes; only POST ACS bypasses normal browser CSRF."""

from urllib.parse import parse_qs

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.exc import SQLAlchemyError

from server.auth.native_password import NativeAuthError
from server.auth.native_session import (
    SESSION_COOKIE,
    _secure,
    get_app_origin,
    set_session_cookie,
)
from server.auth.saml_config import SAML_PREFIX
from server.routes.native_auth import NO_STORE, NativeAuthRoute, client_ip
from server.services.native_auth_service import get_native_auth_service
from server.services.native_saml_service import (
    MAX_RESPONSE_BYTES,
    TRANSACTION_SECONDS,
    get_native_saml_service,
)

SAML_BROWSER_COOKIE = 'openhands_saml'
SAML_ERRORS = {
    'account_link_required': 'Sign in to your existing account and link SSO in account settings.',
    'invitation_required': 'Ask your administrator for an account invitation.',
    'email_mismatch': 'The SSO email differs from this account. Contact your administrator.',
    'unavailable': 'Account access is unavailable. Contact your administrator.',
    'recent_auth_required': 'Sign in again before linking SSO or performing this action.',
    'temporarily_unavailable': 'SSO is temporarily unavailable. Start sign-in again.',
    'invalid_response': 'SSO sign-in could not be completed. Start again.',
}


def _error_response(exc: NativeAuthError) -> JSONResponse:
    status = exc.status_code
    code = exc.code or (
        'temporarily_unavailable'
        if status in (429, 503)
        else 'unavailable'
        if status == 403
        else 'invalid_response'
    )
    if code not in SAML_ERRORS:
        code = 'invalid_response'
    return JSONResponse(
        {'code': code, 'detail': SAML_ERRORS[code]},
        status_code=status,
        headers=NO_STORE,
    )


native_saml_router = APIRouter(
    prefix=SAML_PREFIX, tags=['Authentication'], route_class=NativeAuthRoute
)


class SamlStart(BaseModel):
    return_path: str | None = Field(default=None, max_length=2048)
    invitation_token: SecretStr | None = None
    link: bool = False
    reauthenticate: bool = False


@native_saml_router.get('/metadata')
async def metadata() -> Response:
    config = get_native_saml_service().config()
    from onelogin.saml2.settings import OneLogin_Saml2_Settings

    toolkit = OneLogin_Saml2_Settings(config.toolkit_settings())
    return Response(
        toolkit.get_sp_metadata(), media_type='application/samlmetadata+xml'
    )


@native_saml_router.post('/start', response_model=None)
async def start(
    body: SamlStart, request: Request, response: Response
) -> dict[str, str] | JSONResponse:
    try:
        await get_native_auth_service().throttle('saml_start', client_ip(request))
        location, browser = await get_native_saml_service().start(
            return_path=body.return_path,
            invitation_token=body.invitation_token.get_secret_value()
            if body.invitation_token
            else None,
            link=body.link,
            reauthenticate=body.reauthenticate,
            session_token=request.cookies.get(SESSION_COOKIE),
        )
    except NativeAuthError as exc:
        return _error_response(exc)
    except SQLAlchemyError:
        return _error_response(NativeAuthError('', 503, code='temporarily_unavailable'))
    response.set_cookie(
        SAML_BROWSER_COOKIE,
        browser,
        max_age=TRANSACTION_SECONDS,
        secure=_secure(request),
        httponly=True,
        samesite='lax',
        path=SAML_PREFIX,
    )
    return {'redirect_to': location}


@native_saml_router.post('/acs')
async def acs(request: Request) -> RedirectResponse:
    try:
        await get_native_auth_service().throttle('saml_acs', client_ip(request))
        if (
            request.headers.get('content-type', '').split(';', 1)[0]
            != 'application/x-www-form-urlencoded'
        ):
            raise NativeAuthError('Invalid SAML binding')
        # Bound streaming bytes before form or XML parsing, even without Content-Length.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES * 2:
                raise NativeAuthError('SAML response too large')
        form = parse_qs(
            body.decode('ascii'),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=4,
        )
        if set(form) != {'SAMLResponse', 'RelayState'} or any(
            len(v) != 1 for v in form.values()
        ):
            raise NativeAuthError('Invalid SAML binding')
        await get_native_saml_service().accept_response(
            form['RelayState'][0], form['SAMLResponse'][0]
        )
    except (NativeAuthError, ValueError, UnicodeError, SQLAlchemyError):
        # No assertion/error body/subject reaches the URL or application logger.
        return RedirectResponse(
            f'{get_app_origin()}/login?sso_error=invalid_response',
            status_code=303,
            headers=NO_STORE,
        )
    return RedirectResponse(
        f'{get_app_origin()}/auth/saml/complete', status_code=303, headers=NO_STORE
    )


@native_saml_router.post('/complete', response_model=None)
async def complete(
    request: Request, response: Response
) -> dict[str, str] | JSONResponse:
    try:
        await get_native_auth_service().throttle('saml_complete', client_ip(request))
        result = await get_native_saml_service().complete(
            request.cookies.get(SAML_BROWSER_COOKIE),
            request.cookies.get(SESSION_COOKIE),
        )
    except NativeAuthError as exc:
        return _error_response(exc)
    except SQLAlchemyError:
        return _error_response(NativeAuthError('', 503, code='temporarily_unavailable'))
    response.delete_cookie(
        SAML_BROWSER_COOKIE,
        path=SAML_PREFIX,
        secure=_secure(request),
        httponly=True,
        samesite='lax',
    )
    set_session_cookie(response, result.token, request)
    return {'redirect_to': result.redirect_to}
