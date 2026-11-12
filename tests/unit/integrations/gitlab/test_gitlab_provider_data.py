"""GitLab webhook and repository rows are filed per auth provider.

GitLab ids are only unique within one instance, so two GitLab providers can hold
a project with the same id.
"""

from collections.abc import Awaitable, Callable, Iterator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from integrations.gitlab.gitlab_service import (
    SaaSGitLabService,
    provider_service_kwargs,
)
from integrations.store_repo_utils import store_repositories_in_db
from integrations.types import GitLabResourceType
from openhands.app_server.integrations.service_types import ProviderType, Repository
from storage.auth_provider import AuthProvider
from storage.gitlab_webhook import GitlabWebhook
from storage.gitlab_webhook_store import GitlabWebhookStore
from storage.stored_repository import StoredRepository
from storage.user_repo_map import UserRepositoryMap

OTHER_GITLAB_URL = 'https://gitlab.other.example.org'

CreateProvider = Callable[..., Awaitable[AuthProvider]]


@pytest.fixture(autouse=True)
def gitlab_data_stores(oauth_v2_stores, async_session_maker) -> Iterator[None]:
    with (
        patch('storage.gitlab_webhook_store.a_session_maker', async_session_maker),
        patch('storage.repository_store.a_session_maker', async_session_maker),
        patch('storage.user_repo_map_store.a_session_maker', async_session_maker),
    ):
        yield


@pytest.fixture
async def providers(create_git_provider: CreateProvider) -> tuple[AuthProvider, ...]:
    first = await create_git_provider(ProviderType.GITLAB)
    second = await create_git_provider(ProviderType.GITLAB, host=OTHER_GITLAB_URL)
    return first, second


def _project(project_id: str, provider_id: int | None) -> GitlabWebhook:
    return GitlabWebhook(
        project_id=project_id,
        group_id=None,
        user_id='user-1',
        webhook_exists=False,
        provider_id=provider_id,
    )


async def _webhooks(async_session_maker: async_sessionmaker) -> list[GitlabWebhook]:
    async with async_session_maker() as session:
        result = await session.execute(select(GitlabWebhook).order_by(GitlabWebhook.id))
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_two_providers_can_hold_a_project_with_the_same_id(
    providers, async_session_maker
):
    first, second = providers

    await GitlabWebhookStore().store_webhooks(
        [_project('5', first.id), _project('5', second.id)]
    )

    assert [w.provider_id for w in await _webhooks(async_session_maker)] == [
        first.id,
        second.id,
    ]


@pytest.mark.asyncio
async def test_a_project_is_stored_once_per_provider(providers, async_session_maker):
    first, _ = providers
    store = GitlabWebhookStore()

    await store.store_webhooks([_project('5', first.id)])
    await store.store_webhooks([_project('5', first.id)])

    assert len(await _webhooks(async_session_maker)) == 1


@pytest.mark.asyncio
async def test_a_project_without_a_provider_is_stored_once(async_session_maker):
    store = GitlabWebhookStore()

    await store.store_webhooks([_project('5', None)])
    await store.store_webhooks([_project('5', None)])

    assert len(await _webhooks(async_session_maker)) == 1


@pytest.mark.asyncio
async def test_a_group_is_stored_once_per_provider(providers, async_session_maker):
    first, second = providers
    store = GitlabWebhookStore()

    def group(provider_id: int) -> GitlabWebhook:
        return GitlabWebhook(
            group_id='9',
            project_id=None,
            user_id='user-1',
            webhook_exists=False,
            provider_id=provider_id,
        )

    await store.store_webhooks([group(first.id), group(second.id)])
    await store.store_webhooks([group(first.id)])

    assert len(await _webhooks(async_session_maker)) == 2


@pytest.mark.asyncio
async def test_webhook_lookups_only_see_the_named_provider(
    providers, async_session_maker
):
    first, second = providers
    store = GitlabWebhookStore()
    await store.store_webhooks([_project('5', first.id), _project('5', second.id)])

    found = await store.get_webhook_by_resource_only(
        GitLabResourceType.PROJECT, '5', provider_id=second.id
    )
    by_resource, _ = await store.get_webhooks_by_resources(
        ['5'], [], provider_id=first.id
    )
    missing = await store.get_webhook_by_resource_only(
        GitLabResourceType.PROJECT, '5', provider_id=None
    )

    assert found is not None
    assert found.provider_id == second.id
    assert by_resource['5'].provider_id == first.id
    assert missing is None


@pytest.mark.asyncio
async def test_webhook_writes_only_touch_the_named_provider(
    providers, async_session_maker
):
    first, second = providers
    store = GitlabWebhookStore()
    await store.store_webhooks([_project('5', first.id), _project('5', second.id)])

    await store.update_webhook(_project('5', first.id), {'webhook_exists': True})
    assert await store.request_reinstall(
        GitLabResourceType.PROJECT, '5', provider_id=second.id
    )
    assert await store.reset_webhook_for_reinstallation_by_resource(
        GitLabResourceType.PROJECT, '5', 'admin', provider_id=second.id
    )
    rows = {w.provider_id: w for w in await _webhooks(async_session_maker)}

    assert rows[first.id].webhook_exists is True
    assert rows[first.id].reinstall_requested_gen == 0
    assert rows[first.id].user_id == 'user-1'
    assert rows[second.id].webhook_exists is False
    assert rows[second.id].reinstall_requested_gen == 1
    assert rows[second.id].user_id == 'admin'


@pytest.mark.asyncio
async def test_deleting_a_webhook_leaves_the_same_project_of_another_provider(
    providers, async_session_maker
):
    first, second = providers
    store = GitlabWebhookStore()
    await store.store_webhooks([_project('5', first.id), _project('5', second.id)])

    await store.delete_webhook(_project('5', first.id))

    assert [w.provider_id for w in await _webhooks(async_session_maker)] == [second.id]


