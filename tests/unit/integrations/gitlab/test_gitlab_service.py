"""Unit tests for SaaSGitLabService."""

from unittest.mock import patch

import pytest

from integrations.gitlab.gitlab_service import SaaSGitLabService
from integrations.types import GitLabResourceType
from openhands.app_server.integrations.service_types import (
    RateLimitError,
    RequestMethod,
)
from storage.gitlab_webhook import WebhookStatus


@pytest.fixture
def gitlab_service():
    """Create a SaaSGitLabService instance for testing."""
    return SaaSGitLabService(external_auth_id='test_user_id')


class TestSaaSGitLabServiceInit:
    """Tests for SaaSGitLabService __init__."""

    def test_explicit_base_domain_overrides_default(self):
        """An explicit base_domain parameter overrides the upstream class default."""
        service = SaaSGitLabService(external_auth_id='u1', base_domain='other.host')

        assert service.BASE_URL == 'https://other.host/api/v4'


class TestGetUserResourcesWithAdminAccess:
    """Test cases for get_user_resources_with_admin_access method."""

    @pytest.mark.asyncio
    async def test_get_resources_single_page_projects_and_groups(self, gitlab_service):
        """Test fetching resources when all data fits in a single page."""
        # Arrange
        mock_projects = [
            {'id': 1, 'name': 'Project 1'},
            {'id': 2, 'name': 'Project 2'},
        ]
        mock_groups = [
            {'id': 10, 'name': 'Group 1'},
        ]

        with patch.object(gitlab_service, '_make_request') as mock_request:
            # First call for projects, second for groups
            mock_request.side_effect = [
                (mock_projects, {'Link': ''}),  # No next page
                (mock_groups, {'Link': ''}),  # No next page
            ]

            # Act
            (
                projects,
                groups,
            ) = await gitlab_service.get_user_resources_with_admin_access()

            # Assert
            assert len(projects) == 2
            assert len(groups) == 1
            assert projects[0]['id'] == 1
            assert projects[1]['id'] == 2
            assert groups[0]['id'] == 10
            assert mock_request.call_count == 2

    @pytest.mark.asyncio
    async def test_get_resources_multiple_pages_projects(self, gitlab_service):
        """Test fetching projects across multiple pages."""
        # Arrange
        page1_projects = [{'id': i, 'name': f'Project {i}'} for i in range(1, 101)]
        page2_projects = [{'id': i, 'name': f'Project {i}'} for i in range(101, 151)]

        with patch.object(gitlab_service, '_make_request') as mock_request:
            mock_request.side_effect = [
                (page1_projects, {'Link': '<url>; rel="next"'}),  # Has next page
                (page2_projects, {'Link': ''}),  # Last page
                ([], {'Link': ''}),  # Groups (empty)
            ]

            # Act
            (
                projects,
                groups,
            ) = await gitlab_service.get_user_resources_with_admin_access()

            # Assert
            assert len(projects) == 150
            assert len(groups) == 0
            assert mock_request.call_count == 3

    @pytest.mark.asyncio
    async def test_get_resources_multiple_pages_groups(self, gitlab_service):
        """Test fetching groups across multiple pages."""
        # Arrange
        page1_groups = [{'id': i, 'name': f'Group {i}'} for i in range(1, 101)]
        page2_groups = [{'id': i, 'name': f'Group {i}'} for i in range(101, 151)]

        with patch.object(gitlab_service, '_make_request') as mock_request:
            mock_request.side_effect = [
                ([], {'Link': ''}),  # Projects (empty)
                (page1_groups, {'Link': '<url>; rel="next"'}),  # Has next page
                (page2_groups, {'Link': ''}),  # Last page
            ]

            # Act
            (
                projects,
                groups,
            ) = await gitlab_service.get_user_resources_with_admin_access()

            # Assert
            assert len(projects) == 0
            assert len(groups) == 150
            assert mock_request.call_count == 3

    @pytest.mark.asyncio
    async def test_get_resources_empty_response(self, gitlab_service):
        """Test when user has no projects or groups with admin access."""
        # Arrange
        with patch.object(gitlab_service, '_make_request') as mock_request:
            mock_request.side_effect = [
                ([], {'Link': ''}),  # No projects
                ([], {'Link': ''}),  # No groups
            ]

            # Act
            (
                projects,
                groups,
            ) = await gitlab_service.get_user_resources_with_admin_access()

            # Assert
            assert len(projects) == 0
            assert len(groups) == 0
            assert mock_request.call_count == 2

    @pytest.mark.asyncio
    async def test_get_resources_uses_correct_params_for_projects(self, gitlab_service):
        """Test that projects API is called with correct parameters."""
        # Arrange
        with patch.object(gitlab_service, '_make_request') as mock_request:
            mock_request.side_effect = [
                ([], {'Link': ''}),  # Projects
                ([], {'Link': ''}),  # Groups
            ]

            # Act
            await gitlab_service.get_user_resources_with_admin_access()

            # Assert
            # Check first call (projects)
            first_call = mock_request.call_args_list[0]
            assert 'projects' in first_call[0][0]
            assert first_call[0][1]['membership'] == 1
            assert first_call[0][1]['min_access_level'] == 40
            assert first_call[0][1]['per_page'] == '100'

    @pytest.mark.asyncio
    async def test_get_resources_uses_correct_params_for_groups(self, gitlab_service):
        """Test that groups API is called with correct parameters."""
        # Arrange
        with patch.object(gitlab_service, '_make_request') as mock_request:
            mock_request.side_effect = [
                ([], {'Link': ''}),  # Projects
                ([], {'Link': ''}),  # Groups
            ]

            # Act
            await gitlab_service.get_user_resources_with_admin_access()

            # Assert
            # Check second call (groups)
            second_call = mock_request.call_args_list[1]
            assert 'groups' in second_call[0][0]
            assert second_call[0][1]['min_access_level'] == 40
            assert second_call[0][1]['top_level_only'] == 'true'
            assert second_call[0][1]['per_page'] == '100'

    @pytest.mark.asyncio
    async def test_get_resources_handles_api_error_gracefully(self, gitlab_service):
        """Test that API errors are handled gracefully and don't crash."""
        # Arrange
        with patch.object(gitlab_service, '_make_request') as mock_request:
            # First call succeeds, second call fails
            mock_request.side_effect = [
                ([{'id': 1, 'name': 'Project 1'}], {'Link': ''}),
                Exception('API Error'),
            ]

            # Act
            (
                projects,
                groups,
            ) = await gitlab_service.get_user_resources_with_admin_access()

            # Assert
            # Should return what was fetched before the error
            assert len(projects) == 1
            assert len(groups) == 0

    @pytest.mark.asyncio
    async def test_get_resources_stops_on_empty_response(self, gitlab_service):
        """Test that pagination stops when API returns empty response."""
        # Arrange
        with patch.object(gitlab_service, '_make_request') as mock_request:
            mock_request.side_effect = [
                (None, {'Link': ''}),  # Empty response stops pagination
                ([], {'Link': ''}),  # Groups
            ]

            # Act
            (
                projects,
                groups,
            ) = await gitlab_service.get_user_resources_with_admin_access()

            # Assert
            assert len(projects) == 0
            assert mock_request.call_count == 2  # Should not continue pagination


