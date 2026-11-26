import json
import os
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.app_server.file_store.files import FileStore
from openhands.app_server.settings.file_settings_store import FileSettingsStore
from openhands.app_server.settings.settings_models import Settings
from openhands.sdk.llm import LLM
from openhands.sdk.settings import ConversationSettings, OpenHandsAgentSettings


@pytest.fixture(autouse=True)
def allow_short_context_windows():
    with patch.dict(os.environ, {'ALLOW_SHORT_CONTEXT_WINDOWS': 'true'}, clear=False):
        yield


@pytest.fixture
def mock_file_store():
    return MagicMock(spec=FileStore)


@pytest.fixture
def file_settings_store(mock_file_store):
    return FileSettingsStore(mock_file_store)


@pytest.mark.asyncio
async def test_load_nonexistent_data(file_settings_store):
    file_settings_store.file_store.read.side_effect = FileNotFoundError()
    assert await file_settings_store.load() is None


@pytest.mark.asyncio
async def test_store_and_load_data(file_settings_store):
    # Test data
    init_data = Settings(
        language='python',
        agent_settings=OpenHandsAgentSettings(
            agent='test-agent',
            llm=LLM(
                model='test-model',
                api_key=SecretStr('test-key'),
                base_url='https://test.com',
            ),
        ),
        conversation_settings=ConversationSettings(
            max_iterations=100,
            security_analyzer='llm',
            confirmation_mode=True,
        ),
    )

    # Store data
    await file_settings_store.edit(init_data, set())

    # Verify store called with correct JSON
    expected_json = init_data.model_dump_json(
        context={'expose_secrets': True, 'persist_settings': True}
    )
    file_settings_store.file_store.write.assert_called_once_with(
        'settings.json', expected_json
    )

    # Setup mock for load
    file_settings_store.file_store.read.return_value = expected_json

    # Load and verify data
    loaded_data = await file_settings_store.load()
    assert loaded_data is not None
    assert loaded_data.language == init_data.language
    assert loaded_data.agent_settings.agent == init_data.agent_settings.agent
    assert (
        loaded_data.conversation_settings.max_iterations
        == init_data.conversation_settings.max_iterations
    )
    assert (
        loaded_data.conversation_settings.security_analyzer
        == init_data.conversation_settings.security_analyzer
    )
    assert (
        loaded_data.conversation_settings.confirmation_mode
        == init_data.conversation_settings.confirmation_mode
    )
    assert loaded_data.agent_settings.llm.model == init_data.agent_settings.llm.model
    assert loaded_data.agent_settings.llm.api_key is not None
    assert init_data.agent_settings.llm.api_key is not None
    assert (
        loaded_data.agent_settings.llm.api_key.get_secret_value()
        == init_data.agent_settings.llm.api_key.get_secret_value()
    )
    assert (
        loaded_data.agent_settings.llm.base_url == init_data.agent_settings.llm.base_url
    )


@pytest.mark.asyncio
async def test_load_seeds_default_profile_from_legacy_llm(file_settings_store):
    legacy_payload = {
        'agent_settings': {
            'llm': {
                'model': 'openai/gpt-4o',
                'api_key': 'legacy-key',
                'base_url': 'https://example.com',
            },
        },
    }
    file_settings_store.file_store.read.return_value = json.dumps(legacy_payload)

    loaded = await file_settings_store.load()

    assert loaded is not None
    assert loaded.llm_profiles.active == 'Default'
    default_profile = loaded.llm_profiles.require('Default')
    assert default_profile.model == 'openai/gpt-4o'
    assert default_profile.base_url == 'https://example.com'
    assert default_profile.api_key.get_secret_value() == 'legacy-key'


@pytest.mark.asyncio
async def test_load_does_not_overwrite_existing_profiles(file_settings_store):
    payload = {
        'agent_settings': {
            'llm': {'model': 'openai/gpt-4o', 'api_key': 'legacy-key'},
        },
        'llm_profiles': {
            'profiles': {'Saved': {'model': 'anthropic/claude-opus-4'}},
            'active': 'Saved',
        },
    }
    file_settings_store.file_store.read.return_value = json.dumps(payload)

    loaded = await file_settings_store.load()

    assert loaded is not None
    assert set(loaded.llm_profiles.profiles) == {'Saved'}
    assert loaded.llm_profiles.active == 'Saved'


@pytest.mark.asyncio
async def test_load_skips_seeding_when_no_legacy_model(file_settings_store):
    file_settings_store.file_store.read.return_value = json.dumps({})

    loaded = await file_settings_store.load()

    assert loaded is not None
    assert loaded.llm_profiles.profiles == {}
    assert loaded.llm_profiles.active is None


@pytest.mark.asyncio
async def test_get_instance():
    mock_store = MagicMock(spec=FileStore)

    with patch('openhands.app_server.config.get_global_config') as mock_get_config:
        mock_config = MagicMock()
        mock_config.file_store = mock_store
        mock_get_config.return_value = mock_config

        store = await FileSettingsStore.get_instance(None)

        assert isinstance(store, FileSettingsStore)
        assert store.file_store == mock_store
        mock_get_config.assert_called_once()


@pytest.mark.asyncio
async def test_migrate_legacy_provider_tokens_without_losing_settings():
    from openhands.app_server.file_store.memory import InMemoryFileStore
    from openhands.app_server.integrations.service_types import ProviderType
    from openhands.app_server.secrets.file_secrets_store import FileSecretsStore
    from openhands.app_server.settings.settings_router import (
        invalidate_legacy_secrets_store,
    )

    files = InMemoryFileStore()
    store = FileSettingsStore(files)
    secrets = FileSecretsStore(files)
    files.write(
        'settings.json',
        json.dumps(
            {
                'language': 'fr',
                'secrets_store': {
                    'provider_tokens': {'github': {'token': 'legacy-token'}}
                },
            }
        ),
    )

    settings = await store.load()
    assert settings is not None
    await invalidate_legacy_secrets_store(settings, store, secrets)

    migrated = await secrets.load()
    assert migrated is not None
    assert (
        migrated.provider_tokens[ProviderType.GITHUB].token.get_secret_value()
        == 'legacy-token'
    )
    reloaded = await store.load()
    assert reloaded is not None
    assert reloaded.language == 'fr'
    assert await invalidate_legacy_secrets_store(reloaded, store, secrets) is None
