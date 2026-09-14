"""Authenticated Git connections, deliberately separate from application login."""

import os
import re
from datetime import UTC, datetime
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, SecretStr

from openhands.app_server.user_auth import get_user_id
from server.auth.native_git_config import git_config
from server.auth.native_session import SESSION_COOKIE
from server.auth.native_types import GitConnectionList, GitConnectionView
from server.routes.native_auth import NativeAuthRoute, authenticated_id
from server.services.native_auth_service import NativePrincipal, get_native_auth_service
from server.services.native_git_credentials import get_native_git_service
from server.services.native_git_provider import (
    GitCredentialError,
    ProviderPayload,
    _payload,
    provider_request,
)

native_git_router = APIRouter(
    prefix='/api/git-connections', route_class=NativeAuthRoute, tags=['Git connections']
)
native_git_oauth_router = APIRouter(
    prefix='/oauth/git', route_class=NativeAuthRoute, tags=['Git connections']
)


class GitHubAppPayload(ProviderPayload):
    slug: str = ''


class GitHostBody(BaseModel):
    host: str | None = Field(default=None, max_length=255)


class GitCredentialBody(GitHostBody):
    token: SecretStr
    email: str | None = Field(default=None, max_length=320)


async def browser_principal(request: Request) -> NativePrincipal:
    principal = await get_native_auth_service().authenticate_session(
        request.cookies.get(SESSION_COOKIE, '')
    )
    if principal is None:
        raise GitCredentialError('browser_session_required', 403)
    if any(
        name in request.headers
        for name in ('Authorization', 'X-Access-Token', 'X-Session-API-Key')
    ):
        from server.auth.auth_error import AuthError
        from server.auth.saas_user_auth import saas_user_auth_from_bearer

        try:
            explicit = await saas_user_auth_from_bearer(request)
        except AuthError as exc:
            raise GitCredentialError('browser_identity_mismatch', 403) from exc
        if explicit is None or await explicit.get_user_id() != str(
            principal.account_id
        ):
            raise GitCredentialError('browser_identity_mismatch', 403)
    return principal


@native_git_router.get('')
async def list_connections(
    user_id: str | None = Depends(get_user_id),
) -> GitConnectionList:
    return await get_native_git_service().list_connections(authenticated_id(user_id))


@native_git_router.put('/{provider}')
async def connect_manual(
    provider: str, body: GitCredentialBody, user_id: str | None = Depends(get_user_id)
) -> GitConnectionView:
    try:
        return await get_native_git_service().connect_manual(
            authenticated_id(user_id),
            provider,
            body.token.get_secret_value(),
            body.host,
            body.email,
        )
    except GitCredentialError:
        raise
    except ValueError as exc:
        raise GitCredentialError('invalid_provider_configuration', 400) from exc


@native_git_router.delete('/{provider}', status_code=204)
async def disconnect(provider: str, user_id: str | None = Depends(get_user_id)) -> None:
    try:
        await get_native_git_service().disconnect(authenticated_id(user_id), provider)
    except GitCredentialError:
        raise
    except ValueError as exc:
        raise GitCredentialError('invalid_provider_configuration', 400) from exc


@native_git_router.post('/{provider}/oauth')
async def start_oauth(
    provider: str, body: GitHostBody, request: Request
) -> dict[str, str]:
    try:
        url = await get_native_git_service().start_oauth(
            await browser_principal(request), provider, body.host
        )
        return {'authorization_url': url}
    except GitCredentialError:
        raise
    except ValueError as exc:
        raise GitCredentialError('invalid_provider_configuration', 400) from exc


@native_git_oauth_router.get('/{provider}/callback')
async def complete_oauth(
    provider: str,
    request: Request,
    state: str = '',
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    result = 'error'
    error_code = None
    try:
        result = await get_native_git_service().complete_oauth(
            await browser_principal(request), provider, state, code, error
        )
    except GitCredentialError as exc:
        error_code = exc.code
    except ValueError:
        error_code = 'invalid_provider_configuration'
    params = {
        'git_provider': provider
        if provider in ('github', 'gitlab', 'bitbucket')
        else '',
        'git_result': result,
    }
    if error_code:
        params['git_error'] = error_code
    return RedirectResponse(
        '/settings/integrations?' + urlencode(params), status_code=303
    )


@native_git_router.get('/github/installation')
async def github_installation(
    user_id: str | None = Depends(get_user_id),
) -> dict[str, str]:
    # Proof of active application identity, then lazy App metadata validation.
    await get_native_git_service().list_connections(authenticated_id(user_id))
    config = git_config('github')
    app_id = os.getenv('GITHUB_APP_ID') or os.getenv('GITHUB_APP_CLIENT_ID')
    private_key = os.getenv('GITHUB_APP_PRIVATE_KEY', '')
    if not app_id or not private_key:
        raise GitCredentialError('github_app_not_configured', 409)
    timestamp = int(datetime.now(UTC).timestamp())
    assertion = jwt.encode(
        {'iat': timestamp - 60, 'exp': timestamp + 540, 'iss': app_id},
        private_key,
        algorithm='RS256',
    )
    data = await provider_request(
        'GET',
        f'{config.api_url}/app',
        headers={
            'Authorization': f'Bearer {assertion}',
            'Accept': 'application/vnd.github+json',
        },
    )
    slug = _payload(data, GitHubAppPayload).slug
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', slug):
        raise GitCredentialError('provider_response_invalid', 502)
    return {'installation_url': f'https://{config.host}/apps/{slug}/installations/new'}