@pytest.mark.asyncio
async def test_deleting_a_provider_deletes_its_gitlab_rows(
    providers, async_session_maker
):
    first, second = providers
    await GitlabWebhookStore().store_webhooks(
        [_project('5', first.id), _project('5', second.id)]
    )
    await store_repositories_in_db([_repository('7')], 'user-1', first.id)

    async with async_session_maker() as session:
        await session.delete(await session.get(AuthProvider, first.id))
        await session.commit()

    assert [w.provider_id for w in await _webhooks(async_session_maker)] == [second.id]
    async with async_session_maker() as session:
        assert (await session.execute(select(StoredRepository))).first() is None
        assert (await session.execute(select(UserRepositoryMap))).first() is None


def _repository(repo_id: str, full_name: str = 'group/project') -> Repository:
    return Repository(
        id=repo_id,
        full_name=full_name,
        git_provider=ProviderType.GITLAB,
        is_public=False,
    )


@pytest.mark.asyncio
async def test_a_repository_id_is_stored_per_provider(providers, async_session_maker):
    first, second = providers

    await store_repositories_in_db(
        [_repository('7', 'one/project')], 'user-1', first.id
    )
    await store_repositories_in_db(
        [_repository('7', 'two/project')], 'user-1', second.id
    )

    async with async_session_maker() as session:
        repos = (await session.execute(select(StoredRepository))).scalars().all()
        mappings = (await session.execute(select(UserRepositoryMap))).scalars().all()
    assert {(r.provider_id, r.repo_name) for r in repos} == {
        (first.id, 'one/project'),
        (second.id, 'two/project'),
    }
    assert {(m.provider_id, m.repo_id) for m in mappings} == {
        (first.id, 'gitlab##7'),
        (second.id, 'gitlab##7'),
    }


@pytest.mark.asyncio
async def test_storing_a_repository_again_updates_it_in_place(
    providers, async_session_maker
):
    first, second = providers
    await store_repositories_in_db([_repository('7', 'old/name')], 'user-1', first.id)
    await store_repositories_in_db(
        [_repository('7', 'other/name')], 'user-1', second.id
    )

    await store_repositories_in_db([_repository('7', 'new/name')], 'user-1', first.id)

    async with async_session_maker() as session:
        repos = (await session.execute(select(StoredRepository))).scalars().all()
        mappings = (await session.execute(select(UserRepositoryMap))).scalars().all()
    assert {(r.provider_id, r.repo_name) for r in repos} == {
        (first.id, 'new/name'),
        (second.id, 'other/name'),
    }
    assert len(mappings) == 2


async def _store_repository_data(
    service: SaaSGitLabService, async_session_maker: async_sessionmaker
) -> int | None:
    """Store one personal project and one repository; return the project row's provider."""
    with patch.object(service, 'get_owned_groups', AsyncMock(return_value=[])):
        await service.store_repository_data(
            [{'id': 11}], [_repository('7', 'one/project')]
        )
    return (await _webhooks(async_session_maker))[-1].provider_id


@pytest.mark.asyncio
async def test_the_service_files_its_data_under_its_own_provider(
    providers, async_session_maker
):
    _, second = providers
    service = SaaSGitLabService(external_auth_id='user-1', auth_provider_id=second.id)

    assert await _store_repository_data(service, async_session_maker) == second.id
    async with async_session_maker() as session:
        repo = (await session.execute(select(StoredRepository))).scalars().one()
    assert repo.provider_id == second.id


@pytest.mark.asyncio
async def test_a_service_without_a_provider_files_its_data_under_the_primary_one(
    providers, async_session_maker
):
    first, _ = providers
    service = SaaSGitLabService(external_auth_id='user-1')

    assert await _store_repository_data(service, async_session_maker) == first.id


@pytest.mark.asyncio
@pytest.mark.usefixtures('oauth_v2_login_on')
async def test_a_provider_points_the_service_at_its_host(providers):
    _, second = providers

    kwargs = await provider_service_kwargs(second.id)
    service = SaaSGitLabService(external_auth_id='user-1', **kwargs)

    assert kwargs['auth_provider_id'] == second.id
    assert service.BASE_URL == f'{OTHER_GITLAB_URL}/api/v4'
    assert await provider_service_kwargs(None) == {}
    with pytest.raises(ValueError):
        await provider_service_kwargs(second.id + 100)


@pytest.mark.usefixtures('oauth_v2_login_off')
class TestWithOAuthV2LoginOff:
    """The Keycloak flow: no provider is named, the install-wide host is used."""

    @pytest.mark.asyncio
    async def test_a_row_provider_does_not_change_the_installers_host(
        self, providers, async_session_maker
    ):
        _, second = providers
        installer_service = SaaSGitLabService(
            external_auth_id='user-1', **await provider_service_kwargs(second.id)
        )
        legacy_service = SaaSGitLabService(external_auth_id='user-1')

        assert installer_service.auth_provider_id is None
        assert installer_service.BASE_URL == legacy_service.BASE_URL

    @pytest.mark.asyncio
    async def test_data_is_filed_under_the_primary_provider(
        self, providers, async_session_maker
    ):
        first, _ = providers
        service = SaaSGitLabService(external_auth_id='user-1')

        assert await _store_repository_data(service, async_session_maker) == first.id

    @pytest.mark.asyncio
    async def test_data_has_no_provider_when_none_is_configured(
        self, async_session_maker
    ):
        service = SaaSGitLabService(external_auth_id='user-1')

        assert await _store_repository_data(service, async_session_maker) is None
