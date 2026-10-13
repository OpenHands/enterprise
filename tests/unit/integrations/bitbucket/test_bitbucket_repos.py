"""Tests for Bitbucket repository service URL parsing."""

from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.app_server.integrations.bitbucket.bitbucket_service import (
    BitBucketService,
)
from openhands.app_server.integrations.service_types import OwnerType, Repository
from openhands.app_server.integrations.service_types import (
    ProviderType as ServiceProviderType,
)
from openhands.app_server.types import AppMode


@pytest.fixture
def bitbucket_service():
    """Create a BitBucketService instance for testing."""
    return BitBucketService(token=SecretStr('test-token'))


@pytest.mark.asyncio
async def test_search_repositories_url_parsing_standard_url(bitbucket_service):
    """Test URL parsing with standard Bitbucket URL and verify correct workspace/repo extraction."""
    mock_repo = Repository(
        id='1',
        full_name='workspace/repo',
        name='repo',
        owner=OwnerType.USER,
        git_provider=ServiceProviderType.BITBUCKET,
        is_public=True,
        clone_url='https://bitbucket.org/workspace/repo.git',
        html_url='https://bitbucket.org/workspace/repo',
    )

    with patch.object(
        bitbucket_service,
        'get_repository_details_from_repo_name',
        return_value=mock_repo,
    ) as mock_get_repo:
        url = 'https://bitbucket.org/workspace/repo'
        repositories = await bitbucket_service.search_repositories(
            query=url,
            per_page=10,
            sort='updated',
            order='desc',
            public=True,
            app_mode=AppMode.OPENHANDS,
        )

        # Verify the correct workspace/repo combination was extracted and passed
        assert len(repositories) == 1
        assert repositories[0].full_name == 'workspace/repo'
        mock_get_repo.assert_called_once_with('workspace/repo')


@pytest.mark.asyncio
async def test_search_repositories_url_parsing_with_extra_path_segments(
    bitbucket_service,
):
    """Test URL parsing with additional path segments and verify correct workspace/repo extraction."""
    mock_repo = Repository(
        id='1',
        full_name='my-workspace/my-repo',
        name='my-repo',
        owner=OwnerType.USER,
        git_provider=ServiceProviderType.BITBUCKET,
        is_public=True,
        clone_url='https://bitbucket.org/my-workspace/my-repo.git',
        html_url='https://bitbucket.org/my-workspace/my-repo',
    )

    with patch.object(
        bitbucket_service,
        'get_repository_details_from_repo_name',
        return_value=mock_repo,
    ) as mock_get_repo:
        # Test complex URL with query params, fragments, and extra paths
        url = 'https://bitbucket.org/my-workspace/my-repo/src/feature-branch/src/main.py?at=feature-branch&fileviewer=file-view-default#lines-25'
        repositories = await bitbucket_service.search_repositories(
            query=url,
            per_page=10,
            sort='updated',
            order='desc',
            public=True,
            app_mode=AppMode.OPENHANDS,
        )

        # Verify the correct workspace/repo combination was extracted from complex URL
        assert len(repositories) == 1
        assert repositories[0].full_name == 'my-workspace/my-repo'
        mock_get_repo.assert_called_once_with('my-workspace/my-repo')


@pytest.mark.asyncio
async def test_search_repositories_url_parsing_invalid_url(bitbucket_service):
    """Test URL parsing with invalid URL returns empty results."""
    with patch.object(
        bitbucket_service, 'get_repository_details_from_repo_name'
    ) as mock_get_repo:
        url = 'not-a-valid-url'
        repositories = await bitbucket_service.search_repositories(
            query=url,
            per_page=10,
            sort='updated',
            order='desc',
            public=True,
            app_mode=AppMode.OPENHANDS,
        )

        # Should return empty list for invalid URL and not call API
        assert len(repositories) == 0
        mock_get_repo.assert_not_called()


@pytest.mark.asyncio
async def test_search_repositories_url_parsing_insufficient_path_segments(
    bitbucket_service,
):
    """Test URL parsing with insufficient path segments returns empty results."""
    with patch.object(
        bitbucket_service, 'get_repository_details_from_repo_name'
    ) as mock_get_repo:
        url = 'https://bitbucket.org/workspace'
        repositories = await bitbucket_service.search_repositories(
            query=url,
            per_page=10,
            sort='updated',
            order='desc',
            public=True,
            app_mode=AppMode.OPENHANDS,
        )

        # Should return empty list for insufficient path segments and not call API
        assert len(repositories) == 0
        mock_get_repo.assert_not_called()


@pytest.mark.asyncio
async def test_search_repositories_passes_page_to_each_workspace(bitbucket_service):
    """A search across workspaces takes the same page from each workspace."""
    with (
        patch.object(
            bitbucket_service, 'get_installations', return_value=['ws1', 'ws2']
        ),
        patch.object(
            bitbucket_service, 'get_paginated_repos', return_value=[]
        ) as mock_paginated,
    ):
        await bitbucket_service.search_repositories(
            query='repo',
            per_page=10,
            sort='updated',
            order='desc',
            public=False,
            app_mode=AppMode.OPENHANDS,
            page=2,
        )

    pages = {call.args[0] for call in mock_paginated.call_args_list}
    workspaces = {call.args[3] for call in mock_paginated.call_args_list}
    assert pages == {2}
    assert workspaces == {'ws1', 'ws2'}


@pytest.mark.asyncio
async def test_search_repositories_url_has_no_later_page(bitbucket_service):
    """A repository URL names one repository, so a later page is empty."""
    with patch.object(
        bitbucket_service, 'get_repository_details_from_repo_name'
    ) as mock_get_repo:
        repositories = await bitbucket_service.search_repositories(
            query='https://bitbucket.org/workspace/repo',
            per_page=10,
            sort='updated',
            order='desc',
            public=True,
            app_mode=AppMode.OPENHANDS,
            page=2,
        )

    assert repositories == []
    mock_get_repo.assert_not_called()
