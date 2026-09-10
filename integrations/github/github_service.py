import asyncio
from collections.abc import Coroutine

from pydantic import SecretStr

from integrations.provider_service import ProviderCredentialErrorMixin
from integrations.store_repo_utils import store_repositories_in_db
from openhands.app_server.integrations.github.github_service import GitHubService
from openhands.app_server.integrations.service_types import ProviderType, Repository
from openhands.app_server.types import AppMode
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.provider_credentials import ProviderCredentialService

# Module-level set to keep background tasks alive until completion.
# Without this, tasks could be garbage-collected mid-execution.
# See: https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
_background_tasks: set[asyncio.Task] = set()


def _create_safe_task(coro: Coroutine) -> asyncio.Task:
    """Create a task that won't be garbage-collected before completion."""
    task = asyncio.create_task(coro)
    task.add_done_callback(_background_tasks.discard)
    _background_tasks.add(task)
    return task


class SaaSGitHubService(ProviderCredentialErrorMixin, GitHubService):
    def __init__(
        self,
        user_id: str | None = None,
        external_auth_token: SecretStr | None = None,
        external_auth_id: str | None = None,
        token: SecretStr | None = None,
        external_token_manager: bool = False,
        base_domain: str | None = None,
    ):
        logger.debug(
            f'SaaSGitHubService created with user_id {user_id}, external_auth_id {external_auth_id}, external_auth_token {"set" if external_auth_token else "None"}, github_token {"set" if token else "None"}, external_token_manager {external_token_manager}'
        )
        super().__init__(
            user_id=user_id,
            external_auth_token=external_auth_token,
            external_auth_id=external_auth_id,
            token=token,
            external_token_manager=external_token_manager,
            base_domain=base_domain,
        )

        self.external_auth_token = external_auth_token
        self.external_auth_id = external_auth_id
        self.provider_credentials = ProviderCredentialService()
        self._credential_host = base_domain

    async def get_latest_token(self) -> SecretStr | None:
        token = await self.provider_credentials.token_for_service(
            ProviderType.GITHUB,
            user_id=self.external_auth_id,
            account_id=self.user_id,
            access_token=self.external_auth_token,
            host=self._credential_host,
        )
        if token:
            self.token = token
        return token

    async def get_pr_patches(
        self, owner: str, repo: str, pr_number: int, per_page: int = 30, page: int = 1
    ):
        """Get patches for files changed in a PR with pagination support.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number
            per_page: Number of files per page (default: 30, max: 100)
            page: Page number to fetch (default: 1)
        """
        url = f'https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files'
        params = {'per_page': min(per_page, 100), 'page': page}  # GitHub max is 100
        response, headers = await self._make_request(url, params)

        has_next_page = 'next' in headers.get('link', '')
        total_count = int(headers.get('total', 0))

        return {
            'files': response,
            'pagination': {
                'has_next_page': has_next_page,
                'total_count': total_count,
                'current_page': page,
                'per_page': per_page,
            },
        }

    async def get_repository_node_id(self, repo_id: str) -> str:
        """
        Get the new GitHub GraphQL node ID for a repository using REST API.

        Args:
            repo_id: Numeric repository ID as string (e.g., "123456789")

        Returns:
            New format node ID for GraphQL queries (e.g., "R_kgDOLfkiww")

        Raises:
            Exception: If the API request fails or node_id is not found
        """
        url = f'https://api.github.com/repositories/{repo_id}'
        response, _ = await self._make_request(url)
        node_id = response.get('node_id')
        if not node_id:
            raise Exception(f'No node_id found for repository {repo_id}')
        return node_id

    async def _get_external_auth_id(self) -> str | None:
        """Get or fetch external_auth_id from Keycloak token if not already set."""
        if self.external_auth_id:
            return self.external_auth_id

        if self.external_auth_token:
            try:
                from server.auth.provider_compatibility import user_from_broker_token

                self.external_auth_id = await user_from_broker_token(
                    self.external_auth_token
                )
                logger.info(
                    f'Determined external_auth_id from Keycloak token: {self.external_auth_id}'
                )
                return self.external_auth_id
            except Exception:
                logger.warning(
                    'Could not determine external_auth_id from token',
                    exc_info=True,
                )
        return None

    async def get_paginated_repos(
        self,
        page: int,
        per_page: int,
        sort: str,
        installation_id: str | None,
        query: str | None = None,
    ) -> list[Repository]:
        repositories = await super().get_paginated_repos(
            page, per_page, sort, installation_id, query
        )
        external_auth_id = await self._get_external_auth_id()
        if external_auth_id:
            _ = _create_safe_task(
                store_repositories_in_db(repositories, external_auth_id)
            )
        return repositories

    async def get_all_repositories(
        self, sort: str, app_mode: AppMode
    ) -> list[Repository]:
        repositories = await super().get_all_repositories(sort, app_mode)
        # Schedule the background task without awaiting it
        external_auth_id = await self._get_external_auth_id()
        if external_auth_id:
            _ = _create_safe_task(
                store_repositories_in_db(repositories, external_auth_id)
            )
        return repositories