class TestDeleteWebhooksWithUrl:
    """delete_webhooks_with_url removes only hooks pointing at our URL."""

    async def test_deletes_every_hook_with_the_url_and_no_other(self, gitlab_service):
        hooks = [
            {'id': 1, 'url': 'https://ours/hook'},
            {'id': 2, 'url': 'https://someone-else/hook'},
            {'id': 3, 'url': 'https://ours/hook'},
        ]
        with patch.object(gitlab_service, '_make_request') as mock_request:
            mock_request.side_effect = [(hooks, {}), (None, {}), (None, {})]

            deleted, status = await gitlab_service.delete_webhooks_with_url(
                GitLabResourceType.GROUP, '42', 'https://ours/hook'
            )

        assert (deleted, status) == (2, None)
        assert mock_request.call_args_list[0].kwargs['params'] == {'per_page': 100}
        delete_calls = mock_request.call_args_list[1:]
        assert [call.args[0] for call in delete_calls] == [
            f'{gitlab_service.BASE_URL}/groups/42/hooks/1',
            f'{gitlab_service.BASE_URL}/groups/42/hooks/3',
        ]
        assert all(
            call.kwargs['method'] == RequestMethod.DELETE for call in delete_calls
        )

    async def test_rate_limit_part_way_reports_what_was_deleted(self, gitlab_service):
        hooks = [
            {'id': 1, 'url': 'https://ours/hook'},
            {'id': 2, 'url': 'https://ours/hook'},
        ]
        with patch.object(gitlab_service, '_make_request') as mock_request:
            mock_request.side_effect = [
                (hooks, {}),
                (None, {}),
                RateLimitError('slow down'),
            ]

            deleted, status = await gitlab_service.delete_webhooks_with_url(
                GitLabResourceType.PROJECT, '7', 'https://ours/hook'
            )

        assert (deleted, status) == (1, WebhookStatus.RATE_LIMITED)
