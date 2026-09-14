"""Bounded direct provider exchanges. Never log a provider response or credential."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from server.auth.native_git_config import NativeGitConfig
from server.auth.native_password import NativeAuthError


class GitCredentialError(NativeAuthError):
    def __init__(self, code: str, status_code: int = 422) -> None:
        self.code = code
        super().__init__(code, status_code)


@dataclass(frozen=True)
class GitGrant:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None


@dataclass(frozen=True)
class GitIdentity:
    id: str
    login: str
    display_name: str | None = None
    avatar_url: str | None = None


class ProviderPayload(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    error: str | None = None


class ProviderAvatar(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    href: str | None = None


class BitbucketLinks(BaseModel):
    avatar: ProviderAvatar | None = None


class ProviderIdentityPayload(ProviderPayload):
    id: str | int | None = None
    uuid: str | None = None
    state: str = 'active'
    login: str | None = None
    username: str | None = None
    nickname: str | None = None
    name: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    links: BitbucketLinks | None = None


class GitlabTokenDetails(ProviderPayload):
    scopes: list[str] = []


class GrantPayload(ProviderPayload):
    access_token: str = ''
    refresh_token: str | None = None
    token_type: str = 'bearer'
    scope: str | None = None
    scopes: str | None = None
    expires_in: int | str | None = None
    refresh_token_expires_in: int | str | None = None


async def provider_request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, str | int] | None = None,
    data: Mapping[str, str] | None = None,
    json: Mapping[str, str] | None = None,
    auth: httpx.BasicAuth | None = None,
) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                data=data,
                json=json,
                auth=auth,
            )
    except httpx.HTTPError as exc:
        raise GitCredentialError('provider_unavailable', 503) from exc
    if response.status_code == 403 and (
        response.headers.get('Retry-After')
        or response.headers.get('X-RateLimit-Remaining') == '0'
    ):
        raise GitCredentialError('provider_unavailable', 503)
    if response.status_code in (401, 403):
        raise GitCredentialError('credential_rejected')
    if response.status_code == 429 or response.status_code >= 500:
        raise GitCredentialError('provider_unavailable', 503)
    if response.status_code == 204:
        return response
    try:
        payload = ProviderPayload.model_validate_json(response.content)
    except ValidationError:
        code = (
            'provider_response_invalid'
            if response.is_success
            else 'provider_request_failed'
        )
        raise GitCredentialError(code, 502) from None
    if payload.error in ('invalid_grant', 'bad_refresh_token', 'expired_token'):
        raise GitCredentialError('credential_rejected')
    if not response.is_success:
        raise GitCredentialError('provider_request_failed', 502)
    if payload.error:
        raise GitCredentialError('oauth_exchange_failed')
    return response


def _payload[Payload: ProviderPayload](
    response: httpx.Response, model: type[Payload]
) -> Payload:
    try:
        return model.model_validate_json(response.content)
    except ValidationError:
        raise GitCredentialError('provider_response_invalid', 502) from None


async def verify_provider_identity(
    config: NativeGitConfig,
    token: str,
    email: str | None = None,
    *,
    manual: bool = False,
) -> GitIdentity:
    headers = {'Accept': 'application/json'}
    auth = None
    if config.provider == 'bitbucket' and email:
        auth = httpx.BasicAuth(email, token)
    else:
        headers['Authorization'] = f'Bearer {token}'
    response = await provider_request(
        'GET', f'{config.api_url}/user', headers=headers, auth=auth
    )
    data = _payload(response, ProviderIdentityPayload)
    oauth_scopes = response.headers.get('X-OAuth-Scopes')
    if manual and config.provider == 'github' and oauth_scopes is not None:
        scopes = {scope.strip() for scope in oauth_scopes.split(',')}
        if not scopes.intersection({'repo', 'public_repo'}):
            raise GitCredentialError('insufficient_scope')
    if manual and config.provider == 'gitlab':
        details = _payload(
            await provider_request(
                'GET',
                f'{config.api_url}/personal_access_tokens/self',
                headers=headers,
                auth=auth,
            ),
            GitlabTokenDetails,
        )
        if 'api' not in details.scopes:
            raise GitCredentialError('insufficient_scope')
    if manual and config.provider == 'bitbucket':
        await provider_request(
            'GET',
            f'{config.api_url}/user/permissions/repositories',
            params={'pagelen': 1},
            headers=headers,
            auth=auth,
        )
    subject = data.uuid if config.provider == 'bitbucket' else data.id
    if not subject or data.state not in ('active', 'ACTIVE'):
        raise GitCredentialError('provider_identity_invalid')
    login = data.login or data.username or data.nickname or str(subject)
    avatar = data.avatar_url
    if config.provider == 'bitbucket':
        avatar = data.links.avatar.href if data.links and data.links.avatar else None
    return GitIdentity(
        str(subject),
        login,
        data.name or data.display_name,
        avatar if avatar and avatar.startswith('https://') else None,
    )


async def exchange_grant(
    config: NativeGitConfig,
    *,
    code: str | None = None,
    verifier: str | None = None,
    refresh_token: str | None = None,
) -> GitGrant:
    data = {'grant_type': 'refresh_token' if refresh_token else 'authorization_code'}
    if refresh_token:
        data['refresh_token'] = refresh_token
        if config.provider == 'gitlab':
            data['redirect_uri'] = config.callback_url
    else:
        data.update(code=code or '', redirect_uri=config.callback_url)
        if verifier:
            data['code_verifier'] = verifier
    auth = None
    if config.provider == 'bitbucket':
        auth = httpx.BasicAuth(config.client_id, config.client_secret)
    else:
        data.update(client_id=config.client_id, client_secret=config.client_secret)
    response = _payload(
        await provider_request(
            'POST',
            config.token_url,
            data=data,
            headers={'Accept': 'application/json'},
            auth=auth,
        ),
        GrantPayload,
    )
    scope_value = response.scope or response.scopes
    if scope_value is not None:
        scopes = set(scope_value.replace(',', ' ').split())
        if config.provider == 'gitlab' and 'api' not in scopes:
            raise GitCredentialError('insufficient_scope')
        if config.provider == 'github' and not scopes.intersection(
            {'repo', 'public_repo'}
        ):
            raise GitCredentialError('insufficient_scope')
        if config.provider == 'bitbucket' and not {
            'repository:write',
            'pullrequest:write',
        }.issubset(scopes):
            raise GitCredentialError('insufficient_scope')
    if not response.access_token:
        raise GitCredentialError('oauth_exchange_failed')
    if response.token_type.lower() != 'bearer':
        raise GitCredentialError('oauth_token_type_invalid')
    now = datetime.now(UTC)

    def expiry(value: int | str | None) -> datetime | None:
        if value is None:
            return None
        try:
            seconds = int(value)
            if seconds <= 0:
                raise ValueError
            return now + timedelta(seconds=seconds)
        except (ValueError, TypeError, OverflowError):
            raise GitCredentialError('provider_response_invalid', 502) from None

    return GitGrant(
        response.access_token,
        response.refresh_token or refresh_token,
        expiry(response.expires_in),
        expiry(response.refresh_token_expires_in),
    )


async def revoke_grant(config: NativeGitConfig, access_token: str) -> None:
    """Best-effort revocation of this OAuth token after local disconnect commits.

    Bitbucket documents no token-revocation endpoint. PAT/API tokens remain
    user-owned and are never globally revoked by disconnecting OpenHands.
    """
    if config.provider == 'bitbucket':
        return
    for _ in range(2):
        try:
            if config.provider == 'github':
                await provider_request(
                    'DELETE',
                    f'{config.api_url}/applications/{config.client_id}/token',
                    auth=httpx.BasicAuth(config.client_id, config.client_secret),
                    json={'access_token': access_token},
                )
            else:
                await provider_request(
                    'POST',
                    f'https://{config.host}/oauth/revoke',
                    data={
                        'client_id': config.client_id,
                        'client_secret': config.client_secret,
                        'token': access_token,
                    },
                )
            return
        except GitCredentialError as exc:
            if exc.status_code != 503:
                return
