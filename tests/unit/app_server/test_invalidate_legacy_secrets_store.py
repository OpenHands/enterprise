"""Regression test: legacy secrets_store migration must not delete custom secrets."""

from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from openhands.app_server.file_store.memory import InMemoryFileStore
from openhands.app_server.integrations.provider import (
    CustomSecret,
    ProviderToken,
    ProviderType,
)
from openhands.app_server.secrets.file_secrets_store import FileSecretsStore
from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.settings.settings_router import (
    invalidate_legacy_secrets_store,
)


@pytest.mark.asyncio
async def test_invalidate_legacy_secrets_store_preserves_custom_secrets():
    secrets_store = FileSecretsStore(InMemoryFileStore())

    # A custom secret already exists in the dedicated secrets store.
    await secrets_store.store(
        Secrets(
            custom_secrets={
                'MY_API_KEY': CustomSecret(secret=SecretStr('super-secret'))
            }
        )
    )

    settings_store = AsyncMock()
    settings = Settings(
        secrets_store=Secrets(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(token=SecretStr('legacy-token'))
            }
        )
    )

    result = await invalidate_legacy_secrets_store(
        settings, settings_store, secrets_store
    )

    assert result is not None
    assert ProviderType.GITHUB in result.provider_tokens

    stored = await secrets_store.load()
    assert stored is not None
    assert 'MY_API_KEY' in stored.custom_secrets
    assert (
        stored.custom_secrets['MY_API_KEY'].secret.get_secret_value() == 'super-secret'
    )


@pytest.mark.asyncio
async def test_invalidate_legacy_secrets_store_prefers_already_stored_provider_token():
    secrets_store = FileSecretsStore(InMemoryFileStore())

    # A provider token was already migrated/reconnected directly in the
    # dedicated store since the legacy field was last read.
    await secrets_store.store(
        Secrets(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(token=SecretStr('fresh-token'))
            }
        )
    )

    settings_store = AsyncMock()
    settings = Settings(
        secrets_store=Secrets(
            provider_tokens={
                ProviderType.GITHUB: ProviderToken(
                    token=SecretStr('stale-legacy-token')
                ),
                ProviderType.GITLAB: ProviderToken(
                    token=SecretStr('legacy-gitlab-token')
                ),
            }
        )
    )

    result = await invalidate_legacy_secrets_store(
        settings, settings_store, secrets_store
    )

    assert result is not None
    # Already-stored token wins over the stale legacy one for the same provider...
    assert (
        result.provider_tokens[ProviderType.GITHUB].token.get_secret_value()
        == 'fresh-token'
    )
    # ...but legacy still fills in providers not already present.
    assert (
        result.provider_tokens[ProviderType.GITLAB].token.get_secret_value()
        == 'legacy-gitlab-token'
    )
