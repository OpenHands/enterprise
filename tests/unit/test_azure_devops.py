from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openhands.app_server.integrations.azure_devops.azure_devops_service import (
    AzureDevOpsServiceImpl as AzureDevOpsService,
)
from openhands.app_server.integrations.service_types import ProviderType


@pytest.mark.asyncio
async def test_azure_devops_service_init():
    """Test that the Azure DevOps service initializes correctly."""
    service = AzureDevOpsService(
        user_id='test_user',
        token=None,
        base_domain='myorg',
    )

    assert service.organization == 'myorg'
    assert service.provider == ProviderType.AZURE_DEVOPS.value


@pytest.mark.asyncio
async def test_azure_devops_get_repositories():
    """Repositories from every project come from one organization-wide call."""
    with patch('httpx.AsyncClient') as mock_client:
        mock_repos_response = MagicMock()
        mock_repos_response.json.return_value = {
            'value': [
                {
                    'id': 'repo1',
                    'name': 'Repo1',
                    'project': {'name': 'Project1'},
                    'lastUpdateTime': '2023-01-01T00:00:00Z',
                },
                {
                    'id': 'repo2',
                    'name': 'Repo2',
                    'project': {'name': 'Project 150'},
                    'lastUpdateTime': '2023-01-02T00:00:00Z',
                },
            ]
        }
        mock_repos_response.raise_for_status = AsyncMock()

        mock_client_instance = MagicMock()
        mock_client_instance.get = AsyncMock(return_value=mock_repos_response)
        mock_client.return_value.__aenter__.return_value = mock_client_instance

        service = AzureDevOpsService(
            user_id='test_user',
            token=None,
            base_domain='myorg',
        )
        service._get_azure_devops_headers = AsyncMock(return_value={})

        repos = await service.get_repositories('updated', None)

        # The projects endpoint pages at 100 projects, so it is not used.
        mock_client_instance.get.assert_awaited_once()
        assert mock_client_instance.get.await_args.args[0] == (
            'https://dev.azure.com/myorg/_apis/git/repositories?api-version=7.1'
        )
        # Sorted by lastUpdateTime descending, so repo2 first
        assert [repo.full_name for repo in repos] == [
            'myorg/Project 150/Repo2',
            'myorg/Project1/Repo1',
        ]
        assert [repo.id for repo in repos] == ['repo2', 'repo1']
        assert all(repo.git_provider == ProviderType.AZURE_DEVOPS for repo in repos)


@pytest.mark.asyncio
async def test_azure_devops_get_repository_details():
    """Test that the Azure DevOps service can get repository details."""
    with patch('httpx.AsyncClient') as mock_client:
        # Mock the response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'id': 'repo1',
            'name': 'Repo1',
            'project': {'name': 'Project1'},
        }
        mock_response.raise_for_status = AsyncMock()

        # Set up the mock client to return our mock response
        mock_client_instance = MagicMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__.return_value = mock_client_instance

        # Create the service and call get_repository_details_from_repo_name
        service = AzureDevOpsService(
            user_id='test_user',
            token=None,
            base_domain='myorg',
        )

        # Mock the _get_azure_devops_headers method
        service._get_azure_devops_headers = AsyncMock(return_value={})

        # Call the method
        repo = await service.get_repository_details_from_repo_name(
            'myorg/Project1/Repo1'
        )

        # Verify the results
        assert repo.id == 'repo1'
        assert repo.full_name == 'myorg/Project1/Repo1'
        assert repo.git_provider == ProviderType.AZURE_DEVOPS
