"""Direct provider HTTP contracts, including modern Bitbucket API tokens."""

import base64
import traceback
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import JsonValue

from server.auth.native_git_config import NativeGitConfig
from server.services import native_git_provider
from server.services.native_git_provider import GitCredentialError

HttpProvider = tuple[list[httpx.Request], list[tuple[int, dict[str, str], JsonValue]]]


@pytest.fixture
def http_provider(monkeypatch: pytest.MonkeyPatch) -> HttpProvider:
    original = httpx.AsyncClient
    requests: list[httpx.Request] = []
    responses: list[tuple[int, dict[str, str], JsonValue]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status, headers, body = responses.pop(0)
        return httpx.Response(status, headers=headers, json=body)

    def client(*, timeout: float, follow_redirects: bool) -> httpx.AsyncClient:
        return original(
            transport=httpx.MockTransport(handle),
            timeout=timeout,
            follow_redirects=follow_redirects,
        )

    monkeypatch.setattr(native_git_provider.httpx, 'AsyncClient', client)
    return requests, responses


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'provider,email',
    [('github', None), ('gitlab', None), ('bitbucket', 'dev@example.com')],
)
async def test_identity_endpoint_and_transport(
    http_provider: HttpProvider, provider: str, email: str | None
) -> None:
    requests, responses = http_provider
    responses.append(
        (200, {}, {'id': 123, 'uuid': '{bitbucket-id}', 'login': 'developer'})
    )
    config = NativeGitConfig(
        provider, f'{provider}.com' if provider != 'bitbucket' else 'bitbucket.org', ()
    )
    result = await native_git_provider.verify_provider_identity(
        config, 'provider-secret', email
    )
    assert result.id == ('{bitbucket-id}' if email else '123')
    authorization = requests[0].headers['Authorization']
    assert authorization == (
        'Basic ' + base64.b64encode(b'dev@example.com:provider-secret').decode()
        if email
        else 'Bearer provider-secret'
    )
    assert str(requests[0].url) == config.api_url + '/user'


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['github', 'gitlab', 'bitbucket'])
async def test_confidential_oauth_code_and_rotation(
    http_provider: HttpProvider, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    requests, responses = http_provider
    monkeypatch.setenv(f'{provider.upper()}_APP_CLIENT_ID', 'client')
    monkeypatch.setenv(f'{provider.upper()}_APP_CLIENT_SECRET', 'client-secret')
    monkeypatch.setenv('NATIVE_AUTH_APP_ORIGIN', 'https://native.example.com')
    from server.auth.auth_config import get_native_auth_settings

    get_native_auth_settings.cache_clear()
    responses.extend(
        [
            (
                200,
                {},
                {
                    'access_token': 'first',
                    'refresh_token': 'refresh-one',
                    'expires_in': 3600,
                },
            ),
            (
                200,
                {},
                {
                    'access_token': 'second',
                    'refresh_token': 'refresh-two',
                    'expires_in': 3600,
                },
            ),
        ]
    )
    config = NativeGitConfig(
        provider,
        'bitbucket.org' if provider == 'bitbucket' else f'{provider}.com',
        ('oauth',),
    )
    grant = await native_git_provider.exchange_grant(
        config, code='code', verifier='verifier' if provider != 'bitbucket' else None
    )
    rotated = await native_git_provider.exchange_grant(
        config, refresh_token=grant.refresh_token
    )
    assert rotated.refresh_token == 'refresh-two'
    body = parse_qs(requests[0].content.decode())
    assert body['redirect_uri'] == [
        f'https://native.example.com/oauth/git/{provider}/callback'
    ]
    assert ('code_verifier' in body) == (provider != 'bitbucket')
    if provider == 'bitbucket':
        assert requests[0].headers['Authorization'].startswith('Basic ')
        assert 'client_secret' not in body
    else:
        assert body['client_secret'] == ['client-secret']
    if provider == 'gitlab':
        assert (
            parse_qs(requests[1].content.decode())['redirect_uri']
            == body['redirect_uri']
        )
    get_native_auth_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status,headers,body,expected',
    [
        (403, {'X-RateLimit-Remaining': '0'}, {}, 'provider_unavailable'),
        (403, {'Retry-After': '30'}, {}, 'provider_unavailable'),
        (503, {}, {'secret': 'never-show'}, 'provider_unavailable'),
        (
            400,
            {},
            {'error': 'invalid_grant', 'secret': 'never-show'},
            'credential_rejected',
        ),
        (401, {}, {'secret': 'never-show'}, 'credential_rejected'),
    ],
)
async def test_provider_errors_are_safe_and_not_app_auth(
    http_provider: HttpProvider,
    status: int,
    headers: dict[str, str],
    body: JsonValue,
    expected: str,
) -> None:
    _, responses = http_provider
    responses.append((status, headers, body))
    with pytest.raises(GitCredentialError, match=expected) as raised:
        await native_git_provider.provider_request('GET', 'https://api.github.com/user')
    assert raised.value.status_code != 401
    assert 'never-show' not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['github', 'gitlab'])
async def test_manual_tokens_with_insufficient_scopes_are_rejected(
    http_provider: HttpProvider, provider: str
) -> None:
    _, responses = http_provider
    responses.append(
        (
            200,
            {'X-OAuth-Scopes': 'read:user'} if provider == 'github' else {},
            {'id': 123, 'login': 'developer'},
        )
    )
    if provider == 'gitlab':
        responses.append((200, {}, {'scopes': ['read_user']}))
    config = NativeGitConfig(provider, f'{provider}.com', ('pat',))
    with pytest.raises(GitCredentialError, match='insufficient_scope'):
        await native_git_provider.verify_provider_identity(
            config, 'account-only-token', manual=True
        )


@pytest.mark.asyncio
async def test_malformed_grant_never_exposes_credentials(
    http_provider: HttpProvider,
) -> None:
    _, responses = http_provider
    responses.append(
        (
            200,
            {},
            {
                'access_token': {'secret': 'private-access-marker'},
                'refresh_token': 'private-refresh-marker',
            },
        )
    )
    config = NativeGitConfig('github', 'github.com', ('oauth',))
    with pytest.raises(GitCredentialError, match='provider_response_invalid') as raised:
        await native_git_provider.exchange_grant(
            config, refresh_token='existing-refresh'
        )
    formatted = ''.join(traceback.format_exception(raised.value))
    assert 'private-access-marker' not in formatted
    assert 'private-refresh-marker' not in formatted
    assert raised.value.status_code == 502
