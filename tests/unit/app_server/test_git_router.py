"""Unit tests for the git_router endpoints.

This module tests the git router endpoints,
focusing on pagination and error handling.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from openhands.app_server.git.git_models import SortOrder
from openhands.app_server.git.git_router import (
    router,
    search_branches,
    search_repositories,
    search_suggested_tasks,
    search_user_installations,
)
from openhands.app_server.integrations.provider import ProviderToken
from openhands.app_server.integrations.service_types import (
    Branch,
    PaginatedBranchesResponse,
    ProviderType,
    Repository,
    SuggestedTask,
    TaskType,
)
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.dependencies import check_session_api_key
from openhands.app_server.utils.paging_utils import encode_page_id, paginate_results


class TestPagination:
    """Test suite for pagination helper function."""

    def test_returns_first_page_when_no_page_id(self):
        """Test that first page is returned when no page_id is provided."""
        items = ['a', 'b', 'c', 'd', 'e']

        result, next_page_id = paginate_results(items, None, 2)

        assert result == ['a', 'b']
        # next_page_id is base64-encoded
        assert next_page_id == encode_page_id(2)

    def test_returns_second_page_when_page_id_provided(self):
        """Test that correct page is returned when page_id is provided."""
        items = ['a', 'b', 'c', 'd', 'e']
        # Use base64-encoded page_id
        encoded_page_id = encode_page_id(2)

        result, next_page_id = paginate_results(items, encoded_page_id, 2)

        assert result == ['c', 'd']
        assert next_page_id == encode_page_id(4)

    def test_returns_empty_when_page_id_exceeds_length(self):
        """Test that empty list is returned when page_id exceeds length."""
        items = ['a', 'b', 'c']
        # Use base64-encoded page_id
        encoded_page_id = encode_page_id(10)

        result, next_page_id = paginate_results(items, encoded_page_id, 2)

        assert result == []
        assert next_page_id is None

    def test_returns_none_next_page_when_last_page(self):
        """Test that next_page_id is None on last page."""
        items = ['a', 'b', 'c']
        # Use base64-encoded page_id
        encoded_page_id = encode_page_id(2)

        result, next_page_id = paginate_results(items, encoded_page_id, 2)

        assert result == ['c']
        assert next_page_id is None

    def test_respects_limit(self):
        """Test that limit is respected."""
        items = ['a', 'b', 'c', 'd', 'e']

        result, next_page_id = paginate_results(items, None, 5)

        assert result == items
        assert next_page_id is None


def _make_mock_user_context(
    provider_tokens: dict | None = None,
    user_id: str = 'test-user-id',
):
    """Create a mock UserContext for testing."""
    context = MagicMock(spec=UserContext)
    context.get_provider_tokens = AsyncMock(return_value=provider_tokens)
    context.get_user_id = AsyncMock(return_value=user_id)
    return context


def _make_mock_provider_handler():
    """Create a mock ProviderHandler."""
    handler = MagicMock()
    handler.get_github_installations = AsyncMock(
        return_value=['inst-1', 'inst-2', 'inst-3', 'inst-4', 'inst-5']
    )
    handler.get_bitbucket_workspaces = AsyncMock(return_value=['ws-1', 'ws-2'])
    handler.get_repositories = AsyncMock(return_value=[])
    return handler


@pytest.fixture
def test_client():
    """Create a test client with the actual git router and mocked dependencies.

    We override check_session_api_key to bypass auth checks.
    This allows us to test the actual Query parameter validation in the router.
    """
    app = FastAPI()
    app.include_router(router)

    # Override the auth dependency to always pass
    app.dependency_overrides[check_session_api_key] = lambda: None

    client = TestClient(app, raise_server_exceptions=False)
    yield client

    # Clean up
    app.dependency_overrides.clear()


class TestInstallationsEndpoint:
    """Test suite for /installations endpoint."""

    def test_returns_403_when_no_provider_tokens(self, test_client):
        """Test that 403 is returned when no provider tokens."""
        with patch(
            'openhands.app_server.user.auth_user_context.AuthUserContext.get_provider_tokens',
            AsyncMock(return_value=None),
        ):
            response = test_client.get(
                '/git/installations/search', params={'provider': 'github'}
            )
            assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_returns_422_for_unsupported_provider(self, test_client):
        """Test that 422 is returned for unsupported provider."""
        with patch(
            'openhands.app_server.user.auth_user_context.AuthUserContext.get_provider_tokens',
            AsyncMock(return_value={'github': 'token'}),
        ):
            response = test_client.get(
                '/git/installations/search', params={'provider': 'invalid'}
            )
            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
class TestSearchUserInstallations:
    """Test suite for search_user_installations function."""

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_returns_paginated_installations(self, mock_handler_cls):
        """Test that installations are returned with pagination."""
        # Arrange
        mock_handler = _make_mock_provider_handler()
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act
        result = await search_user_installations(
            provider=ProviderType.GITHUB,
            page_id=None,
            limit=2,
            user_context=mock_context,
        )

        # Assert
        assert result.items == ['inst-1', 'inst-2']
        # next_page_id is base64-encoded
        assert result.next_page_id == encode_page_id(2)

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_returns_second_page_correctly(self, mock_handler_cls):
        """Test that second page of installations is returned correctly."""
        # Arrange
        mock_handler = _make_mock_provider_handler()
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act - request second page
        result = await search_user_installations(
            provider=ProviderType.GITHUB,
            page_id=encode_page_id(2),  # Second page starts at offset 2
            limit=2,
            user_context=mock_context,
        )

        # Assert
        assert result.items == ['inst-3', 'inst-4']
        assert result.next_page_id == encode_page_id(4)


@pytest.mark.asyncio
class TestSearchRepositories:
    """Test suite for search_repositories function (handles both user repos and search)."""

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_returns_user_repositories_without_query(self, mock_handler_cls):
        """Test that repositories_search returns user repositories when no query is provided."""
        # Arrange
        mock_handler = MagicMock()
        mock_handler.get_repositories = AsyncMock(
            return_value=[
                Repository(
                    id='1',
                    full_name='user/repo1',
                    git_provider=ProviderType.GITHUB,
                    is_public=True,
                ),
                Repository(
                    id='2',
                    full_name='user/repo2',
                    git_provider=ProviderType.GITHUB,
                    is_public=False,
                ),
                Repository(
                    id='3',
                    full_name='user/repo3',
                    git_provider=ProviderType.GITHUB,
                    is_public=True,
                ),
            ]
        )
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act - call without query to get user repos
        result = await search_repositories(
            provider=ProviderType.GITHUB,
            query=None,
            installation_id=None,
            page_id=None,
            limit=10,
            user_context=mock_context,
        )

        # Assert
        assert len(result.items) == 3
        assert result.items[0].id == '1'
        assert result.items[1].id == '2'
        assert result.items[2].id == '3'
        assert result.next_page_id is None  # No more pages

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_search_repositories_with_query(self, mock_handler_cls):
        """Test repository search when query is provided.

        This tests the search path (with query) which calls search_repositories
        instead of get_repositories, and verifies sort_order is parsed correctly.
        """
        # Arrange
        mock_handler = MagicMock()
        mock_handler.search_repositories = AsyncMock(
            return_value=[
                Repository(
                    id='10',
                    full_name='org/searched-repo',
                    git_provider=ProviderType.GITHUB,
                    is_public=True,
                ),
                Repository(
                    id='11',
                    full_name='user/searched-repo',
                    git_provider=ProviderType.GITHUB,
                    is_public=False,
                ),
            ]
        )
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act - call with query and sort_order to trigger search path
        result = await search_repositories(
            provider=ProviderType.GITHUB,
            query='my-search-term',
            installation_id=None,
            page_id=None,
            limit=10,
            sort_order=SortOrder.STAR_DESC,  # This should be parsed into sort='stars', order='desc'
            user_context=mock_context,
        )

        # Assert - verify search_repositories was called (not get_repositories)
        mock_handler.search_repositories.assert_called_once()
        call_kwargs = mock_handler.search_repositories.call_args.kwargs

        # Verify query is passed
        assert call_kwargs.get('query') == 'my-search-term'

        # Verify sort and order are parsed from sort_order ('stars-desc' -> sort='stars', order='desc')
        assert call_kwargs.get('sort') == 'stars'
        assert call_kwargs.get('order') == 'desc'

        # Verify per_page is the page size
        assert call_kwargs.get('per_page') == 10

        # Verify results are returned
        assert len(result.items) == 2
        assert result.items[0].id == '10'
        assert result.items[1].id == '11'

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_search_repositories_sort_order_asc(self, mock_handler_cls):
        """Test that sort_order ascending is parsed correctly."""
        # Arrange
        mock_handler = MagicMock()
        mock_handler.search_repositories = AsyncMock(return_value=[])
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act - call with sort_order ascending
        await search_repositories(
            provider=ProviderType.GITHUB,
            query='test',
            installation_id=None,
            page_id=None,
            limit=10,
            sort_order=SortOrder.FORKS_ASC,
            user_context=mock_context,
        )

        # Assert
        call_kwargs = mock_handler.search_repositories.call_args.kwargs
        assert call_kwargs.get('sort') == 'forks'
        assert call_kwargs.get('order') == 'asc'

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_search_repositories_default_sort_order(self, mock_handler_cls):
        """Test default sort order when sort_order is not provided."""
        # Arrange
        mock_handler = MagicMock()
        mock_handler.search_repositories = AsyncMock(return_value=[])
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act - call with query but no sort_order (uses default: stars, desc)
        await search_repositories(
            provider=ProviderType.GITHUB,
            query='test',
            installation_id=None,
            page_id=None,
            limit=10,
            sort_order=None,
            user_context=mock_context,
        )

        # Assert - defaults should be used
        call_kwargs = mock_handler.search_repositories.call_args.kwargs
        assert call_kwargs.get('sort') == 'stars'  # Default
        assert call_kwargs.get('order') == 'desc'  # Default

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_pagination_works_across_pages(self, mock_handler_cls):
        """Following next_page_id must show every repository exactly once.

        The providers use page-number pagination: page N returns items
        (N-1)*per_page+1 .. N*per_page. The mock behaves the same way.
        """
        # Arrange
        all_repos = [
            Repository(
                id=str(i),
                full_name=f'user/repo{i}',
                git_provider=ProviderType.GITHUB,
                is_public=True,
            )
            for i in range(1, 101)
        ]

        def mock_get_repositories(**kwargs):
            start = (kwargs['page'] - 1) * kwargs['per_page']
            return all_repos[start : start + kwargs['per_page']]

        mock_handler = MagicMock()
        mock_handler.get_repositories = AsyncMock(side_effect=mock_get_repositories)
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act - follow next_page_id until the last page
        seen_ids: list[str] = []
        page_sizes: list[int] = []
        page_id = None
        for _ in range(10):
            result = await search_repositories(
                provider=ProviderType.GITHUB,
                query=None,
                installation_id=None,
                page_id=page_id,
                limit=30,
                sort_order=None,
                user_context=mock_context,
            )
            seen_ids.extend(repo.id for repo in result.items)
            page_sizes.append(len(result.items))
            page_id = result.next_page_id
            if page_id is None:
                break

        # Assert
        assert seen_ids == [str(i) for i in range(1, 101)]
        assert page_sizes == [30, 30, 30, 10]

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_full_last_page_gives_next_page_id_then_empty_page(
        self, mock_handler_cls
    ):
        """A full page gives a next_page_id, because the router cannot know it is the last.

        When the total is a multiple of limit, the client gets one empty page
        with no next_page_id.
        """
        # Arrange
        all_repos = [
            Repository(
                id=str(i),
                full_name=f'user/repo{i}',
                git_provider=ProviderType.GITHUB,
                is_public=True,
            )
            for i in range(1, 5)
        ]

        def mock_get_repositories(**kwargs):
            start = (kwargs['page'] - 1) * kwargs['per_page']
            return all_repos[start : start + kwargs['per_page']]

        mock_handler = MagicMock()
        mock_handler.get_repositories = AsyncMock(side_effect=mock_get_repositories)
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act
        result_page2 = await search_repositories(
            provider=ProviderType.GITHUB,
            query=None,
            installation_id=None,
            page_id=encode_page_id(2),
            limit=2,
            sort_order=None,
            user_context=mock_context,
        )
        result_page3 = await search_repositories(
            provider=ProviderType.GITHUB,
            query=None,
            installation_id=None,
            page_id=result_page2.next_page_id,
            limit=2,
            sort_order=None,
            user_context=mock_context,
        )

        # Assert
        assert [repo.id for repo in result_page2.items] == ['3', '4']
        assert result_page2.next_page_id == encode_page_id(3)
        assert result_page3.items == []
        assert result_page3.next_page_id is None

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_passes_sort_parameter_to_provider(self, mock_handler_cls):
        """Test that sort parameter is passed through to the provider handler."""
        # Arrange
        mock_handler = MagicMock()
        mock_handler.get_repositories = AsyncMock(return_value=[])
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act
        await search_repositories(
            provider=ProviderType.GITHUB,
            query=None,
            installation_id=None,
            page_id=None,
            limit=10,
            user_context=mock_context,
        )

        # Assert - verify get_repositories was called with the sort parameter
        mock_handler.get_repositories.assert_called_once()
        call_kwargs = mock_handler.get_repositories.call_args.kwargs
        assert call_kwargs.get('sort') == 'pushed'

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_passes_installation_id_to_provider(self, mock_handler_cls):
        """Test that installation_id filtering is passed through to the provider."""
        # Arrange
        mock_handler = MagicMock()
        mock_handler.get_repositories = AsyncMock(return_value=[])
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act
        await search_repositories(
            provider=ProviderType.GITHUB,
            query=None,
            installation_id='app-123',
            page_id=None,
            limit=10,
            sort_order=None,
            user_context=mock_context,
        )

        # Assert - verify get_repositories was called with installation_id
        mock_handler.get_repositories.assert_called_once()
        call_kwargs = mock_handler.get_repositories.call_args.kwargs
        assert call_kwargs.get('installation_id') == 'app-123'

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_search_results_are_a_single_page(self, mock_handler_cls):
        """Search with a query returns at most limit items and no next_page_id.

        search_repositories takes no page number, so a next page would only
        repeat the first page.
        """
        # Arrange
        mock_handler = MagicMock()
        mock_handler.search_repositories = AsyncMock(
            return_value=[
                Repository(
                    id='1',
                    full_name='user/repo1',
                    git_provider=ProviderType.GITHUB,
                    is_public=True,
                ),
                Repository(
                    id='2',
                    full_name='user/repo2',
                    git_provider=ProviderType.GITHUB,
                    is_public=True,
                ),
                Repository(
                    id='3',
                    full_name='user/repo3',
                    git_provider=ProviderType.GITHUB,
                    is_public=True,
                ),
            ]
        )
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act
        result = await search_repositories(
            provider=ProviderType.GITHUB,
            query='test',
            page_id=None,
            limit=2,
            sort_order=SortOrder.STAR_DESC,
            user_context=mock_context,
        )

        # Assert
        assert result.items == [
            Repository(
                id='1',
                full_name='user/repo1',
                git_provider=ProviderType.GITHUB,
                is_public=True,
            ),
            Repository(
                id='2',
                full_name='user/repo2',
                git_provider=ProviderType.GITHUB,
                is_public=True,
            ),
        ]
        assert result.next_page_id is None

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_search_with_query_rejects_non_first_page(self, mock_handler_cls):
        """Search with a query returns 400 for a page after the first.

        search_repositories takes no page number, so it cannot return a later page.
        """
        # Arrange
        mock_handler = MagicMock()
        mock_handler.search_repositories = AsyncMock(return_value=[])
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act
        with pytest.raises(HTTPException) as exc_info:
            await search_repositories(
                provider=ProviderType.GITHUB,
                query='test',
                installation_id=None,
                page_id=encode_page_id(2),
                limit=2,
                sort_order=None,
                user_context=mock_context,
            )

        # Assert
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        mock_handler.search_repositories.assert_not_called()

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_parses_sort_order_correctly(self, mock_handler_cls):
        """Test that sort_order enum is parsed into sort and order components."""
        # Arrange
        mock_handler = MagicMock()
        mock_handler.search_repositories = AsyncMock(return_value=[])
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act
        await search_repositories(
            provider=ProviderType.GITHUB,
            query='test',
            page_id=None,
            limit=5,
            sort_order=SortOrder.FORKS_ASC,
            user_context=mock_context,
        )

        # Assert - verify search_repositories was called with parsed sort and order
        mock_handler.search_repositories.assert_called_once()
        call_kwargs = mock_handler.search_repositories.call_args.kwargs
        assert call_kwargs.get('sort') == 'forks'
        assert call_kwargs.get('order') == 'asc'

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_returns_empty_first_page(self, mock_handler_cls):
        """Test that empty results return empty items with no next_page_id."""
        # Arrange
        mock_handler = MagicMock()
        mock_handler.search_repositories = AsyncMock(return_value=[])
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act
        result = await search_repositories(
            provider=ProviderType.GITHUB,
            query='nonexistent-repo-xyz',
            page_id=None,
            limit=5,
            sort_order=SortOrder.STAR_DESC,
            user_context=mock_context,
        )

        # Assert
        assert result.items == []
        assert result.next_page_id is None

    def test_returns_403_when_no_provider_tokens(self, test_client, monkeypatch):
        """Test that 403 is returned when no provider tokens."""
        with patch(
            'openhands.app_server.user.auth_user_context.AuthUserContext.get_provider_tokens',
            AsyncMock(return_value=None),
        ):
            response = test_client.get(
                '/git/repositories/search',
                params={'provider': 'github', 'query': 'test'},
            )
            assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
class TestSearchBranches:
    """Test suite for search_branches function."""

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_search_results_are_a_single_page(self, mock_handler_cls):
        """Branch search with a query returns at most limit items and no next_page_id.

        Branch search returns 400 for a page after the first, so a next page
        must not be advertised.
        """
        # Arrange
        mock_handler = MagicMock()
        mock_handler.search_branches = AsyncMock(
            return_value=[
                Branch(name='main', commit_sha='abc123', protected=False),
                Branch(name='develop', commit_sha='def456', protected=False),
                Branch(name='feature-branch', commit_sha='ghi789', protected=False),
            ]
        )
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act
        result = await search_branches(
            provider=ProviderType.GITHUB,
            repository='user/repo',
            query='main',
            page_id=None,
            limit=2,
            user_context=mock_context,
        )

        # Assert
        assert len(result.items) == 2
        assert result.items[0].name == 'main'
        assert result.items[1].name == 'develop'
        assert result.next_page_id is None

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_passes_parameters_to_provider(self, mock_handler_cls):
        """Test that all parameters are passed through to the provider."""
        # Arrange
        mock_handler = MagicMock()
        mock_handler.search_branches = AsyncMock(return_value=[])
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act
        await search_branches(
            provider=ProviderType.GITHUB,
            repository='user/repo',
            query='feature',
            page_id=None,
            limit=10,
            user_context=mock_context,
        )

        # Assert
        mock_handler.search_branches.assert_called_once()
        call_kwargs = mock_handler.search_branches.call_args.kwargs
        assert call_kwargs.get('selected_provider') == ProviderType.GITHUB
        assert call_kwargs.get('repository') == 'user/repo'
        assert call_kwargs.get('query') == 'feature'
        assert call_kwargs.get('per_page') == 10  # the page size

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_listing_pagination_works_across_pages(self, mock_handler_cls):
        """Following next_page_id with an empty query must show every branch once.

        The providers use page-number pagination and report has_next_page. The
        mock behaves the same way.
        """
        # Arrange
        all_branches = [
            Branch(name=f'branch{i}', commit_sha=f'sha{i}', protected=False)
            for i in range(1, 101)
        ]

        def mock_get_branches(**kwargs):
            page = kwargs['page']
            per_page = kwargs['per_page']
            start = (page - 1) * per_page
            return PaginatedBranchesResponse(
                branches=all_branches[start : start + per_page],
                has_next_page=start + per_page < len(all_branches),
                current_page=page,
                per_page=per_page,
                total_count=len(all_branches),
            )

        mock_handler = MagicMock()
        mock_handler.get_branches = AsyncMock(side_effect=mock_get_branches)
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act - follow next_page_id until the last page
        seen_names: list[str] = []
        page_id = None
        for _ in range(10):
            result = await search_branches(
                provider=ProviderType.GITHUB,
                repository='user/repo',
                query='',
                page_id=page_id,
                limit=30,
                user_context=mock_context,
            )
            seen_names.extend(branch.name for branch in result.items)
            page_id = result.next_page_id
            if page_id is None:
                break

        # Assert
        assert seen_names == [f'branch{i}' for i in range(1, 101)]

    def test_returns_403_when_no_provider_tokens(self, test_client):
        """Test that 403 is returned when no provider tokens."""
        with patch(
            'openhands.app_server.user.auth_user_context.AuthUserContext.get_provider_tokens',
            AsyncMock(return_value=None),
        ):
            response = test_client.get(
                '/git/branches/search',
                params={
                    'provider': 'github',
                    'repository': 'user/repo',
                    'query': 'main',
                },
            )
            assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
class TestSearchSuggestedTasks:
    """Test suite for search_suggested_tasks function."""

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_returns_paginated_tasks(self, mock_handler_cls):
        """Test that suggested tasks are returned with pagination."""
        # Arrange
        mock_handler = MagicMock()
        mock_handler.get_suggested_tasks = AsyncMock(
            return_value=[
                SuggestedTask(
                    git_provider=ProviderType.GITHUB,
                    task_type=TaskType.OPEN_ISSUE,
                    repo='user/repo',
                    issue_number=1,
                    title='Fix bug in login',
                ),
                SuggestedTask(
                    git_provider=ProviderType.GITHUB,
                    task_type=TaskType.OPEN_PR,
                    repo='user/repo',
                    issue_number=2,
                    title='Add new feature',
                ),
                SuggestedTask(
                    git_provider=ProviderType.GITHUB,
                    task_type=TaskType.OPEN_ISSUE,
                    repo='user/repo2',
                    issue_number=3,
                    title='Update documentation',
                ),
            ]
        )
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act
        result = await search_suggested_tasks(
            page_id=None,
            limit=2,
            user_context=mock_context,
        )

        # Assert
        assert len(result.items) == 2
        assert result.items[0].issue_number == 1
        assert result.items[1].issue_number == 2
        assert result.next_page_id == encode_page_id(2)

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_returns_second_page(self, mock_handler_cls):
        """Test that second page returns correct items."""
        # Arrange
        mock_handler = MagicMock()
        mock_handler.get_suggested_tasks = AsyncMock(
            return_value=[
                SuggestedTask(
                    git_provider=ProviderType.GITHUB,
                    task_type=TaskType.OPEN_ISSUE,
                    repo='user/repo',
                    issue_number=1,
                    title='Fix bug in login',
                ),
                SuggestedTask(
                    git_provider=ProviderType.GITHUB,
                    task_type=TaskType.OPEN_PR,
                    repo='user/repo',
                    issue_number=2,
                    title='Add new feature',
                ),
                SuggestedTask(
                    git_provider=ProviderType.GITHUB,
                    task_type=TaskType.OPEN_ISSUE,
                    repo='user/repo2',
                    issue_number=3,
                    title='Update documentation',
                ),
            ]
        )
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act - request second page
        result = await search_suggested_tasks(
            page_id=encode_page_id(2),
            limit=2,
            user_context=mock_context,
        )

        # Assert
        assert len(result.items) == 1
        assert result.items[0].issue_number == 3
        assert result.next_page_id is None

    @pytest.mark.asyncio
    @patch('openhands.app_server.git.git_router.ProviderHandler')
    async def test_returns_empty_when_no_tasks(self, mock_handler_cls):
        """Test that empty results return empty items."""
        # Arrange
        mock_handler = MagicMock()
        mock_handler.get_suggested_tasks = AsyncMock(return_value=[])
        mock_handler_cls.return_value = mock_handler

        mock_context = _make_mock_user_context(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(user_id='user-123', token='token')
            },
            user_id='user-123',
        )

        # Act
        result = await search_suggested_tasks(
            page_id=None,
            limit=10,
            user_context=mock_context,
        )

        # Assert
        assert result.items == []
        assert result.next_page_id is None

    def test_returns_403_when_no_provider_tokens(self, test_client):
        """Test that 403 is returned when no provider tokens."""
        with patch(
            'openhands.app_server.user.auth_user_context.AuthUserContext.get_provider_tokens',
            AsyncMock(return_value=None),
        ):
            response = test_client.get('/git/suggested-tasks/search')
            assert response.status_code == status.HTTP_403_FORBIDDEN
