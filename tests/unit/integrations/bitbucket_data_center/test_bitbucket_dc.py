"""Tests for BitbucketDCService core: init, headers, get_user, pagination, email."""

import base64
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.app_server.integrations.bitbucket_data_center.bitbucket_dc_service import (
    BitbucketDCService,
)
from openhands.app_server.integrations.bitbucket_data_center.service import (
    base as bbdc_base,
)
from openhands.app_server.integrations.provider import ProviderHandler, ProviderToken
from openhands.app_server.integrations.service_types import (
    AuthenticationError,
    ProviderType,
    Repository,
    User,
)
from openhands.app_server.types import AppMode

# ── init / BASE_URL ───────────────────────────────────────────────────────────


def test_init_plain_domain():
    svc = BitbucketDCService(token=SecretStr('tok'), base_domain='host.example.com')
    assert svc.BASE_URL == 'https://host.example.com/rest/api/1.0'


def test_init_no_domain():
    svc = BitbucketDCService(token=SecretStr('tok'), base_domain=None)
    assert svc.BASE_URL == ''


def test_init_falls_back_to_env_var_when_base_domain_missing(monkeypatch):
    monkeypatch.setenv('BITBUCKET_DATA_CENTER_HOST', 'env.example.com')
    svc = BitbucketDCService(token=SecretStr('tok'))
    assert svc.BASE_URL == 'https://env.example.com/rest/api/1.0'


def test_init_explicit_base_domain_overrides_env_var(monkeypatch):
    monkeypatch.setenv('BITBUCKET_DATA_CENTER_HOST', 'env.example.com')
    svc = BitbucketDCService(token=SecretStr('tok'), base_domain='explicit.example.com')
    assert svc.BASE_URL == 'https://explicit.example.com/rest/api/1.0'


# ── token formats ─────────────────────────────────────────────────────────────


def test_token_kept_as_given():
    svc = BitbucketDCService(token=SecretStr('mytoken'))
    assert svc.token.get_secret_value() == 'mytoken'


def test_token_preserves_colon_token():
    svc = BitbucketDCService(token=SecretStr('alice:secret'))
    assert svc.token.get_secret_value() == 'alice:secret'


# ── user_id derivation ────────────────────────────────────────────────────────


def test_user_id_derived_from_username_password_token():
    svc = BitbucketDCService(token=SecretStr('alice:secret'))
    assert svc.user_id == 'alice'


def test_user_id_not_derived_from_xtoken_auth_token():
    svc = BitbucketDCService(token=SecretStr('x-token-auth:mytoken'))
    assert svc.user_id is None


def test_user_id_not_derived_from_bare_token():
    svc = BitbucketDCService(token=SecretStr('mytoken'))
    assert svc.user_id is None


def test_explicit_user_id_not_overridden():
    svc = BitbucketDCService(token=SecretStr('alice:secret'), user_id='bob')
    assert svc.user_id == 'bob'


