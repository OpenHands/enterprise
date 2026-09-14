"""Settings patches preserve supported SDK inputs and sparse update semantics."""

import pytest
from fastmcp.mcp_config import MCPConfig
from pydantic import BaseModel, SecretStr, TypeAdapter, ValidationError

from openhands.app_server.file_store.memory import InMemoryFileStore
from openhands.app_server.settings.file_settings_store import FileSettingsStore
from openhands.app_server.settings.llm_profiles import LLMProfiles, StrictLLM
from openhands.app_server.settings.settings_models import (
    MarketplaceRegistration,
    MarketplaceScope,
    Settings,
)
from openhands.app_server.settings.settings_patch import secret_text
from openhands.app_server.settings.settings_router import store_settings
from openhands.sdk.llm import LLM
from openhands.sdk.mcp.config import MCPServer, dump_mcp_config
from openhands.sdk.settings import ConversationSettings, OpenHandsAgentSettings


class _StdioConfig(BaseModel):
    command: str
    env: dict[str, str]


_MCP_CONFIG = TypeAdapter(dict[str, _StdioConfig])


def direct_settings() -> Settings:
    return Settings(
        agent_settings=OpenHandsAgentSettings(
            llm=LLM(model='openai/gpt-4o', api_key=SecretStr('original-key'))
        )
    )


def test_internal_sdk_values_preserve_credentials_and_marketplace_scope() -> None:
    settings = direct_settings()
    marketplace = MarketplaceRegistration(
        name='personal', source='github:owner/repo', scope=MarketplaceScope.ORG
    )
    mcp_config = MCPConfig.model_validate(
        {'mcpServers': {'tool': {'command': 'tool', 'env': {'TOKEN': 'mcp-key'}}}}
    )

    settings.update(
        {
            'agent_settings_diff': {
                'llm': {'api_key': SecretStr('replacement-key')},
                'mcp_config': mcp_config,
            },
            'registered_marketplaces': [marketplace],
            'search_api_key': SecretStr('search-key'),
        }
    )

    assert secret_text(settings.agent_settings.llm.api_key) == 'replacement-key'
    assert settings.search_api_key == SecretStr('search-key')
    assert settings.registered_marketplaces[0].scope == MarketplaceScope.PERSONAL
    assert marketplace.scope == MarketplaceScope.ORG
    mcp_dump = _MCP_CONFIG.validate_python(
        dump_mcp_config(
            settings.agent_settings.mcp_config,
            context={'expose_secrets': 'plaintext'},
        )
    )
    assert mcp_dump['tool'].env == {'TOKEN': 'mcp-key'}


def test_sparse_product_updates_preserve_omissions_and_nullable_clears() -> None:
    settings = Settings(
        agent_settings=direct_settings().agent_settings,
        language='fr',
        git_full_clone=True,
        enable_sound_notifications=True,
        search_api_key=SecretStr('search-key'),
        disabled_skills=['skill'],
    )

    settings.update({'language': None, 'search_api_key': '', 'email': None})

    assert settings.language is None
    assert settings.email is None
    assert settings.search_api_key is None
    assert settings.git_full_clone is True
    assert settings.enable_sound_notifications is True
    assert settings.disabled_skills == ['skill']
    assert secret_text(settings.agent_settings.llm.api_key) == 'original-key'


def test_sdk_model_leaves_preserve_plaintext_secrets() -> None:
    settings = direct_settings()
    settings.update(
        {
            'agent_settings_diff': {
                'llm': LLM(model='openai/gpt-4o', api_key=SecretStr('new-key')),
                'mcp_config': {
                    'tool': MCPServer(
                        command='tool', env={'TOKEN': SecretStr('mcp-key')}
                    )
                },
            }
        }
    )
    assert secret_text(settings.agent_settings.llm.api_key) == 'new-key'
    assert settings.agent_settings.mcp_config['tool'].env == {
        'TOKEN': SecretStr('mcp-key')
    }


def test_conversation_null_resets_default_without_clearing_other_settings() -> None:
    settings = direct_settings()
    settings.conversation_settings = ConversationSettings(max_iterations=17)

    settings.update({'conversation_settings_diff': {'max_iterations': None}})

    assert (
        settings.conversation_settings.max_iterations
        == ConversationSettings().max_iterations
    )
    assert secret_text(settings.agent_settings.llm.api_key) == 'original-key'


def test_sparse_mcp_deletion_preserves_untouched_credentials() -> None:
    settings = direct_settings()
    settings.update(
        {
            'agent_settings_diff': {
                'mcp_config': {
                    'removed': {'command': 'remove'},
                    'kept': {'command': 'keep', 'env': {'TOKEN': 'kept-key'}},
                }
            }
        }
    )

    settings.update({'agent_settings_diff': {'mcp_config': {'removed': None}}})

    assert set(settings.agent_settings.mcp_config) == {'kept'}
    dumped = _MCP_CONFIG.validate_python(
        dump_mcp_config(
            settings.agent_settings.mcp_config,
            context={'expose_secrets': 'plaintext'},
        )
    )
    assert dumped['kept'].env == {'TOKEN': 'kept-key'}


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['registered_marketplaces', 'git_full_clone'])
async def test_invalid_null_patch_is_client_error_without_persisting_changes(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    monkeypatch.setenv('ENABLE_LITELLM', 'false')
    memory = InMemoryFileStore()
    store = FileSettingsStore(memory)
    original = direct_settings()
    original.language = 'fr'
    await store.store(original)
    original_contents = memory.read(store.path)

    response = await store_settings(
        {field: None, 'language': 'de'}, settings_store=store, user_id=None
    )

    assert response.status_code == 400
    assert memory.read(store.path) == original_contents


@pytest.mark.asyncio
async def test_disabled_gateway_checks_changed_credential_and_keeps_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('ENABLE_LITELLM', 'false')
    store = FileSettingsStore(InMemoryFileStore())
    historical = Settings(
        agent_settings=OpenHandsAgentSettings(
            llm=LLM(model='openhands/managed', api_key=SecretStr('original-key'))
        )
    )
    await store.store(historical)

    rejected = await store_settings(
        {'agent_settings_diff': {'llm': {'api_key': 'replacement-key'}}},
        settings_store=store,
        user_id=None,
    )
    assert rejected.status_code == 422

    allowed = await store_settings(
        {'language': 'de'}, settings_store=store, user_id=None
    )
    assert allowed.status_code == 200


def test_profile_validation_preserves_valid_instances_and_rejects_unknown_fields() -> (
    None
):
    llm = LLM(model='openai/gpt-4o', api_key=SecretStr('profile-key'))
    profiles = LLMProfiles.model_validate(
        {'profiles': {'valid': llm, 'invalid': {'model': None}}, 'active': 'valid'}
    )
    assert profiles.require('valid') is llm
    assert not profiles.has('invalid')
    summary = profiles.summaries()[0]
    assert summary['api_key_set'] is True
    assert summary['provider_connection_id'] is None
    assert 'profile-key' not in str(summary)
    restored = StrictLLM.model_validate(
        {'model': 'openai/gpt-4o', 'is_subscription': True}
    )
    assert restored.model == 'openai/gpt-4o'
    with pytest.raises(ValidationError):
        StrictLLM.model_validate({'model': 'openai/gpt-4o', 'typo': 'invalid'})
