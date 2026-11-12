from typing import Any

import pytest
from pydantic import SecretStr

from openhands.app_server.settings.settings_document import (
    build_settings_document,
    settings_document_patch_payload,
)
from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.settings.settings_router import patch_settings_document
from openhands.sdk.settings import SettingsDocument, SettingsDocumentPatch


class RecordingSettingsStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.payload: dict[str, Any] | None = None

    async def load(self) -> Settings:
        return self.settings

    async def store_document_patch(
        self,
        payload: dict[str, Any],
        effective_settings: Settings,
    ) -> None:
        self.payload = payload
        self.settings = effective_settings


def test_document_replaces_secret_values_with_opaque_references() -> None:
    settings = Settings()
    settings.agent_settings.llm.api_key = SecretStr('never-return-this')
    settings.search_api_key = SecretStr('or-this')
    settings.llm_profiles.save(
        'Team',
        settings.agent_settings.llm.model_copy(
            update={
                'model': 'openai/team-model',
                'api_key': SecretStr('profile-secret'),
            }
        ),
    )

    document = build_settings_document(settings).model_dump(mode='json')

    assert document['agent']['llm']['api_key'] is None
    serialized = str(document)
    assert 'never-return-this' not in serialized
    assert 'or-this' not in serialized
    assert 'profile-secret' not in serialized
    assert document['resources']['llm_profiles']['Team']['settings']['model'] == (
        'openai/team-model'
    )
    assert (
        document['resources']['llm_profiles']['Team']['secret_references']['api_key'][
            'is_set'
        ]
        is True
    )
    assert document['resources']['credentials']['agent.llm.api_key'] == {
        'ref': 'settings:agent.llm.api_key',
        'is_set': True,
    }
    assert document['resources']['credentials']['search_api_key']['is_set'] is True


def test_patch_translation_is_sparse_and_drops_secret_plaintext() -> None:
    patch = SettingsDocumentPatch(
        agent={
            'llm': {
                'temperature': 0.25,
                'base_url': None,
                'api_key': 'must-not-be-accepted',
            },
            'agent_context': {'secrets': {'TOKEN': 'must-not-be-accepted'}},
        },
        product={'language': 'fr'},
    )

    payload = settings_document_patch_payload(patch)

    assert payload == {
        'agent_settings_diff': {
            'llm': {'temperature': 0.25, 'base_url': None},
            'agent_context': {},
        },
        'language': 'fr',
    }


@pytest.mark.asyncio
async def test_patch_endpoint_validates_and_persists_only_sparse_fields() -> None:
    store = RecordingSettingsStore(Settings(language='en'))

    response = await patch_settings_document(
        SettingsDocumentPatch(
            conversation={'max_iterations': 12},
            product={'language': 'fr'},
        ),
        settings_store=store,  # type: ignore[arg-type]
        user_id='user-id',
    )

    assert isinstance(response, SettingsDocument)
    assert response.product.language == 'fr'
    assert response.conversation.max_iterations == 12
    assert store.payload == {
        'conversation_settings_diff': {'max_iterations': 12},
        'language': 'fr',
    }


@pytest.mark.asyncio
async def test_patch_endpoint_rejects_resource_mutation() -> None:
    response = await patch_settings_document(
        SettingsDocumentPatch(resources={'pointers': {'active_llm_profile': None}}),
        settings_store=RecordingSettingsStore(Settings()),  # type: ignore[arg-type]
        user_id='user-id',
    )

    assert response.status_code == 422