# ── _get_headers ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_headers_basic_auth():
    svc = BitbucketDCService(
        token=SecretStr('user:pass'), base_domain='host.example.com'
    )
    headers = await svc._get_headers()
    expected = 'Basic ' + base64.b64encode(b'user:pass').decode()
    assert headers['Authorization'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('token', ['BBDC-abc', 'x-token-auth:BBDC-abc'])
async def test_get_headers_bearer_for_access_token(token):
    # Data Center rejects x-token-auth Basic for personal tokens, and for
    # project and repository tokens on 8.x; Bearer works for all of them.
    svc = BitbucketDCService(token=SecretStr(token), base_domain='host.example.com')
    headers = await svc._get_headers()
    assert headers['Authorization'] == 'Bearer BBDC-abc'


@pytest.mark.asyncio
async def test_get_headers_lazy_loads_token_when_empty():
    """When the service is constructed without a token (SaaS path with
    external_auth_id only), _get_headers must resolve the latest token via
    get_latest_token() instead of producing an empty 'Basic ' header that
    httpx rejects with LocalProtocolError.
    """
    svc = BitbucketDCService(base_domain='host.example.com')
    assert svc.token.get_secret_value() == ''  # confirm starting state

    async def fake_get_latest_token():
        return SecretStr('oauth-access-token')

    svc.get_latest_token = fake_get_latest_token  # type: ignore[method-assign]

    headers = await svc._get_headers()
    assert headers['Authorization'] == 'Bearer oauth-access-token'


@pytest.mark.asyncio
async def test_get_headers_uses_bearer_for_raw_oauth_token():
    """OAuth 2.0 access tokens (no colon) must be sent as Bearer per RFC 6750,
    not as Basic auth — Bitbucket Data Center's OAuth provider expects this
    format for tokens issued via /rest/oauth2/latest/token.
    """
    svc = BitbucketDCService(base_domain='host.example.com')
    svc.token = SecretStr('eyJraWQiOiJyYXctb2F1dGgyLXRva2VuIn0')

    headers = await svc._get_headers()
    assert headers['Authorization'] == 'Bearer eyJraWQiOiJyYXctb2F1dGgyLXRva2VuIn0'


# ── get_git_credentials ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _empty_git_username_cache(monkeypatch):
    # Tests reuse a server and token with different outcomes.
    monkeypatch.setattr(bbdc_base, '_git_usernames', {})


async def _token_user(url, params=None, **kwargs):
    """A server whose whoami is empty but whose X-AUSERNAME names the token's user."""
    if url.endswith('/plugins/servlet/applinks/whoami'):
        return '', {}
    return {'values': []}, {'X-AUSERNAME': 'project_7_bot'}


def _login_check(status_code: int):
    """Patch the Basic login check; returns the mock to inspect its headers."""
    return patch(
        'httpx.AsyncClient.get',
        new_callable=AsyncMock,
        return_value=httpx.Response(status_code),
    )


def _basic(username: str, secret: str) -> str:
    return 'Basic ' + base64.b64encode(f'{username}:{secret}'.encode()).decode()


@pytest.mark.asyncio
async def test_git_credentials_keep_username_token():
    svc = BitbucketDCService(
        token=SecretStr('alice:BBDC-abc'), base_domain='host.example.com'
    )
    with _login_check(200) as get:
        assert await svc.get_git_credentials() == ('alice', 'BBDC-abc')
    get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('token', ['BBDC-abc', 'x-token-auth:BBDC-abc'])
async def test_git_credentials_use_x_token_auth_when_accepted(token):
    # OAuth tokens, and project and repository tokens on 9.x
    svc = BitbucketDCService(token=SecretStr(token), base_domain='host.example.com')
    with _login_check(200) as get:
        assert await svc.get_git_credentials() == ('x-token-auth', 'BBDC-abc')
    assert get.await_args.kwargs['headers']['Authorization'] == _basic(
        'x-token-auth', 'BBDC-abc'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('token', ['BBDC-abc', 'x-token-auth:BBDC-abc'])
async def test_git_credentials_use_token_user_when_x_token_auth_rejected(token):
    # Personal tokens, and project and repository tokens on 8.x
    svc = BitbucketDCService(token=SecretStr(token), base_domain='host.example.com')
    with (
        _login_check(401),
        patch.object(svc, '_make_request', side_effect=_token_user),
    ):
        assert await svc.get_git_credentials() == ('project_7_bot', 'BBDC-abc')


@pytest.mark.asyncio
async def test_git_credentials_fall_back_to_x_token_auth_when_server_unreachable():
    svc = BitbucketDCService(
        token=SecretStr('BBDC-abc'), base_domain='host.example.com'
    )
    with (
        patch(
            'httpx.AsyncClient.get',
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError('unreachable'),
        ),
        patch.object(svc, '_make_request', side_effect=httpx.ConnectError('down')),
    ):
        assert await svc.get_git_credentials() == ('x-token-auth', 'BBDC-abc')


def _service(token: str = 'BBDC-abc', domain: str = 'host.example.com'):
    return BitbucketDCService(token=SecretStr(token), base_domain=domain)


@pytest.mark.asyncio
async def test_git_credentials_reuse_the_answer_for_the_same_server_and_token():
    # Each request builds its own service, so the answer must outlive one.
    with (
        _login_check(401) as get,
        patch.object(
            BitbucketDCService, '_make_request', side_effect=_token_user
        ) as lookup,
    ):
        assert await _service().get_git_credentials() == ('project_7_bot', 'BBDC-abc')
        assert await _service().get_git_credentials() == ('project_7_bot', 'BBDC-abc')
    get.assert_awaited_once()
    assert lookup.await_count == 2  # whoami, then X-AUSERNAME, both once
    assert 'BBDC-abc' not in repr(bbdc_base._git_usernames)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'token, domain', [('BBDC-xyz', 'host.example.com'), ('BBDC-abc', 'other.com')]
)
async def test_git_credentials_check_again_for_another_token_or_server(token, domain):
    with _login_check(200) as get:
        await _service().get_git_credentials()
        assert await _service(token, domain).get_git_credentials() == (
            'x-token-auth',
            token,
        )
    assert get.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('status_code', [429, 503])
async def test_git_credentials_check_again_after_an_inconclusive_check(status_code):
    svc = _service()
    with (
        _login_check(status_code) as get,
        patch.object(svc, '_make_request', side_effect=_token_user),
    ):
        assert await svc.get_git_credentials() == ('project_7_bot', 'BBDC-abc')
        assert await svc.get_git_credentials() == ('project_7_bot', 'BBDC-abc')
    assert get.await_count == 2


@pytest.mark.asyncio
async def test_git_credentials_answer_expires_after_ten_minutes():
    with _login_check(200) as get, patch.object(bbdc_base, 'time') as clock:
        clock.monotonic.return_value = 1000.0
        await _service().get_git_credentials()
        clock.monotonic.return_value = 1599.0
        await _service().get_git_credentials()
        assert get.await_count == 1
        clock.monotonic.return_value = 1600.0
        await _service().get_git_credentials()
        assert get.await_count == 2
        clock.monotonic.return_value = 2200.0
        await _service('BBDC-xyz').get_git_credentials()
    # Expired answers are dropped when a new one is kept.
    assert len(bbdc_base._git_usernames) == 1


@pytest.mark.asyncio
async def test_authenticated_git_url_uses_git_credentials():
    handler = ProviderHandler(
        provider_tokens=MappingProxyType(
            {
                ProviderType.BITBUCKET_DATA_CENTER: ProviderToken(
                    token=SecretStr('BBDC-a/b'), host='bitbucket.example.com'
                )
            }
        )
    )
    repository = Repository(
        id='1',
        full_name='PROJ/repo',
        git_provider=ProviderType.BITBUCKET_DATA_CENTER,
        is_public=False,
    )
    with (
        patch.object(
            BitbucketDCService,
            'get_repository_details_from_repo_name',
            new_callable=AsyncMock,
            return_value=repository,
        ),
        patch.object(
            BitbucketDCService,
            'get_git_credentials',
            new_callable=AsyncMock,
            return_value=('project_7_bot', 'BBDC-a/b'),
        ),
    ):
        url = await handler.get_authenticated_git_url(
            'PROJ/repo', specified_provider=ProviderType.BITBUCKET_DATA_CENTER
        )
    assert url == (
        'https://project_7_bot:BBDC-a%2Fb@bitbucket.example.com/scm/proj/repo.git'
    )


# ── get_user ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_user_with_user_id():
    svc = BitbucketDCService(
        token=SecretStr('tok'),
        base_domain='host.example.com',
        user_id='jdoe',
    )
    mock_response = {
        'values': [
            {
                'id': 5,
                'slug': 'jdoe',
                'name': 'jdoe',
                'displayName': 'J Doe',
                'emailAddress': 'j@example.com',
                'avatarUrl': '',
            }
        ]
    }
    with patch.object(svc, '_make_request', return_value=(mock_response, {})):
        user = await svc.get_user()

    assert user.id == '5'
    assert user.login == 'jdoe'
    assert user.name == 'J Doe'
    assert user.email == 'j@example.com'


@pytest.mark.asyncio
async def test_get_user_without_user_id_resolves_via_whoami():
    # OAuth bearer tokens (SaaS/Keycloak broker flow) carry no username, so
    # user_id is None; the user is resolved via the applinks whoami servlet.
    svc = BitbucketDCService(
        token=SecretStr('x-token-auth:mytoken'), base_domain='host.example.com'
    )
    requested_urls = []

    async def mock_make_request(url, params=None, **kwargs):
        requested_urls.append(url)
        if url.endswith('/plugins/servlet/applinks/whoami'):
            return 'jdoe', {}
        return {
            'values': [
                {
                    'id': 5,
                    'slug': 'jdoe',
                    'name': 'jdoe',
                    'displayName': 'J Doe',
                    'emailAddress': 'j@example.com',
                    'avatarUrl': '/users/jdoe/avatar.png?s=64',
                }
            ]
        }, {}

    with patch.object(svc, '_make_request', side_effect=mock_make_request):
        user = await svc.get_user()

    assert requested_urls == [
        'https://host.example.com/plugins/servlet/applinks/whoami',
        'https://host.example.com/rest/api/1.0/users',
    ]
    assert user.id == '5'
    assert user.login == 'jdoe'
    assert user.name == 'J Doe'
    assert user.email == 'j@example.com'
    assert user.avatar_url == 'https://host.example.com/users/jdoe/avatar.png?s=64'


@pytest.mark.asyncio
async def test_get_user_without_user_id_returns_empty_when_resolution_fails():
    # Credentials not tied to a user (e.g. project/repo-scoped HTTP access
    # tokens) fail both username lookups; get_user degrades to an empty user.
    svc = BitbucketDCService(
        token=SecretStr('x-token-auth:mytoken'), base_domain='host.example.com'
    )
    with patch.object(
        svc, '_make_request', side_effect=Exception('401 Unauthorized')
    ) as mock_req:
        user = await svc.get_user()

    assert mock_req.call_count == 2  # whoami + X-AUSERNAME fallback, no /users
    assert isinstance(user, User)
    assert user.id == ''
    assert user.login == ''
    assert user.avatar_url == ''


@pytest.mark.asyncio
async def test_get_user_falls_back_to_x_ausername_when_whoami_is_empty():
    # Some Bitbucket DC servers answer whoami with 200 and an empty body for
    # HTTP access tokens. Fall back to the URL-encoded X-AUSERNAME header
    # from a cheap REST call. Mirrors a real Bitbucket 8.19 instance where
    # the user's name differs from the slug.
    svc = BitbucketDCService(
        token=SecretStr('x-token-auth:mytoken'), base_domain='host.example.com'
    )

    async def mock_make_request(url, params=None, **kwargs):
        if url.endswith('/plugins/servlet/applinks/whoami'):
            return '', {}
        if url.endswith('/projects'):
            return {'values': []}, {'X-AUSERNAME': 'Chris.Bagwell%40example.com'}
        return {
            'values': [
                {
                    'id': 2,
                    'slug': 'chris.bagwell_example.com',
                    'name': 'Chris.Bagwell@example.com',
                    'displayName': 'Bagwell, Chris',
                    'emailAddress': 'Chris.Bagwell@example.com',
                    'avatarUrl': '/users/chris.bagwell_example.com/avatar.png',
                }
            ]
        }, {}

    with patch.object(svc, '_make_request', side_effect=mock_make_request):
        user = await svc.get_user()

    assert user.id == '2'
    assert user.login == 'Chris.Bagwell@example.com'
    assert user.name == 'Bagwell, Chris'
    assert (
        user.avatar_url
        == 'https://host.example.com/users/chris.bagwell_example.com/avatar.png'
    )


@pytest.mark.asyncio
async def test_get_user_resolved_username_not_found_degrades_to_empty_user():
    # If the auto-resolved username yields no /users match, degrade to the
    # empty user instead of raising (explicitly-configured user_id still
    # raises, covered by test_get_user_raises_when_not_found).
    svc = BitbucketDCService(
        token=SecretStr('x-token-auth:mytoken'), base_domain='host.example.com'
    )

    async def mock_make_request(url, params=None, **kwargs):
        if url.endswith('/plugins/servlet/applinks/whoami'):
            return 'ghost-user', {}
        return {'values': []}, {}

    with patch.object(svc, '_make_request', side_effect=mock_make_request):
        user = await svc.get_user()

    assert user.id == ''
    assert user.login == ''


@pytest.mark.asyncio
async def test_get_user_without_user_id_or_base_url_skips_requests():
    svc = BitbucketDCService(token=SecretStr('x-token-auth:mytoken'), base_domain=None)
    with patch.object(svc, '_make_request') as mock_req:
        user = await svc.get_user()
        mock_req.assert_not_called()

    assert user.id == ''
    assert user.login == ''


def test_select_user_data_prefers_exact_match_over_first_result():
    # /users?filter= is a substring match; the requested user may not be first.
    users = [
        {'name': 'jdoe-bot', 'slug': 'jdoe-bot'},
        {'name': 'JDoe', 'slug': 'jdoe'},
    ]
    selected = BitbucketDCService._select_user_data(users, 'jdoe')
    assert selected is not None
    assert selected['slug'] == 'jdoe'


def test_select_user_data_falls_back_to_first_result():
    users = [{'name': 'someone-else', 'slug': 'someone-else'}]
    selected = BitbucketDCService._select_user_data(users, 'jdoe')
    assert selected is not None
    assert selected['slug'] == 'someone-else'


def test_select_user_data_empty_list_returns_none():
    assert BitbucketDCService._select_user_data([], 'jdoe') is None


@pytest.mark.asyncio
async def test_get_user_raises_when_not_found():
    svc = BitbucketDCService(
        token=SecretStr('tok'),
        base_domain='host.example.com',
        user_id='jdoe',
    )
    mock_response = {'values': []}
    with patch.object(svc, '_make_request', return_value=(mock_response, {})):
        with pytest.raises(AuthenticationError):
            await svc.get_user()


# ── _resolve_primary_email ────────────────────────────────────────────────────


def test_resolve_primary_email_selects_primary_confirmed():
    from openhands.app_server.integrations.bitbucket_data_center.service.base import (
        BitbucketDCMixinBase,
    )

    emails = [
        {'email': 'secondary@example.com', 'is_primary': False, 'is_confirmed': True},
        {'email': 'primary@example.com', 'is_primary': True, 'is_confirmed': True},
        {
            'email': 'unconfirmed@example.com',
            'is_primary': False,
            'is_confirmed': False,
        },
    ]
    result = BitbucketDCMixinBase._resolve_primary_email(emails)
    assert result == 'primary@example.com'


def test_resolve_primary_email_returns_none_when_no_primary():
    from openhands.app_server.integrations.bitbucket_data_center.service.base import (
        BitbucketDCMixinBase,
    )

    emails = [
        {'email': 'a@example.com', 'is_primary': False, 'is_confirmed': True},
        {'email': 'b@example.com', 'is_primary': False, 'is_confirmed': True},
    ]
    result = BitbucketDCMixinBase._resolve_primary_email(emails)
    assert result is None


def test_resolve_primary_email_returns_none_when_primary_not_confirmed():
    from openhands.app_server.integrations.bitbucket_data_center.service.base import (
        BitbucketDCMixinBase,
    )

    emails = [
        {'email': 'primary@example.com', 'is_primary': True, 'is_confirmed': False},
        {'email': 'other@example.com', 'is_primary': False, 'is_confirmed': True},
    ]
    result = BitbucketDCMixinBase._resolve_primary_email(emails)
    assert result is None


def test_resolve_primary_email_returns_none_for_empty_list():
    from openhands.app_server.integrations.bitbucket_data_center.service.base import (
        BitbucketDCMixinBase,
    )

    result = BitbucketDCMixinBase._resolve_primary_email([])
    assert result is None


# ── get_user_emails ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_user_emails():
    svc = BitbucketDCService(token=SecretStr('tok'), base_domain='host.example.com')
    mock_response = {
        'values': [
            {'email': 'primary@example.com', 'is_primary': True, 'is_confirmed': True},
            {
                'email': 'secondary@example.com',
                'is_primary': False,
                'is_confirmed': True,
            },
        ]
    }
    with patch.object(svc, '_make_request', return_value=(mock_response, {})):
        emails = await svc.get_user_emails()

    assert emails == mock_response['values']


# ── pagination (get_all_repositories iterates projects) ──────────────────────


@pytest.mark.asyncio
async def test_pagination_iterates_projects():
    svc = BitbucketDCService(token=SecretStr('tok'), base_domain='host.example.com')

    def _repo_dict(key='PROJ', slug='myrepo'):
        return {'id': 1, 'slug': slug, 'project': {'key': key}, 'public': False}

    async def fake_fetch(url, params, max_items):
        if '/projects' in url and '/repos' not in url:
            return [{'key': 'PROJ1'}, {'key': 'PROJ2'}]
        if 'PROJ1' in url:
            return [_repo_dict('PROJ1', 'repo1')]
        if 'PROJ2' in url:
            return [_repo_dict('PROJ2', 'repo2')]
        return []

    with patch.object(svc, '_fetch_paginated_data', side_effect=fake_fetch):
        repos = await svc.get_all_repositories('name', AppMode.SAAS)

    full_names = {r.full_name for r in repos}
    assert 'PROJ1/repo1' in full_names
    assert 'PROJ2/repo2' in full_names


# ── verify_access ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_access_makes_request():
    svc = BitbucketDCService(token=SecretStr('tok'), base_domain='host.example.com')
    with patch.object(svc, '_make_request', return_value=({}, {})) as mock_req:
        await svc.verify_access()

    mock_req.assert_called_once()
    call_url = mock_req.call_args[0][0]
    assert call_url.endswith('/projects')
