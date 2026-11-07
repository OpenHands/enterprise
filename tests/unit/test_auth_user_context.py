"""Tests for ``AuthUserContext.get_secrets`` secret construction."""

from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

import pytest

from openhands.app_server.integrations.provider import (
    ORGANIZATION_SCOPE_DESCRIPTION,
    PERSONAL_SCOPE_DESCRIPTION,
    CustomSecret,
    CustomSecretScope,
)
from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.user.auth_user_context import AuthUserContext
from openhands.sdk.secret import StaticSecret

# The SDK advertises grouped scopes only from the release that adds ``scope`` /
# ``scope_description`` to ``SecretSource`` (software-agent-sdk#5672). Older
# pinned SDKs silently ignore the kwargs, so the scope-specific assertions are
# skipped until the pin is bumped.
_SDK_HAS_SCOPE = 'scope' in StaticSecret.model_fields


def _context_with_secrets(secrets: Secrets) -> AuthUserContext:
    user_auth = MagicMock()
    user_auth.get_secrets = AsyncMock(return_value=secrets)
    return AuthUserContext(user_auth=user_auth)


def _secrets() -> Secrets:
    return Secrets(
        custom_secrets=MappingProxyType(
            {
                'PERSONAL_TOKEN': CustomSecret.from_value(
                    {'secret': 'mine', 'description': 'my key'}
                ),
                'SHARED_TOKEN': CustomSecret(
                    secret=CustomSecret.from_value({'secret': 'bot'}).secret,
                    description='bot key',
                    scope=CustomSecretScope.ORGANIZATION,
                ),
            }
        )
    )


@pytest.mark.asyncio
async def test_get_secrets_builds_static_secrets_with_values_and_descriptions():
    context = _context_with_secrets(_secrets())

    results = await context.get_secrets()

    assert set(results) == {'PERSONAL_TOKEN', 'SHARED_TOKEN'}
    assert isinstance(results['PERSONAL_TOKEN'], StaticSecret)
    assert results['PERSONAL_TOKEN'].get_value() == 'mine'
    assert results['PERSONAL_TOKEN'].description == 'my key'
    assert results['SHARED_TOKEN'].get_value() == 'bot'
    assert results['SHARED_TOKEN'].description == 'bot key'


@pytest.mark.asyncio
async def test_get_secrets_empty_when_no_secrets():
    context = _context_with_secrets(Secrets())
    assert await context.get_secrets() == {}


@pytest.mark.skipif(
    not _SDK_HAS_SCOPE,
    reason='pinned openhands-sdk predates SecretSource.scope (software-agent-sdk#5672)',
)
@pytest.mark.asyncio
async def test_get_secrets_carries_scope_and_description():
    context = _context_with_secrets(_secrets())

    results = await context.get_secrets()

    personal = results['PERSONAL_TOKEN']
    assert personal.scope == CustomSecretScope.PERSONAL.value
    assert personal.scope_description == PERSONAL_SCOPE_DESCRIPTION

    shared = results['SHARED_TOKEN']
    assert shared.scope == CustomSecretScope.ORGANIZATION.value
    assert shared.scope_description == ORGANIZATION_SCOPE_DESCRIPTION
