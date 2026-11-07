from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import SecretStr

from openhands.app_server.settings.settings_models import Settings
from openhands.sdk.settings import (
    LLMProfileDocument,
    ProductSettings,
    SettingsDocument,
    SettingsDocumentPatch,
    SettingsProfilePointers,
    SettingsResources,
    SettingsSecretReference,
)


def _secret_is_set(value: object) -> bool:
    if value is None:
        return False
    raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
    return bool(raw.strip())


def _safe_agent_document(settings: Settings) -> dict[str, Any]:
    return settings.agent_settings.model_dump(
        mode='json',
        exclude={
            'llm': {'api_key'},
            'mcp_config': True,
            'verification': {'critic_api_key'},
            'agent_context': {'secrets'},
        },
    )


def _safe_agent_patch(agent: dict[str, Any]) -> dict[str, Any]:
    patch = deepcopy(agent)
    llm = patch.get('llm')
    if isinstance(llm, dict):
        llm.pop('api_key', None)
    verification = patch.get('verification')
    if isinstance(verification, dict):
        verification.pop('critic_api_key', None)
    agent_context = patch.get('agent_context')
    if isinstance(agent_context, dict):
        agent_context.pop('secrets', None)
    patch.pop('mcp_config', None)
    return patch


def build_settings_document(settings: Settings) -> SettingsDocument:
    profiles = {
        name: LLMProfileDocument(
            settings=llm.model_dump(mode='json', exclude={'api_key'}),
            secret_references=(
                {
                    'api_key': SettingsSecretReference(
                        ref=f'settings:llm_profiles.{name}.api_key',
                        is_set=True,
                    )
                }
                if _secret_is_set(llm.api_key)
                else {}
            ),
        )
        for name, llm in settings.llm_profiles.profiles.items()
    }
    credentials = {
        name: SettingsSecretReference(ref=f'settings:{name}', is_set=is_set)
        for name, is_set in {
            'agent.llm.api_key': settings.llm_api_key_is_set,
            'search_api_key': _secret_is_set(settings.search_api_key),
            'sandbox_api_key': _secret_is_set(settings.sandbox_api_key),
        }.items()
    }
    marketplaces = [
        marketplace.model_dump(exclude={'scope'})
        for marketplace in (
            *settings.inherited_marketplaces,
            *settings.registered_marketplaces,
        )
    ]
    return SettingsDocument(
        agent=_safe_agent_document(settings),
        conversation=settings.conversation_settings.model_dump(mode='json'),
        product=ProductSettings(
            language=settings.language or 'en',
            user_consents_to_analytics=settings.user_consents_to_analytics,
            enable_sound_notifications=settings.enable_sound_notifications,
            git_user_name=settings.git_user_name or '',
            git_user_email=settings.git_user_email or '',
            title_llm_profile=settings.title_llm_profile,
            disabled_skills=settings.disabled_skills or [],
            enabled_skills=settings.enabled_skills,
            run_router_at_conversation_start=settings.run_router_at_conversation_start,
            workspace_file_discovery=settings.workspace_file_discovery,
            default_profile_migrated=settings.default_profile_migrated,
        ),
        resources=SettingsResources(
            llm_profiles=profiles,
            marketplaces=marketplaces,
            credentials=credentials,
            pointers=SettingsProfilePointers(
                active_llm_profile=settings.llm_profiles.active,
                active_agent_profile_id=settings.active_agent_profile_id,
            ),
        ),
    )


def settings_document_patch_payload(
    patch: SettingsDocumentPatch,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if 'agent' in patch.model_fields_set and patch.agent is not None:
        payload['agent_settings_diff'] = _safe_agent_patch(patch.agent)
    if 'conversation' in patch.model_fields_set and patch.conversation is not None:
        payload['conversation_settings_diff'] = patch.conversation
    if 'product' in patch.model_fields_set and patch.product is not None:
        payload.update(patch.product.model_dump(exclude_unset=True))
    return payload
