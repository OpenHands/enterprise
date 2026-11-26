from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Any

from fastapi import HTTPException

from openhands.app_server.file_store.files import FileStore
from openhands.app_server.settings.scoped import Inherit, edit
from openhands.app_server.settings.settings_models import Settings, apply_agent_values
from openhands.app_server.settings.settings_store import SettingsStore
from openhands.app_server.utils.async_utils import call_sync_from_async
from openhands.sdk.settings import ConversationSettings, default_agent_settings

_file_write_lock = Lock()


def _revision_digest(content: str) -> str:
    """Key the settings-revision digest with an HMAC under the JWT secret.

    The file may contain persisted LLM API keys and other credentials, so a
    plain SHA-256 would trip CodeQL's weak-sensitive-data-hashing rule.
    """
    from openhands.app_server.config import get_global_config

    injector = get_global_config().jwt
    if injector is None:
        raise RuntimeError('Settings revisions require a configured JWT service')
    jwt = injector.get_jwt_service()
    key = jwt.get_key(jwt.default_key_id).key.get_secret_value()
    return hmac.new(key.encode(), content.encode(), hashlib.sha256).hexdigest()


@dataclass
class FileSettingsStore(SettingsStore):
    file_store: FileStore
    path: str = 'settings.json'

    async def load(
        self,
        *,
        resolve_agent_profile: bool = False,
        override_agent_profile_id: str | None = None,
    ) -> Settings | None:
        # No Agent Profile concept in the local file store; nothing to resolve.
        del resolve_agent_profile, override_agent_profile_id
        try:
            json_str = await call_sync_from_async(self.file_store.read, self.path)
            kwargs = json.loads(json_str)
            # Seed a Default profile from legacy agent_settings.llm when
            # llm_profiles is absent — pre-llm_profiles settings.json files
            # would otherwise present an empty profiles UI on upgrade.
            if 'llm_profiles' not in kwargs:
                legacy_llm = (kwargs.get('agent_settings') or {}).get('llm')
                if isinstance(legacy_llm, dict) and legacy_llm.get('model'):
                    kwargs['llm_profiles'] = {
                        'profiles': {'Default': legacy_llm},
                        'active': 'Default',
                    }
            settings = Settings(**kwargs)

            # Turn on V1 in OpenHands
            # We can simplify / remove this as part of V0 removal
            settings.v1_enabled = True
            settings.settings_revision = _revision_digest(json_str)

            return settings
        except FileNotFoundError:
            return None

    def _write(self, settings: Settings, revision: str | None) -> None:
        with _file_write_lock:
            try:
                current = self.file_store.read(self.path)
            except FileNotFoundError:
                current = None
            if revision is not None and (
                current is None or revision != _revision_digest(current)
            ):
                raise HTTPException(409, 'Settings changed. Refresh and try again.')
            value = settings.model_dump_json(
                context={'expose_secrets': True, 'persist_settings': True}
            )
            if value != current:
                self.file_store.write(self.path, value)

    async def edit(
        self,
        settings: Settings,
        fields: set[str],
        *,
        submitted: dict[str, Any] | None = None,
        inherit: Sequence[tuple[str, ...]] = (),
        revision: str | None = None,
    ) -> None:
        if inherit:
            # A standalone file inherits factory defaults. Keep separately managed
            # credentials and policy out of the reset, including whole-block resets.
            from openhands.app_server.settings.meta_profiles import (
                ORG_OWNED_ROUTER_SETTINGS_KEYS,
            )

            resets = tuple(Inherit(path) for path in inherit)
            values = settings.model_dump(
                mode='json', context={'expose_secrets': True, 'persist_settings': True}
            )
            candidate = edit(values, resets)
            if any(path[0] == 'agent_settings' for path in inherit):
                agent = candidate.get('agent_settings', {})
                previous = values['agent_settings']
                agent.pop('schema_version', None)
                for key in ORG_OWNED_ROUTER_SETTINGS_KEYS | {'mcp_config'}:
                    if key in previous:
                        agent[key] = previous[key]
                if agent.get('agent_kind', 'openhands') == 'openhands':
                    llm = agent.setdefault('llm', {})
                    llm.pop('is_subscription', None)
                    if key := previous.get('llm', {}).get('api_key'):
                        llm['api_key'] = key
                settings.agent_settings = apply_agent_values(
                    default_agent_settings(), agent
                )
            if any(path[0] == 'conversation_settings' for path in inherit):
                settings.conversation_settings = ConversationSettings.model_validate(
                    candidate.get('conversation_settings', {})
                )
        await call_sync_from_async(
            self._write, settings, revision or settings.settings_revision
        )

    @classmethod
    async def get_instance(cls, user_id: str | None) -> FileSettingsStore:
        """Get a FileSettingsStore instance using the global config's file_store.

        TODO: This method should be replaced with dependency injection.
        """
        from openhands.app_server.config import get_global_config

        file_store = get_global_config().file_store
        return FileSettingsStore(file_store)

    async def get_org_marketplaces(self, user_id: str | None) -> list[dict]:
        """Get organization-level marketplaces.

        OSS mode has no org concept, so returns empty list.
        """
        return []
