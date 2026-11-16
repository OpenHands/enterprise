"""Integration-started conversations must discover the same skills as UI starts.

Resolvers, Jira, Jira DC and Slack run with ResolverUserContext; the UI and API
use AuthUserContext. With the same provider tokens and provider API responses,
the skill loader must produce the same org/user skill repos, authenticated
marketplace sources and Azure DevOps credential-helper orgs for both.
"""

from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from integrations.resolver_context import ResolverUserContext
from openhands.app_server.app_conversation.skill_loader import (
    authenticate_marketplace_sources,
    azure_devops_user_orgs,
    build_org_configs,
)
from openhands.app_server.integrations.provider import (
    ProviderHandler,
    ProviderToken,
    ProviderType,
)
from openhands.app_server.integrations.service_types import Repository
from openhands.app_server.settings.settings_models import MarketplaceRegistration
from openhands.app_server.user.auth_user_context import AuthUserContext

BBDC_HOST = 'bitbucket.example.com'
GITLAB_DOMAIN = ProviderHandler.PROVIDER_DOMAINS[ProviderType.GITLAB]
ADO_ORGS = ['org1', 'org2']

# provider -> (selected repo, user's other org, skill repos on the server,
#              private marketplace URL, marketplace repo path)
CASES = {
    ProviderType.GITHUB: (
        'acme/app',
        'other-org',
        {'acme/.openhands', 'alona/.openhands', 'other-org/.agents'},
        'https://github.com/acme/private-skills.git',
        'acme/private-skills',
    ),
    ProviderType.GITLAB: (
        'acme/app',
        'other-group',
        {
            'acme/openhands-config',
            'alona/openhands-config',
            'other-group/openhands-config',
        },
        f'https://{GITLAB_DOMAIN}/acme/private-skills.git',
        'acme/private-skills',
    ),
    ProviderType.BITBUCKET: (
        'acme/app',
        'other-ws',
        {'acme/.openhands', 'alona/.openhands', 'other-ws/.openhands'},
        'https://bitbucket.org/acme/private-skills.git',
        'acme/private-skills',
    ),
    ProviderType.BITBUCKET_DATA_CENTER: (
        'PROJ/app',
        None,  # org enumeration is skipped for Bitbucket DC
        {'PROJ/.openhands', 'alona/.openhands'},
        f'https://{BBDC_HOST}/scm/~alona/private-skills.git',
        '~alona/private-skills',
    ),
    ProviderType.AZURE_DEVOPS: (
        'org1/proj/app',
        'org2',
        {
            'org1/openhands-config/openhands-config',
            'alona/openhands-config/openhands-config',
            'org2/openhands-config/openhands-config',
        },
        'https://dev.azure.com/org1/proj/_git/private-skills',
        'org1/proj/private-skills',
    ),
}


def _user_auth(provider: ProviderType) -> MagicMock:
    host = BBDC_HOST if provider == ProviderType.BITBUCKET_DATA_CENTER else None
    auth = MagicMock()
    auth.get_provider_tokens = AsyncMock(
        return_value=MappingProxyType(
            {provider: ProviderToken(token=SecretStr('t'), user_id='1', host=host)}
        )
    )
    auth.get_user_id = AsyncMock(return_value='keycloak-user')
    return auth


async def _discover(provider: ProviderType, ctx) -> dict[str, object]:
    selected, other_org, skill_repos, marketplace_url, marketplace_repo = CASES[
        provider
    ]
    existing = skill_repos | {marketplace_repo}

    async def verify_repo_provider(self, repo, *args, **kwargs):
        return Repository(
            id='1', full_name=repo, git_provider=provider, is_public=False
        )

    async def get_authenticated_git_url(self, repo, *args, **kwargs):
        if repo not in existing:
            raise RuntimeError(f'{repo} not found')
        return f'https://x-token@example.invalid/{repo}.git'

    service = MagicMock()
    service.get_user = AsyncMock(return_value=MagicMock(login='alona'))
    orgs = [other_org] if other_org else []

    with (
        patch.object(ProviderHandler, 'verify_repo_provider', verify_repo_provider),
        patch.object(
            ProviderHandler, 'get_authenticated_git_url', get_authenticated_git_url
        ),
        patch.object(ProviderHandler, 'get_service', lambda self, p: service),
        patch.object(
            ProviderHandler, 'get_github_organizations', AsyncMock(return_value=orgs)
        ),
        patch.object(
            ProviderHandler, 'get_gitlab_groups', AsyncMock(return_value=orgs)
        ),
        patch.object(
            ProviderHandler, 'get_bitbucket_workspaces', AsyncMock(return_value=orgs)
        ),
        patch.object(
            ProviderHandler,
            'get_azure_devops_organizations',
            AsyncMock(return_value=ADO_ORGS),
        ),
    ):
        configs = await build_org_configs(selected, ctx)
        [marketplace] = await authenticate_marketplace_sources(
            [
                MarketplaceRegistration(
                    name='private', source=marketplace_url, auto_load=True
                )
            ],
            ctx,
        )
        return {
            'skill_repos': {c.repository for c in configs},
            'marketplace_authenticated': 'x-token@' in marketplace.source,
            'azure_devops_orgs': await azure_devops_user_orgs(ctx, selected),
        }


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', list(CASES), ids=lambda p: p.value)
async def test_resolver_context_matches_ui_context(provider):
    ui = await _discover(provider, AuthUserContext(user_auth=_user_auth(provider)))
    integration = await _discover(
        provider, ResolverUserContext(saas_user_auth=_user_auth(provider))
    )

    assert integration == ui
    assert integration['skill_repos'] == CASES[provider][2]
    assert integration['marketplace_authenticated'] is True
    if provider == ProviderType.AZURE_DEVOPS:
        assert integration['azure_devops_orgs'] == ADO_ORGS
