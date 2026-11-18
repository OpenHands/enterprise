"""``git_provider_id`` selects one connected git provider among several of a type.

Runs against the test database. Only the git provider's API is faked, by a
service class that records the token and host it was built with.
"""

from collections.abc import Callable
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from openhands.app_server.integrations.provider import ProviderToken
from openhands.app_server.integrations.service_types import ProviderType, Repository
from openhands.app_server.user.auth_user_context import AuthUserContext
from openhands.app_server.user_auth.user_auth import GitProviderSelection
from server.auth.saas_user_auth import SaasUserAuth, get_user_auth_from_keycloak_id
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import OAuthProviderDetails

HOST_A = 'gitlab.a.example.com'
HOST_B = 'gitlab.b.example.com'


class RecordingGitLabService:
    """Stands in for the GitLab API: finds any repository, remembers its inputs."""

    instances: list['RecordingGitLabService'] = []

    def __init__(
        self,
        user_id=None,
        token=None,
        external_auth_id=None,
        external_auth_token=None,
        external_token_manager=False,
        base_domain=None,
    ):
        self.token = token
        self.base_domain = base_domain
        self.auth_provider_id = None
        self.instances.append(self)

    async def get_repository_details_from_repo_name(self, repository: str):
        return Repository(
            id='1',
            full_name=repository,
            git_provider=ProviderType.GITLAB,
            is_public=False,
        )


@pytest.fixture
def two_gitlabs(create_git_provider, store_oauth_token, create_user):
    """A user linked to two GitLab hosts and to GitHub, each with its own token."""

    async def _create():
        user = create_user()
        gitlab_a = await create_git_provider(
            ProviderType.GITLAB, host=f'https://{HOST_A}'
        )
        gitlab_b = await create_git_provider(
            ProviderType.GITLAB, host=f'https://{HOST_B}'
        )
        github = await create_git_provider(ProviderType.GITHUB)
        await store_oauth_token(user.id, gitlab_a, 'glpat-a')
        await store_oauth_token(user.id, gitlab_b, 'glpat-b')
        await store_oauth_token(user.id, github, 'ghp-github')
        auth = await get_user_auth_from_keycloak_id(str(user.id))
        assert isinstance(auth, SaasUserAuth)
        return user, gitlab_a, gitlab_b, auth

    return _create


def _gitlab(tokens) -> ProviderToken:
    return tokens[ProviderType.GITLAB]


@pytest.mark.asyncio
async def test_each_id_gets_its_own_token_and_host(
    oauth_v2_login_on, no_keycloak, two_gitlabs
):
    _, gitlab_a, gitlab_b, auth = await two_gitlabs()

    with patch.object(auth, 'get_secrets', AsyncMock(return_value=None)):
        tokens_a = await auth.get_provider_tokens(gitlab_a.id)
        tokens_b = await auth.get_provider_tokens(gitlab_b.id)

    assert _gitlab(tokens_a).token == SecretStr('glpat-a')
    assert _gitlab(tokens_a).host == HOST_A
    assert _gitlab(tokens_b).token == SecretStr('glpat-b')
    assert _gitlab(tokens_b).host == HOST_B


@pytest.mark.asyncio
async def test_an_id_replaces_only_the_entry_of_its_type(
    oauth_v2_login_on, no_keycloak, two_gitlabs
):
    _, gitlab_a, _, auth = await two_gitlabs()

    with patch.object(auth, 'get_secrets', AsyncMock(return_value=None)):
        unscoped = await auth.get_provider_tokens()
        scoped = await auth.get_provider_tokens(gitlab_a.id)

    assert unscoped is not None and scoped is not None
    assert set(scoped) == set(unscoped)
    assert scoped[ProviderType.GITHUB] == unscoped[ProviderType.GITHUB]
    assert _gitlab(scoped).token == SecretStr('glpat-a')


@pytest.mark.asyncio
async def test_the_scoped_mapping_does_not_leak_into_the_unscoped_one(
    oauth_v2_login_on, no_keycloak, two_gitlabs
):
    _, gitlab_a, _, auth = await two_gitlabs()

    with patch.object(auth, 'get_secrets', AsyncMock(return_value=None)):
        before = await auth.get_provider_tokens()
        await auth.get_provider_tokens(gitlab_a.id)
        after = await auth.get_provider_tokens()

    assert after is before
    assert after == await auth.get_provider_tokens(None)


@pytest.mark.asyncio
async def test_no_id_leaves_selection_alone_even_with_the_flag_on(
    oauth_v2_login_on, no_keycloak, two_gitlabs
):
    _, _, _, auth = await two_gitlabs()

    assert await auth.select_git_provider(None, None) == GitProviderSelection()
    assert await auth.select_git_provider(
        ProviderType.GITLAB, None
    ) == GitProviderSelection(git_provider=ProviderType.GITLAB)


