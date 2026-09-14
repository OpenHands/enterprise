"""Resolver provider tokens preserve raw values and export usable environment values."""

from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from integrations.resolver_context import ResolverUserContext
from openhands.app_server.integrations.provider import (
    PROVIDER_TOKEN_TYPE,
    ProviderToken,
    ProviderType,
)
from openhands.app_server.user_auth.user_auth import UserAuth


@pytest.mark.parametrize(
    ('tokens', 'environment'),
    [
        (None, {}),
        ({}, {}),
        (
            {
                ProviderType.GITHUB: ProviderToken(token=SecretStr('github-token')),
                ProviderType.GITLAB: ProviderToken(token=SecretStr('')),
                ProviderType.BITBUCKET: ProviderToken(token=None),
            },
            {'github_token': 'github-token'},
        ),
    ],
)
async def test_resolver_provider_token_modes(
    tokens: PROVIDER_TOKEN_TYPE | None, environment: dict[str, str]
) -> None:
    auth = AsyncMock(spec=UserAuth)
    auth.get_provider_tokens.return_value = tokens
    context = ResolverUserContext(auth)

    assert await context.get_provider_tokens() is tokens
    assert await context.get_provider_tokens(False) is tokens
    assert await context.get_provider_tokens(True) == environment
    assert auth.get_provider_tokens.await_count == 3
