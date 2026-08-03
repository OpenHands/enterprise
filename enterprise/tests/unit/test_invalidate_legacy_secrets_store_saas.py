"""Regression test: legacy secrets_store migration against the production
SaasSecretsStore path (Postgres delete-then-insert + JWT encryption), not just
the simpler FileSecretsStore used in the app_server unit tests.
"""

from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from pydantic import SecretStr
from storage.saas_secrets_store import SaasSecretsStore

from openhands.app_server.integrations.provider import (
    CustomSecret,
    ProviderToken,
    ProviderType,
)
from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.settings.settings_router import (
    invalidate_legacy_secrets_store,
)
from openhands.app_server.utils.encryption_key import EncryptionKey


def _make_jwt_service() -> JwtService:
    key = EncryptionKey(id='test', key=SecretStr('test_secret'), active=True)
    return JwtService(keys=[key])


@pytest.fixture
def jwt_svc():
    return _make_jwt_service()


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.current_org_id = UUID('a1111111-1111-1111-1111-111111111111')
    return user


@pytest.fixture
def secrets_store(async_session_maker, jwt_svc):
    import storage.saas_secrets_store as store_module

    store_module.a_session_maker = async_session_maker

    store = SaasSecretsStore('user-id', jwt_svc)
    store.a_session_maker = async_session_maker
    return store


@pytest.mark.asyncio
@patch(
    'storage.saas_secrets_store.UserStore.get_user_by_id',
    new_callable=AsyncMock,
)
async def test_invalidate_legacy_secrets_store_preserves_custom_secrets_saas(
    mock_get_user, secrets_store, mock_user
):
    mock_get_user.return_value = mock_user

    # A custom secret already exists in the dedicated (encrypted, Postgres-backed)
    # secrets store, as it would for a real SaaS user.
    await secrets_store.store(
        Secrets(
            custom_secrets=MappingProxyType(
                {
                    'MY_API_KEY': CustomSecret.from_value(
                        {'secret': 'super-secret', 'description': ''}
                    )
                }
            )
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

    stored = await secrets_store.load()
    assert stored is not None
    assert 'MY_API_KEY' in stored.custom_secrets
    assert (
        stored.custom_secrets['MY_API_KEY'].secret.get_secret_value() == 'super-secret'
    )


@pytest.mark.asyncio
@patch(
    'storage.saas_secrets_store.UserStore.get_user_by_id',
    new_callable=AsyncMock,
)
async def test_invalidate_legacy_secrets_store_saas_does_not_persist_provider_tokens(
    mock_get_user, secrets_store, mock_user
):
    """Documents a known, pre-existing limitation (not fixed by this PR):
    SaasSecretsStore.store() never persists provider_tokens (see
    `del kwargs['provider_tokens']` in SaasSecretsStore.store()), and load()
    never returns them. So on the SaaS path, invalidate_legacy_secrets_store's
    return value carries the migrated provider tokens for the in-flight
    request, but nothing durably retains them once settings.secrets_store is
    cleared afterwards. Tracked separately from the custom-secrets data loss
    this PR fixes.
    """
    mock_get_user.return_value = mock_user

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
    assert stored.provider_tokens == {}
