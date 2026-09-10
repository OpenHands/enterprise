"""Authentication choices and compatibility authorization routes."""

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

from openhands.app_server.user_auth.user_auth import get_user_auth
from server.auth.browser_security import auth_email_configured, csrf_seed
from server.auth.keycloak.authorization import (
    authorization_url,
    configured_login_providers,
    require_keycloak,
)
from server.auth.mode import AuthMode, get_auth_mode

router = APIRouter(prefix='/api/auth', tags=['Authentication'])


@router.get('/capabilities')
async def capabilities(request: Request, response: Response):
    mode = get_auth_mode()
    local = mode is AuthMode.LOCAL
    csrf_seed(request, response)
    return {
        'mode': mode.value,
        'password_login': local,
        'login_providers': [] if local else configured_login_providers(),
        'registration': 'admin_or_invitation',
        'email_recovery': auth_email_configured() if local else False,
        'repository_connections': {'manual_tokens': True, 'broker': not local},
    }


@router.get('/authorize')
async def authorize(
    request: Request,
    provider: str,
    redirect_url: str = '/',
    invitation_token: str | None = None,
    recaptcha_token: str | None = None,
):
    return RedirectResponse(
        authorization_url(
            request, provider, redirect_url, invitation_token, recaptcha_token
        ),
        302,
    )


@router.get('/providers/{provider}/link')
async def link_provider(request: Request, provider: str, redirect_url: str = '/'):
    require_keycloak()
    await get_user_auth(request)
    return RedirectResponse(
        authorization_url(request, provider, redirect_url, link=True), 302
    )
