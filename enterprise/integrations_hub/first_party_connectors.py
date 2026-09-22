"""First-party OpenHands Enterprise connectors missing from openhands_extensions.

These mirror legacy Settings > Integrations providers so Hub cutover / reconnect
can target the same products users already connected.
"""

from __future__ import annotations

from .models import ManagedConnector, OAuthConfig


def enterprise_first_party_connectors() -> tuple[ManagedConnector, ...]:
    return (
        ManagedConnector(
            slug='gitlab',
            name='GitLab',
            description=(
                'Repositories, merge requests, issues, and pipelines via the '
                'GitLab API.'
            ),
            logoUrl='https://cdn.simpleicons.org/gitlab/FFFFFF',
            iconBg='#FC6D26',
            iconColor='#FFFFFF',
            docsUrl='https://docs.gitlab.com/ee/api/oauth2.html',
            categories=['Engineering', 'Source control'],
            authModes=['oauth2', 'api_key'],
            authStrategy='oauth2',
            provider='http',
            oauthConfigured=False,
            credentialLabel='GitLab personal access token',
            credentialPlaceholder='glpat-…',
            credentialHelp=(
                'Use gitlab.com OAuth when configured, or a personal access '
                'token with read_api / read_repository scopes.'
            ),
            apiBaseUrl='https://gitlab.com/api/v4',
            oauthConfig=OAuthConfig(
                authorizationUrl='https://gitlab.com/oauth/authorize',
                tokenUrl='https://gitlab.com/oauth/token',
                scopes=['read_user', 'read_api', 'read_repository'],
            ),
            tools=[],
        ),
        ManagedConnector(
            slug='azure_devops',
            name='Azure DevOps',
            description=(
                'Repos, pull requests, and work items in Azure DevOps '
                'organizations.'
            ),
            logoUrl='https://cdn.simpleicons.org/azuredevops/FFFFFF',
            iconBg='#0078D4',
            iconColor='#FFFFFF',
            docsUrl=(
                'https://learn.microsoft.com/en-us/azure/devops/integrate/'
                'get-started/authentication/pats'
            ),
            categories=['Engineering', 'Source control'],
            authModes=['api_key'],
            authStrategy='api_key',
            provider='http',
            oauthConfigured=True,
            credentialLabel='Azure DevOps personal access token',
            credentialPlaceholder='Paste PAT',
            credentialHelp=(
                'Create a PAT with Code and Work Items scopes for your '
                'organization.'
            ),
            apiBaseUrl='https://dev.azure.com',
            tools=[],
        ),
        ManagedConnector(
            slug='forgejo',
            name='Forgejo',
            description=(
                'Self-hosted Git forges compatible with the Forgejo / Gitea API.'
            ),
            logoUrl='https://cdn.simpleicons.org/forgejo/FFFFFF',
            iconBg='#FB923C',
            iconColor='#FFFFFF',
            docsUrl='https://forgejo.org/docs/latest/user/api-usage/',
            categories=['Engineering', 'Source control'],
            authModes=['api_key'],
            authStrategy='api_key',
            provider='http',
            oauthConfigured=True,
            credentialLabel='Forgejo access token',
            credentialPlaceholder='Paste access token',
            credentialHelp=(
                'Use an access token from your Forgejo instance and set the '
                'API base URL to your host.'
            ),
            tools=[],
        ),
        ManagedConnector(
            slug='bitbucket_data_center',
            name='Bitbucket Data Center',
            description=(
                'Self-managed Bitbucket Data Center repositories, PRs, and '
                'projects.'
            ),
            logoUrl='https://cdn.simpleicons.org/bitbucket/FFFFFF',
            iconBg='#0052CC',
            iconColor='#FFFFFF',
            docsUrl=(
                'https://confluence.atlassian.com/bitbucketserver/'
                'personal-access-tokens-939515499.html'
            ),
            categories=['Engineering', 'Source control'],
            authModes=['api_key'],
            authStrategy='api_key',
            provider='http',
            oauthConfigured=True,
            credentialLabel='Bitbucket Data Center personal access token',
            credentialPlaceholder='Paste PAT',
            credentialHelp=(
                'Create a personal access token on your Bitbucket Data Center '
                'instance and set the API base URL to your host.'
            ),
            tools=[],
        ),
        ManagedConnector(
            slug='jira-dc',
            name='Jira Data Center',
            description=(
                'Self-managed Jira Data Center issues, projects, and workflows.'
            ),
            logoUrl='https://cdn.simpleicons.org/jira/FFFFFF',
            iconBg='#0052CC',
            iconColor='#FFFFFF',
            docsUrl=(
                'https://confluence.atlassian.com/adminjiraserver/'
                'using-personal-access-tokens-1026032368.html'
            ),
            categories=['Project management', 'Engineering'],
            authModes=['api_key'],
            authStrategy='api_key',
            provider='http',
            oauthConfigured=True,
            credentialLabel='Jira Data Center personal access token',
            credentialPlaceholder='Paste PAT',
            credentialHelp=(
                'Create a personal access token on your Jira Data Center '
                'instance and set the API base URL to your host.'
            ),
            tools=[],
        ),
    )
