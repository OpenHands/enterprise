"""Tests for the read-only OAuth access token secrets listing."""

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.app_server.integrations.provider import ProviderToken, ProviderType
from openhands.app_server.secrets.provider_token_secrets import (
    get_provider_token_secret_specs,
)
from openhands.app_server.secrets.secrets_router import (
    router as secrets_router,
)
from openhands.app_server.secrets.secrets_router import (
    user_context_dependency,
)
from openhands.app_server.user.user_models import NamedOAuthSecret, NamedOAuthSecrets

GITHUB_TOKEN_VALUE = 'ghp_super_secret_value'


class FakeUserContext:
    def __init__(self, provider_tokens=None, named_oauth_secrets=None):
        self._provider_tokens = provider_tokens
        self._named_oauth_secrets = named_oauth_secrets

    async def get_provider_tokens(self, as_env_vars=False, git_provider_id=None):
        return self._provider_tokens

    async def get_named_oauth_secrets(self, git_provider_id=None):
        return self._named_oauth_secrets


def _tokens(*provider_types: ProviderType):
    return {
        provider_type: ProviderToken(token=SecretStr(GITHUB_TOKEN_VALUE))
        for provider_type in provider_types
    }


@pytest.mark.asyncio
async def test_no_connected_accounts_yields_no_specs():
    assert await get_provider_token_secret_specs(FakeUserContext()) == []


@pytest.mark.asyncio
async def test_provider_tokens_use_provider_env_key():
    context = FakeUserContext(_tokens(ProviderType.GITHUB, ProviderType.GITLAB))

    specs = await get_provider_token_secret_specs(context)

    assert {spec.name: spec.provider_type for spec in specs} == {
        'GITHUB_TOKEN': ProviderType.GITHUB,
        'GITLAB_TOKEN': ProviderType.GITLAB,
    }
    assert all(spec.oauth_provider_id is None for spec in specs)


@pytest.mark.asyncio
async def test_provider_without_token_is_skipped():
    context = FakeUserContext({ProviderType.GITHUB: ProviderToken(token=None)})

    assert await get_provider_token_secret_specs(context) == []


@pytest.mark.asyncio
async def test_named_oauth_secret_replaces_provider_token_of_linked_provider():
    context = FakeUserContext(
        _tokens(ProviderType.GITHUB, ProviderType.GITLAB),
        NamedOAuthSecrets(
            secrets=[
                NamedOAuthSecret(
                    secret_name='GH_OAUTH',
                    oauth_provider_id=7,
                    description='GitHub OAuth access token',
                )
            ],
            linked_provider_types={ProviderType.GITHUB},
        ),
    )

    specs = await get_provider_token_secret_specs(context)

    assert {spec.name: spec.oauth_provider_id for spec in specs} == {
        'GITLAB_TOKEN': None,
        'GH_OAUTH': 7,
    }


@pytest.fixture
def client_for():
    app = FastAPI()
    app.include_router(secrets_router)

    def make(context: FakeUserContext) -> TestClient:
        app.dependency_overrides[user_context_dependency.dependency] = lambda: context
        return TestClient(app)

    with patch.dict(os.environ, {'SESSION_API_KEY': ''}, clear=False):
        with patch('openhands.app_server.utils.dependencies._SESSION_API_KEY', None):
            yield make


def test_endpoint_lists_names_and_never_token_values(client_for):
    context = FakeUserContext(
        _tokens(ProviderType.GITHUB, ProviderType.BITBUCKET),
        NamedOAuthSecrets(
            secrets=[
                NamedOAuthSecret(
                    secret_name='LINEAR_TOKEN',
                    oauth_provider_id=3,
                    description='Linear OAuth access token',
                )
            ]
        ),
    )

    response = client_for(context).get('/secrets/oauth-tokens')

    assert response.status_code == 200
    assert response.json() == {
        'items': [
            {
                'name': 'BITBUCKET_TOKEN',
                'description': 'BITBUCKET authentication token',
                'provider': 'bitbucket',
            },
            {
                'name': 'GITHUB_TOKEN',
                'description': 'GITHUB authentication token',
                'provider': 'github',
            },
            {
                'name': 'LINEAR_TOKEN',
                'description': 'Linear OAuth access token',
                'provider': None,
            },
        ]
    }
    assert GITHUB_TOKEN_VALUE not in response.text


def test_endpoint_is_empty_without_connected_accounts(client_for):
    response = client_for(FakeUserContext()).get('/secrets/oauth-tokens')

    assert response.status_code == 200
    assert response.json() == {'items': []}
