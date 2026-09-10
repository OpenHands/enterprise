"""Backend-owned authorization URLs for the existing broker callbacks."""

import base64
import json
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, Request

from server.auth import constants
from server.auth.browser_security import safe_redirect
from server.auth.mode import is_keycloak_enabled
from server.utils.url_utils import get_web_url


def require_keycloak() -> None:
    if not is_keycloak_enabled():
        raise HTTPException(404, 'Keycloak authentication is unavailable.')


def configured_login_providers() -> list[str]:
    configured = (
        ('github', constants.GITHUB_APP_CLIENT_ID),
        ('gitlab', constants.GITLAB_APP_CLIENT_ID),
        ('bitbucket', constants.BITBUCKET_APP_CLIENT_ID),
        ('enterprise_sso', constants.ENABLE_ENTERPRISE_SSO),
        ('bitbucket_data_center', constants.BITBUCKET_DATA_CENTER_CLIENT_ID),
        ('azure_devops', constants.AZURE_DEVOPS_CLIENT_ID),
    )
    return [provider for provider, enabled in configured if enabled]


def validate_return_destination(value: str | None, web_url: str) -> str:
    if not value:
        return web_url
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        origin = urlsplit(web_url)
        if (
            parsed.scheme != origin.scheme
            or parsed.netloc != origin.netloc
            or parsed.username
            or parsed.password
        ):
            raise HTTPException(400, 'Invalid return destination.')
        relative = urlunsplit(
            ('', '', parsed.path or '/', parsed.query, parsed.fragment)
        )
        safe_redirect(relative)
        return value
    return web_url + safe_redirect(value)


def authorization_url(
    request: Request,
    provider: str,
    redirect_url: str,
    invitation_token: str | None = None,
    recaptcha_token: str | None = None,
    *,
    link: bool = False,
) -> str:
    require_keycloak()
    if provider not in configured_login_providers() or (
        link and provider == 'enterprise_sso'
    ):
        raise HTTPException(400, 'Unsupported identity provider.')
    web_url = get_web_url(request)
    state = {'redirect_url': validate_return_destination(redirect_url, web_url)}
    if invitation_token:
        state['invitation_token'] = invitation_token
    if recaptcha_token:
        state['recaptcha_token'] = recaptcha_token
    if link:
        state['link_provider'] = provider
    params = {
        'client_id': constants.KEYCLOAK_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': f'{web_url}/oauth/keycloak/callback',
        'scope': 'openid email profile',
        'state': base64.urlsafe_b64encode(json.dumps(state).encode()).decode(),
    }
    if link:
        params['kc_action'] = f'idp_link:{provider}'
    else:
        params['kc_idp_hint'] = provider
    base = constants.KEYCLOAK_SERVER_URL_EXT
    if not urlsplit(base).netloc:
        base = constants.KEYCLOAK_SERVER_URL
    if not urlsplit(base).netloc:
        raise HTTPException(
            503, 'The Keycloak public authorization URL is not configured.'
        )
    return f'{base}/realms/{constants.KEYCLOAK_REALM_NAME}/protocol/openid-connect/auth?{urlencode(params)}'