@pytest.mark.asyncio
async def test_the_id_is_ignored_with_the_flag_off(
    oauth_v2_login_off, no_keycloak, two_gitlabs, create_git_provider
):
    _, gitlab_a, _, auth = await two_gitlabs()

    with patch.object(auth, 'get_secrets', AsyncMock(return_value=None)):
        by_default = await auth.get_provider_tokens()
        # A real id, an unlinked row and an id that names nothing: none matters.
        for git_provider_id in (gitlab_a.id, 987654):
            assert await auth.get_provider_tokens(git_provider_id) is by_default
            assert await auth.select_git_provider(
                ProviderType.GITHUB, git_provider_id
            ) == GitProviderSelection(git_provider=ProviderType.GITHUB)


@pytest.mark.asyncio
async def test_an_id_the_user_did_not_link_is_a_404(
    oauth_v2_login_on, no_keycloak, two_gitlabs, create_git_provider, create_user
):
    _, _, _, auth = await two_gitlabs()
    unlinked = await create_git_provider(ProviderType.GITLAB, host='https://gl.c.test')

    with patch.object(auth, 'get_secrets', AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await auth.get_provider_tokens(unlinked.id)
        assert exc_info.value.status_code == 404
        with pytest.raises(HTTPException) as exc_info:
            await auth.select_git_provider(ProviderType.GITLAB, unlinked.id)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_a_link_of_another_user_does_not_count(
    oauth_v2_login_on,
    no_keycloak,
    two_gitlabs,
    create_git_provider,
    store_oauth_token,
    create_user,
):
    _, _, _, auth = await two_gitlabs()
    theirs = await create_git_provider(ProviderType.GITLAB, host='https://gl.c.test')
    await store_oauth_token(create_user().id, theirs, 'glpat-theirs')

    with pytest.raises(HTTPException) as exc_info:
        await auth.select_git_provider(None, theirs.id)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_an_id_that_names_nothing_is_a_404(
    oauth_v2_login_on, no_keycloak, two_gitlabs
):
    _, _, _, auth = await two_gitlabs()

    with pytest.raises(HTTPException) as exc_info:
        await auth.select_git_provider(None, 987654)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_a_row_that_is_not_a_git_provider_is_a_404(
    oauth_v2_login_on,
    no_keycloak,
    two_gitlabs,
    store_oauth_token,
    async_session_maker,
):
    user, _, _, auth = await two_gitlabs()
    login_only = AuthProvider(
        title='SSO',
        is_idp=True,
        details=OAuthProviderDetails(
            provider_type=ProviderType.ENTERPRISE_SSO, client_id='c'
        ),
    )
    async with async_session_maker() as session:
        session.add(login_only)
        await session.commit()
        await session.refresh(login_only)
    await store_oauth_token(user.id, login_only, 'sso-token')

    with pytest.raises(HTTPException) as exc_info:
        await auth.select_git_provider(None, login_only.id)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_a_provider_type_that_differs_from_the_row_is_a_400(
    oauth_v2_login_on, no_keycloak, two_gitlabs
):
    _, gitlab_a, _, auth = await two_gitlabs()

    with pytest.raises(HTTPException) as exc_info:
        await auth.select_git_provider(ProviderType.GITHUB, gitlab_a.id)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_a_missing_provider_type_is_taken_from_the_row(
    oauth_v2_login_on, no_keycloak, two_gitlabs
):
    _, gitlab_a, _, auth = await two_gitlabs()

    assert await auth.select_git_provider(None, gitlab_a.id) == GitProviderSelection(
        git_provider=ProviderType.GITLAB, git_provider_id=gitlab_a.id
    )
    assert await auth.select_git_provider(
        ProviderType.GITLAB, gitlab_a.id
    ) == GitProviderSelection(
        git_provider=ProviderType.GITLAB, git_provider_id=gitlab_a.id
    )


@pytest.mark.asyncio
async def test_a_linked_row_without_a_usable_token_must_be_reconnected(
    oauth_v2_login_on, no_keycloak, two_gitlabs
):
    _, gitlab_a, _, auth = await two_gitlabs()

    with (
        patch.object(auth, 'get_secrets', AsyncMock(return_value=None)),
        patch(
            'storage.oauth_token_store.OAuthTokenStore.get_valid_access_token',
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await auth.get_provider_tokens(gitlab_a.id)

    assert exc_info.value.status_code == 403


@pytest.fixture
def recording_gitlab() -> Callable[[AuthUserContext], None]:
    RecordingGitLabService.instances = []

    def _install(context: AuthUserContext) -> None:
        context._provider_handlers.clear()
        original = context.get_provider_handler

        async def get_provider_handler(git_provider_id=None):
            handler = await original(git_provider_id)
            handler.service_class_map[ProviderType.GITLAB] = RecordingGitLabService
            return handler

        context.get_provider_handler = get_provider_handler  # type: ignore[method-assign]

    return _install


@pytest.mark.asyncio
async def test_the_clone_url_uses_the_token_and_host_of_the_chosen_row(
    oauth_v2_login_on, no_keycloak, two_gitlabs, recording_gitlab
):
    _, gitlab_a, gitlab_b, auth = await two_gitlabs()
    context = AuthUserContext(user_auth=auth)
    recording_gitlab(context)

    with patch.object(auth, 'get_secrets', AsyncMock(return_value=None)):
        url_a = await context.get_authenticated_git_url(
            'tofarr/servey',
            specified_provider=ProviderType.GITLAB,
            git_provider_id=gitlab_a.id,
        )
        url_b = await context.get_authenticated_git_url(
            'tofarr/servey',
            specified_provider=ProviderType.GITLAB,
            git_provider_id=gitlab_b.id,
        )

    assert url_a == f'https://oauth2:glpat-a@{HOST_A}/tofarr/servey.git'
    assert url_b == f'https://oauth2:glpat-b@{HOST_B}/tofarr/servey.git'


@pytest.mark.asyncio
async def test_the_handler_is_cached_per_id_and_remembers_the_row(
    oauth_v2_login_on, no_keycloak, two_gitlabs
):
    _, gitlab_a, gitlab_b, auth = await two_gitlabs()
    context = AuthUserContext(user_auth=auth)

    with patch.object(auth, 'get_secrets', AsyncMock(return_value=None)):
        handler_a = await context.get_provider_handler(gitlab_a.id)
        handler_b = await context.get_provider_handler(gitlab_b.id)
        unscoped = await context.get_provider_handler()

        assert await context.get_provider_handler(gitlab_a.id) is handler_a
        assert await context.get_provider_handler() is unscoped
    assert handler_a is not handler_b
    assert handler_a.auth_provider_ids == {ProviderType.GITLAB: gitlab_a.id}
    assert handler_b.auth_provider_ids == {ProviderType.GITLAB: gitlab_b.id}
    assert unscoped.auth_provider_ids == {}
    assert handler_a.get_service(ProviderType.GITLAB).auth_provider_id == gitlab_a.id


@pytest.mark.asyncio
async def test_without_an_id_the_handler_is_as_before(
    oauth_v2_login_on, no_keycloak, two_gitlabs
):
    _, _, _, auth = await two_gitlabs()
    context = AuthUserContext(user_auth=auth)

    with patch.object(auth, 'get_secrets', AsyncMock(return_value=None)):
        handler = await context.get_provider_handler()
        tokens = await auth.get_provider_tokens()

    assert handler.provider_tokens == MappingProxyType(dict(tokens or {}))
    assert handler.auth_provider_ids == {}
    assert handler.get_service(ProviderType.GITLAB).auth_provider_id is None


@pytest.mark.asyncio
async def test_the_latest_token_is_that_of_the_chosen_row(
    oauth_v2_login_on, no_keycloak, two_gitlabs
):
    _, gitlab_a, gitlab_b, auth = await two_gitlabs()
    context = AuthUserContext(user_auth=auth)

    with patch.object(auth, 'get_secrets', AsyncMock(return_value=None)):
        token_a = await context.get_latest_token(ProviderType.GITLAB, gitlab_a.id)
        token_b = await context.get_latest_token(ProviderType.GITLAB, gitlab_b.id)

    assert (token_a, token_b) == ('glpat-a', 'glpat-b')


@pytest.mark.asyncio
async def test_the_chosen_row_wins_a_shared_named_secret(
    oauth_v2_login_on, no_keycloak, create_git_provider, store_oauth_token, create_user
):
    user = create_user()
    gitlab_a = await create_git_provider(ProviderType.GITLAB, host=f'https://{HOST_A}')
    gitlab_b = await create_git_provider(ProviderType.GITLAB, host=f'https://{HOST_B}')
    for provider in (gitlab_a, gitlab_b):
        provider.details.named_secret = 'GITLAB_TOKEN'
        await _save_details(provider)
        await store_oauth_token(user.id, provider, f'glpat-{provider.id}')
    auth = await get_user_auth_from_keycloak_id(str(user.id))

    def secret_rows(named):
        return {s.secret_name: s.oauth_provider_id for s in named.secrets}

    assert secret_rows(await auth.get_named_oauth_secrets()) == {
        'GITLAB_TOKEN': gitlab_b.id
    }
    assert secret_rows(await auth.get_named_oauth_secrets(gitlab_a.id)) == {
        'GITLAB_TOKEN': gitlab_a.id
    }


async def _save_details(provider: AuthProvider) -> None:
    from storage.auth_provider_store import AuthProviderStore

    await AuthProviderStore().update(provider.id, details=provider.details)
