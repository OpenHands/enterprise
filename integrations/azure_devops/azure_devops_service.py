from pydantic import SecretStr

from integrations.provider_service import ProviderCredentialErrorMixin
from openhands.app_server.integrations.azure_devops.azure_devops_service import (
    AzureDevOpsService,
)
from openhands.app_server.integrations.service_types import ProviderType, RequestMethod
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.constants import AZURE_DEVOPS_ORGANIZATION
from server.auth.provider_credentials import ProviderCredentialService

# Git Repositories security namespace + GenericContribute (write) permission bit.
GIT_REPOSITORIES_NAMESPACE_ID = '2e9eb7ed-3c0a-47d4-87c1-0ffdd275fd87'
GENERIC_CONTRIBUTE_PERMISSION = 4


class SaaSAzureDevOpsService(ProviderCredentialErrorMixin, AzureDevOpsService):
    def __init__(
        self,
        user_id: str | None = None,
        external_auth_token: SecretStr | None = None,
        external_auth_id: str | None = None,
        token: SecretStr | None = None,
        external_token_manager: bool = False,
        base_domain: str | None = None,
    ):
        configured_org = AZURE_DEVOPS_ORGANIZATION or None
        super().__init__(
            user_id=user_id,
            external_auth_token=external_auth_token,
            external_auth_id=external_auth_id,
            token=token,
            external_token_manager=external_token_manager,
            base_domain=base_domain or configured_org,
        )

        self.external_auth_token = external_auth_token
        self.external_auth_id = external_auth_id
        self.provider_credentials = ProviderCredentialService()
        self._credential_host = base_domain
        self.refresh = True

    async def get_latest_token(self) -> SecretStr | None:
        token = await self.provider_credentials.token_for_service(
            ProviderType.AZURE_DEVOPS,
            user_id=self.external_auth_id,
            account_id=self.user_id,
            access_token=self.external_auth_token,
            host=self._credential_host,
        )
        if token:
            self.token = token
        return token

    async def get_installations(self) -> list[str]:
        if self.organization:
            return [self.organization]

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
    ):
        if installation_id:
            self.organization = installation_id
        elif not self.organization:
            installations = await self.get_installations()
            if installations:
                self.organization = installations[0]

        return await super().get_paginated_repos(
            page=page,
            per_page=per_page,
            sort=sort,
            installation_id=installation_id,
            query=query,
        )

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
