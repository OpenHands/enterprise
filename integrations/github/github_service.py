import asyncio
from collections.abc import Coroutine, Mapping
from typing import TypedDict

from pydantic import JsonValue, SecretStr, TypeAdapter

from integrations.native_git_mixin import NativeGitMixin, native_service_token
from integrations.native_git_types import GitHubNode
from integrations.store_repo_utils import store_repositories_in_db
from openhands.app_server.integrations.github.github_service import GitHubService
from openhands.app_server.integrations.service_types import (
    ProviderType,
    Repository,
    RequestMethod,
)
from openhands.app_server.types import AppMode
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.auth_config import ENABLE_KEYCLOAK
from server.auth.native_git_config import NativeGitConfig
from server.auth.token_manager import TokenManager

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


class GitPatchPagination(TypedDict):
    has_next_page: bool
    total_count: int
    current_page: int
    per_page: int


class GitPatchPage(TypedDict):
    files: JsonValue
    pagination: GitPatchPagination


class SaaSGitHubService(NativeGitMixin, GitHubService):
    def __init__(
        self,
        user_id: str | None = None,
        external_auth_token: SecretStr | None = None,
        external_auth_id: str | None = None,
        token: SecretStr | None = None,
        external_token_manager: bool = False,
        base_domain: str | None = None,
    ) -> None:
        logger.debug(
            f'SaaSGitHubService created with user_id {user_id}, external_auth_id {external_auth_id}, external_auth_token {"set" if external_auth_token else "None"}, github_token {"set" if token else "None"}, external_token_manager {external_token_manager}'
        )
        self._native_requested_host = base_domain
        if not ENABLE_KEYCLOAK:
            from server.auth.native_git_config import git_config

            base_domain = git_config('github', base_domain).host
        self.base_domain = base_domain
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
        self.token_manager = TokenManager(external=external_token_manager)

    def _configure_native_urls(self, config: NativeGitConfig) -> None:
        super()._configure_native_urls(config)
        self.GRAPHQL_URL = (
            'https://api.github.com/graphql'
            if config.host == 'github.com'
            else f'https://{config.host}/api/graphql'
        )

    async def _legacy_headers(self) -> dict[str, str]:
        return TypeAdapter(dict[str, str]).validate_python(
            await GitHubService._get_headers(self)
        )

    async def _legacy_request(
        self, url: str, params: Mapping[str, JsonValue] | None, method: RequestMethod
    ) -> tuple[JsonValue, dict[str, str]]:
        data, headers = await GitHubService._make_request(
            self, url, dict(params) if params is not None else None, method
        )
        return TypeAdapter(JsonValue).validate_python(data), TypeAdapter(
            dict[str, str]
        ).validate_python(headers)

    async def get_latest_token(self) -> SecretStr | None:
        if not ENABLE_KEYCLOAK:
            return await native_service_token(self, ProviderType.GITHUB)
        github_token = None
        if self.external_auth_token:
            github_token = SecretStr(
                await self.token_manager.get_idp_token(
                    self.external_auth_token.get_secret_value(), ProviderType.GITHUB
                )
            )
            logger.debug(
                f'Got GitHub token {github_token} from access token: {self.external_auth_token}'
            )
        elif self.external_auth_id:
            offline_token = await self.token_manager.load_offline_token(
                self.external_auth_id
            )
            github_token_str: str | None = (
                await self.token_manager.get_idp_token_from_offline_token(
                    offline_token, ProviderType.GITHUB
                )
                if offline_token
                else None
            )
            github_token = SecretStr(github_token_str) if github_token_str else None
            logger.debug(
                f'Got GitHub token {github_token} from external auth user ID: {self.external_auth_id}'
            )
        elif self.user_id:
            github_token_str = await self.token_manager.get_idp_token_from_idp_user_id(
                self.user_id, ProviderType.GITHUB
            )
            github_token = SecretStr(github_token_str) if github_token_str else None
            logger.debug(
                f'Got GitHub token {github_token} from user ID: {self.user_id}'
            )
        else:
            logger.warning('external_auth_token and user_id not set!')
        return github_token

    async def get_pr_patches(
        self, owner: str, repo: str, pr_number: int, per_page: int = 30, page: int = 1
    ) -> GitPatchPage:
        """Get patches for files changed in a PR with pagination support.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number
            per_page: Number of files per page (default: 30, max: 100)
            page: Page number to fetch (default: 1)
        """
        url = f'{self.BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}/files'
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
        url = f'{self.BASE_URL}/repositories/{repo_id}'
        response, _ = await self._make_request(url)
        node_id = TypeAdapter(GitHubNode).validate_python(response).get('node_id')
        if not node_id:
            raise Exception(f'No node_id found for repository {repo_id}')
        return node_id

    async def _get_external_auth_id(self) -> str | None:
        """Get or fetch external_auth_id from Keycloak token if not already set."""
        if not ENABLE_KEYCLOAK:
            from server.auth.native_git_config import git_config

            if self.base_domain != git_config('github').host:
                return None
        if self.external_auth_id:
            return self.external_auth_id

        if not ENABLE_KEYCLOAK:
            return None
        if self.external_auth_token:
            try:
                user_info = await self.token_manager.get_user_info(
                    self.external_auth_token.get_secret_value()
                )
                self.external_auth_id = user_info.sub
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
        # Native PAT/OAuth connections list their own repositories. App
        # installation APIs are optional and have separate provider permissions.
        repositories = await super().get_all_repositories(
            sort, app_mode if ENABLE_KEYCLOAK else AppMode.OPENHANDS
        )
        # Schedule the background task without awaiting it
        external_auth_id = await self._get_external_auth_id()
        if external_auth_id:
            _ = _create_safe_task(
                store_repositories_in_db(repositories, external_auth_id)
            )
        return repositories

    async def get_installations(self) -> list[str]:
        if not ENABLE_KEYCLOAK:
            token = await self.get_latest_token()
            if not token or not token.get_secret_value().startswith('ghu_'):
                return []
        return await super().get_installations()

    async def search_repositories(
        self,
        query: str,
        per_page: int,
        sort: str,
        order: str,
        public: bool,
        app_mode: AppMode,
    ) -> list[Repository]:
        return await super().search_repositories(
            query,
            per_page,
            sort,
            order,
            public,
            app_mode if ENABLE_KEYCLOAK else AppMode.OPENHANDS,
        )
