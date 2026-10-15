import asyncio
import json

from fastapi import HTTPException, status
from pydantic import SecretStr

from openhands.app_server.integrations.azure_devops.azure_devops_service import (
    AzureDevOpsService,
)
from openhands.app_server.integrations.service_types import (
    ProviderType,
    Repository,
    RequestMethod,
    SuggestedTask,
)
from openhands.app_server.types import AppMode
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.token_manager import TokenManager
from storage.redis import get_redis_client_async

# Git Repositories security namespace + GenericContribute (write) permission bit.
GIT_REPOSITORIES_NAMESPACE_ID = '2e9eb7ed-3c0a-47d4-87c1-0ffdd275fd87'
GENERIC_CONTRIBUTE_PERMISSION = 4

# Short, so a removed member loses access quickly.
MEMBERSHIP_CACHE_SECONDS = 60


class SaaSAzureDevOpsService(AzureDevOpsService):
    def __init__(
        self,
        user_id: str | None = None,
        external_auth_token: SecretStr | None = None,
        external_auth_id: str | None = None,
        token: SecretStr | None = None,
        external_token_manager: bool = False,
        base_domain: str | None = None,
    ):
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
        self.refresh = True
        # Only per-organization instances act on self.organization; user-wide
        # calls span every organization the user belongs to.
        self.bound_to_organization = False

    async def get_latest_token(self) -> SecretStr | None:
        azure_devops_token = None
        if self.external_auth_token:
            azure_devops_token = SecretStr(
                await self.token_manager.get_idp_token(
                    self.external_auth_token.get_secret_value(),
                    idp=ProviderType.AZURE_DEVOPS,
                )
            )
            logger.debug('Got Azure DevOps token via external_auth_token')
        elif self.external_auth_id:
            offline_token = await self.token_manager.load_offline_token(
                self.external_auth_id
            )
            azure_devops_token_str: str | None = (
                await self.token_manager.get_idp_token_from_offline_token(
                    offline_token, ProviderType.AZURE_DEVOPS
                )
                if offline_token
                else None
            )
            azure_devops_token = (
                SecretStr(azure_devops_token_str) if azure_devops_token_str else None
            )
            logger.debug('Got Azure DevOps token via external_auth_id')
        elif self.user_id:
            azure_devops_token_str = (
                await self.token_manager.get_idp_token_from_idp_user_id(
                    self.user_id, ProviderType.AZURE_DEVOPS
                )
            )
            azure_devops_token = (
                SecretStr(azure_devops_token_str) if azure_devops_token_str else None
            )
            logger.debug('Got Azure DevOps token via user_id')
        else:
            logger.warning('external_auth_token and user_id not set!')
        if azure_devops_token:
            self.token = azure_devops_token
        return azure_devops_token

    def _for_organization(self, organization: str) -> 'SaaSAzureDevOpsService':
        """A service with the same credentials, bound to one organization."""
        service = SaaSAzureDevOpsService(
            user_id=self.user_id,
            external_auth_token=self.external_auth_token,
            external_auth_id=self.external_auth_id,
            token=self.token or None,
            external_token_manager=self.external_token_manager,
            base_domain=organization,
        )
        service.bound_to_organization = True
        return service

    async def _organization_services(self) -> list['SaaSAzureDevOpsService']:
        organizations = await self.get_accessible_organizations()
        if organizations and not self.token:
            # Fetch once instead of once per organization.
            await self.get_latest_token()
        return [self._for_organization(organization) for organization in organizations]

    async def _service_for_installation(
        self, installation_id: str
    ) -> 'SaaSAzureDevOpsService':
        """Bind to Azure's canonical name for an organization the user belongs to."""
        requested = installation_id.strip().casefold()
        for organization in await self.get_accessible_organizations():
            if organization.casefold() == requested:
                return self._for_organization(organization)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Azure DevOps organization access denied.',
        )

    async def get_installations(self) -> list[str]:
        return await self.get_accessible_organizations()

    def _membership_cache_key(self) -> str | None:
        if self.external_auth_id:
            return f'azure_devops:organizations:{self.external_auth_id}'
        if self.user_id:
            return f'azure_devops:organizations:idp:{self.user_id}'
        return None

    async def get_accessible_organizations(self) -> list[str]:
        """Organizations the token holder belongs to, cached briefly per user."""
        key = self._membership_cache_key()
        if key:
            try:
                cached = await get_redis_client_async().get(key)
                if cached:
                    return json.loads(cached)
            except Exception as e:
                logger.warning(f'[Azure DevOps] membership cache read failed: {e}')

        organizations = await self._discover_organizations()
        if key:
            try:
                await get_redis_client_async().set(
                    key, json.dumps(organizations), ex=MEMBERSHIP_CACHE_SECONDS
                )
            except Exception as e:
                logger.warning(f'[Azure DevOps] membership cache write failed: {e}')
        return organizations

    async def _discover_organizations(self) -> list[str]:
        """Discover token-holder membership without trusting a configured default."""
        profile_url = (
            'https://app.vssps.visualstudio.com/_apis/profile/profiles/me'
            '?api-version=7.1-preview.3'
        )
        profile, _ = await self._make_request(profile_url)
        member_id = profile.get('id')
        if not member_id:
            return []

        accounts_url = (
            'https://app.vssps.visualstudio.com/_apis/accounts'
            f'?memberId={member_id}&api-version=7.1-preview.1'
        )
        accounts, _ = await self._make_request(accounts_url)
        account_values = accounts.get('value') or accounts.get('accounts') or []
        return [
            account['accountName']
            for account in account_values
            if account.get('accountName')
        ]

    async def get_paginated_repos(
        self,
        page: int,
        per_page: int,
        sort: str,
        installation_id: str | None,
        query: str | None = None,
    ) -> list[Repository]:
        if self.bound_to_organization:
            return await super().get_paginated_repos(
                page, per_page, sort, installation_id, query
            )
        if installation_id:
            service = await self._service_for_installation(installation_id)
            return await service.get_paginated_repos(
                page=page,
                per_page=per_page,
                sort=sort,
                installation_id=installation_id,
                query=query,
            )

        # Without an organization, page through every organization's repositories.
        repos = await self.get_repositories(sort, AppMode.SAAS)
        if query:
            repos = [repo for repo in repos if query.lower() in repo.full_name.lower()]
        start = (page - 1) * per_page
        return repos[start : start + per_page]

    async def get_repositories(self, sort: str, app_mode: AppMode) -> list[Repository]:
        if self.bound_to_organization:
            return await super().get_repositories(sort, app_mode)
        repos: list[Repository] = []
        for service in await self._organization_services():
            repos.extend(await service.get_repositories(sort, app_mode))
        return repos

    async def search_repositories(
        self,
        query: str,
        per_page: int = 30,
        sort: str = 'updated',
        order: str = 'desc',
        public: bool = False,
        app_mode: AppMode = AppMode.OPENHANDS,
    ) -> list[Repository]:
        """Search every organization and merge the results."""
        if self.bound_to_organization:
            return await super().search_repositories(
                query, per_page, sort, order, public, app_mode
            )
        results = await asyncio.gather(
            *(
                service.search_repositories(
                    query, per_page, sort, order, public, app_mode
                )
                for service in await self._organization_services()
            )
        )
        merged: dict[str, Repository] = {}
        for repos in results:
            for repo in repos:
                merged.setdefault(repo.full_name.casefold(), repo)
        return list(merged.values())[:per_page]

    async def get_suggested_tasks(self) -> list[SuggestedTask]:
        if self.bound_to_organization:
            return await super().get_suggested_tasks()
        tasks: list[SuggestedTask] = []
        for service in await self._organization_services():
            tasks.extend(await service.get_suggested_tasks())
        return tasks

    async def has_contribute_access(self, project_id: str, repository_id: str) -> bool:
        """Whether the caller has GenericContribute (write) on the repo.

        Fails closed: missing ids or any error returns False.
        """
        if not project_id or not repository_id:
            return False

        url = (
            f'{self.base_url}/_apis/security/permissionevaluationbatch'
            '?api-version=7.1-preview.1'
        )
        payload = {
            'evaluations': [
                {
                    'securityNamespaceId': GIT_REPOSITORIES_NAMESPACE_ID,
                    'token': f'repoV2/{project_id}/{repository_id}',
                    'permissions': GENERIC_CONTRIBUTE_PERMISSION,
                }
            ]
        }
        try:
            response, _ = await self._make_request(
                url=url, params=payload, method=RequestMethod.POST
            )
            evaluations = response.get('evaluations') or []
            return bool(evaluations and evaluations[0].get('value'))
        except Exception as e:
            logger.warning(f'[Azure DevOps] permission check failed: {e}')
            return False

    async def get_project_repositories(self, project: str) -> list[dict]:
        """List a project's Git repositories as raw API dicts."""
        project_enc = self._encode_url_component(project)
        url = f'{self.base_url}/{project_enc}/_apis/git/repositories?api-version=7.1'
        response, _ = await self._make_request(url)
        return response.get('value') or []
