"""Tests for the write-time managed-key verify-and-rotate logic.

Covers ``_maybe_rotate_stale_managed_key`` and its integration into the three
endpoints that persist LLM configs (``save_profile``, ``activate_profile``,
``store_settings``). Uses the same ``sys.modules`` mocking pattern as the
conversation-service tests to stub the enterprise-only ``storage`` modules.
"""

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from openhands.sdk.llm import LLM
from openhands.sdk.settings import OpenHandsAgentSettings
from pydantic import SecretStr

from openhands.app_server.app import app
from openhands.app_server.file_store import get_file_store
from openhands.app_server.integrations.provider import ProviderToken, ProviderType
from openhands.app_server.integrations.service_types import UserGitInfo
from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.secrets.secrets_store import SecretsStore
from openhands.app_server.settings.file_settings_store import FileSettingsStore
from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.settings.settings_router import (
    _maybe_rotate_stale_managed_key,
)
from openhands.app_server.settings.settings_store import SettingsStore
from openhands.app_server.user_auth.user_auth import UserAuth

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_MANAGED_URL = 'https://llm-proxy.app.all-hands.dev'


class _MockUserAuth(UserAuth):
    _store: SettingsStore | None = None

    def __init__(self, settings_store: SettingsStore) -> None:
        self._store = settings_store

    async def get_user_id(self) -> str | None:
        return 'test-user'

    async def get_user_email(self) -> str | None:
        return 'test@example.com'

    async def get_access_token(self) -> SecretStr | None:
        return SecretStr('test-token')

    async def get_provider_tokens(
        self,
    ) -> dict[ProviderType, ProviderToken] | None:
        return None

    async def get_user_settings_store(self) -> SettingsStore | None:
        return self._store

    async def get_secrets_store(self) -> SecretsStore | None:
        return None

    async def get_secrets(self) -> Secrets | None:
        return None

    async def get_mcp_api_key(self) -> str | None:
        return None

    async def get_user_git_info(self) -> UserGitInfo | None:
        return None

    @classmethod
    async def get_instance(cls, request: Request) -> UserAuth:
        raise NotImplementedError

    @classmethod
    async def get_for_user(cls, user_id: str) -> UserAuth:
        raise NotImplementedError


@pytest.fixture
def settings_store(tmp_path: Path) -> FileSettingsStore:
    return FileSettingsStore(get_file_store('local', str(tmp_path)))


def _make_saas_test_client(
    monkeypatch,
    file_store: FileSettingsStore,
    *,
    verify_return: bool = False,
    rotate_new_key: str | None = 'sk-fresh',
):
    """Build a TestClient whose injected settings store is a fake
    SaasSettingsStore (from the mocked enterprise modules), backed by the
    given FileSettingsStore for load/store.

    The _MockUserAuth returns the fake SaasSettingsStore so the isinstance
    check in _maybe_rotate_stale_managed_key passes.
    """
    mocks = _install_managed_key_modules(
        monkeypatch,
        verify_return=verify_return,
        rotate_new_key=rotate_new_key,
    )
    from storage.saas_settings_store import SaasSettingsStore

    # Wrap the FileSettingsStore as a SaasSettingsStore subclass that
    # delegates load/store to the file store but passes isinstance.
    class _SaasFileStore(SaasSettingsStore, FileSettingsStore):
        pass

    saas_store = _SaasFileStore(file_store.file_store)  # reuse the same file store
    auth = _MockUserAuth(saas_store)
    with (
        patch.dict(
            'os.environ',
            {'SESSION_API_KEY': '', 'ALLOW_SHORT_CONTEXT_WINDOWS': 'true'},
            clear=False,
        ),
        patch('openhands.app_server.utils.dependencies._SESSION_API_KEY', None),
        patch(
            'openhands.app_server.user_auth.user_auth.UserAuth.get_instance',
            return_value=auth,
        ),
        patch(
            'openhands.app_server.user_auth.default_user_auth.DefaultUserAuth.get_instance',
            return_value=auth,
        ),
        patch(
            'openhands.app_server.settings.file_settings_store.FileSettingsStore.get_instance',
            AsyncMock(return_value=saas_store),
        ),
    ):
        yield TestClient(app), mocks, saas_store


def _install_managed_key_modules(
    monkeypatch,
    *,
    verify_return: bool = False,
    rotate_status: str = 'rotated',
    rotate_new_key: str | None = 'sk-fresh-rotated',
    is_saas_store: bool = True,
) -> dict[str, AsyncMock]:
    """Install stub ``storage`` modules so the OSS test process can exercise
    the enterprise-only rotation path without a real SaaS backend.

    Returns the mock objects so tests can assert call counts.
    """
    verify_mock = AsyncMock(return_value=verify_return)
    rotate_mock = AsyncMock(
        return_value=types.SimpleNamespace(
            status=rotate_status,
            new_key=rotate_new_key,
            old_key='sk-old',
            openhands_type=True,
        )
    )

    lite_llm_mod = types.ModuleType('storage.lite_llm_manager')
    lite_llm_mod.LiteLlmManager = types.SimpleNamespace(verify_key=verify_mock)

    saas_mod = types.ModuleType('storage.saas_settings_store')

    class _ManagedLlmKeyStatus:
        ROTATED = 'rotated'
        NOT_MANAGED = 'not_managed'
        BYOK = 'byok'
        MISSING_MEMBER = 'missing_member'

    saas_mod.ManagedLlmKeyStatus = _ManagedLlmKeyStatus

    # ``managed_llm_key_config_from_model`` — returns truthy only for
    # openhands/* models (or base_url == managed proxy).
    def _config(model, base_url):
        if model and model.startswith('openhands/'):
            return types.SimpleNamespace(openhands_type=True)
        if base_url and 'all-hands.dev' in (base_url or '').lower():
            return types.SimpleNamespace(openhands_type=False)
        return None

    saas_mod.managed_llm_key_config_from_model = _config

    class _FakeSaasStore:
        rotate_managed_llm_key = rotate_mock

    saas_mod.SaasSettingsStore = _FakeSaasStore

    storage_mod = types.ModuleType('storage')

    monkeypatch.setitem(sys.modules, 'storage', storage_mod)
    monkeypatch.setitem(sys.modules, 'storage.lite_llm_manager', lite_llm_mod)
    monkeypatch.setitem(sys.modules, 'storage.saas_settings_store', saas_mod)

    return {'verify': verify_mock, 'rotate': rotate_mock}


def _base_settings() -> Settings:
    return Settings(
        agent_settings=OpenHandsAgentSettings(
            llm=LLM(
                model='openhands/gpt-5.5',
                base_url=_MANAGED_URL,
                api_key=SecretStr('sk-old-managed'),
            ),
        ),
    )


async def _seed(store: FileSettingsStore, settings: Settings) -> None:
    await store.store(settings)


# ---------------------------------------------------------------------------
# Unit tests: _maybe_rotate_stale_managed_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_refreshes_stale_managed_key(monkeypatch):
    """Invalid managed key → rotate → new key returned."""
    mocks = _install_managed_key_modules(
        monkeypatch, verify_return=False, rotate_new_key='sk-fresh'
    )
    from storage.saas_settings_store import SaasSettingsStore

    store = SaasSettingsStore()  # isinstance(SaasSettingsStore) → True
    llm = LLM(
        model='openhands/gpt-5.5',
        base_url=_MANAGED_URL,
        api_key=SecretStr('sk-old-managed'),
    )

    result = await _maybe_rotate_stale_managed_key(llm, store, 'user-1')

    assert result.api_key.get_secret_value() == 'sk-fresh'
    mocks['verify'].assert_awaited_once_with('sk-old-managed', 'user-1')
    mocks['rotate'].assert_awaited_once_with()


@pytest.mark.asyncio
async def test_rotate_skips_when_key_valid(monkeypatch):
    """Valid managed key → no rotation, original LLM returned."""
    mocks = _install_managed_key_modules(monkeypatch, verify_return=True)
    from storage.saas_settings_store import SaasSettingsStore

    store = SaasSettingsStore()
    llm = LLM(
        model='openhands/gpt-5.5',
        base_url=_MANAGED_URL,
        api_key=SecretStr('sk-good'),
    )

    result = await _maybe_rotate_stale_managed_key(llm, store, 'user-1')

    assert result is llm
    mocks['verify'].assert_awaited_once_with('sk-good', 'user-1')
    mocks['rotate'].assert_not_called()


@pytest.mark.asyncio
async def test_rotate_skips_non_managed_model(monkeypatch):
    """BYOK model (openai/...) → no verification, no rotation."""
    mocks = _install_managed_key_modules(monkeypatch, verify_return=False)
    from storage.saas_settings_store import SaasSettingsStore

    store = SaasSettingsStore()
    llm = LLM(
        model='openai/gpt-4o',
        base_url='https://api.openai.com/v1',
        api_key=SecretStr('sk-byok'),
    )

    result = await _maybe_rotate_stale_managed_key(llm, store, 'user-1')

    assert result is llm
    mocks['verify'].assert_not_called()
    mocks['rotate'].assert_not_called()


@pytest.mark.asyncio
async def test_rotate_skips_when_no_key(monkeypatch):
    """Keyless profile → skip."""
    mocks = _install_managed_key_modules(monkeypatch, verify_return=False)
    from storage.saas_settings_store import SaasSettingsStore

    store = SaasSettingsStore()
    llm = LLM(model='openhands/gpt-5.5', base_url=_MANAGED_URL)

    result = await _maybe_rotate_stale_managed_key(llm, store, 'user-1')

    assert result is llm
    mocks['verify'].assert_not_called()


@pytest.mark.asyncio
async def test_rotate_skips_masked_key(monkeypatch):
    """Masked key ('**********') → skip."""
    mocks = _install_managed_key_modules(monkeypatch, verify_return=False)
    from storage.saas_settings_store import SaasSettingsStore

    store = SaasSettingsStore()
    llm = LLM(
        model='openhands/gpt-5.5',
        base_url=_MANAGED_URL,
        api_key=SecretStr('**********'),
    )

    result = await _maybe_rotate_stale_managed_key(llm, store, 'user-1')

    assert result is llm
    mocks['verify'].assert_not_called()


@pytest.mark.asyncio
async def test_rotate_skips_none_user_id(monkeypatch):
    """No user_id → skip (can't call verify_key without a user)."""
    mocks = _install_managed_key_modules(monkeypatch, verify_return=False)
    from storage.saas_settings_store import SaasSettingsStore

    store = SaasSettingsStore()
    llm = LLM(
        model='openhands/gpt-5.5',
        base_url=_MANAGED_URL,
        api_key=SecretStr('sk-old'),
    )

    result = await _maybe_rotate_stale_managed_key(llm, store, None)

    assert result is llm
    mocks['verify'].assert_not_called()


@pytest.mark.asyncio
async def test_rotate_skips_non_saas_store(monkeypatch):
    """When enterprise modules aren't installed (OSS / FileSettingsStore),
    the ImportError guard returns the LLM unchanged."""
    # Don't install the fake modules → ImportError inside the helper.
    llm = LLM(
        model='openhands/gpt-5.5',
        base_url=_MANAGED_URL,
        api_key=SecretStr('sk-old'),
    )
    # A plain object that is NOT a SaasSettingsStore.
    store = types.SimpleNamespace()

    result = await _maybe_rotate_stale_managed_key(llm, store, 'user-1')

    assert result is llm


@pytest.mark.asyncio
async def test_rotate_handles_rotation_not_applied(monkeypatch):
    """When rotate returns NOT_MANAGED, the original LLM is returned."""
    mocks = _install_managed_key_modules(
        monkeypatch,
        verify_return=False,
        rotate_status='not_managed',
        rotate_new_key=None,
    )
    from storage.saas_settings_store import SaasSettingsStore

    store = SaasSettingsStore()
    llm = LLM(
        model='openhands/gpt-5.5',
        base_url=_MANAGED_URL,
        api_key=SecretStr('sk-dead'),
    )

    result = await _maybe_rotate_stale_managed_key(llm, store, 'user-1')

    assert result is llm
    mocks['rotate'].assert_awaited_once_with()


@pytest.mark.asyncio
async def test_rotate_swallows_verify_exception(monkeypatch):
    """If verify_key raises, the original LLM is returned (best-effort)."""
    mocks = _install_managed_key_modules(monkeypatch)
    mocks['verify'].side_effect = RuntimeError('network error')
    from storage.saas_settings_store import SaasSettingsStore

    store = SaasSettingsStore()
    llm = LLM(
        model='openhands/gpt-5.5',
        base_url=_MANAGED_URL,
        api_key=SecretStr('sk-old'),
    )

    result = await _maybe_rotate_stale_managed_key(llm, store, 'user-1')

    assert result is llm
    mocks['rotate'].assert_not_called()


# ---------------------------------------------------------------------------
# Integration: save_profile endpoint with rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_profile_rotates_stale_managed_key(
    monkeypatch, settings_store, tmp_path
):
    """Saving a profile with a dead managed key transparently rotates it."""
    gen = _make_saas_test_client(monkeypatch, settings_store)
    test_client, mocks, saas_store = next(gen)
    try:
        await _seed(saas_store, _base_settings())

        resp = test_client.post(
            '/api/v1/settings/profiles/managed',
            json={
                'llm': {
                    'model': 'openhands/gpt-5.5',
                    'base_url': _MANAGED_URL,
                    'api_key': 'sk-old-managed',
                }
            },
        )

        assert resp.status_code == 201
        mocks['verify'].assert_awaited_once_with('sk-old-managed', 'test-user')
        mocks['rotate'].assert_awaited_once_with()

        stored = await saas_store.load()
        saved = stored.llm_profiles.get('managed')
        assert saved is not None
        assert saved.api_key.get_secret_value() == 'sk-fresh'
    finally:
        try:
            gen.close()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_save_profile_skips_rotation_for_byok(monkeypatch, settings_store):
    """A BYOK profile (openai/...) must not trigger managed-key rotation."""
    gen = _make_saas_test_client(monkeypatch, settings_store)
    test_client, mocks, saas_store = next(gen)
    try:
        await _seed(saas_store, _base_settings())

        resp = test_client.post(
            '/api/v1/settings/profiles/byok',
            json={
                'llm': {
                    'model': 'openai/gpt-4o',
                    'api_key': 'sk-byok',
                }
            },
        )

        assert resp.status_code == 201
        mocks['verify'].assert_not_called()
        mocks['rotate'].assert_not_called()

        stored = await saas_store.load()
        saved = stored.llm_profiles.get('byok')
        assert saved.api_key.get_secret_value() == 'sk-byok'
    finally:
        try:
            gen.close()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_save_profile_no_rotation_when_key_valid(monkeypatch, settings_store):
    """A valid managed key is left untouched."""
    gen = _make_saas_test_client(monkeypatch, settings_store, verify_return=True)
    test_client, mocks, saas_store = next(gen)
    try:
        await _seed(saas_store, _base_settings())

        resp = test_client.post(
            '/api/v1/settings/profiles/valid',
            json={
                'llm': {
                    'model': 'openhands/gpt-5.5',
                    'base_url': _MANAGED_URL,
                    'api_key': 'sk-good',
                }
            },
        )

        assert resp.status_code == 201
        mocks['rotate'].assert_not_called()

        stored = await saas_store.load()
        saved = stored.llm_profiles.get('valid')
        assert saved.api_key.get_secret_value() == 'sk-good'
    finally:
        try:
            gen.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Integration: activate_profile endpoint with rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_profile_rotates_stale_managed_key(monkeypatch, settings_store):
    """Activating a profile with a dead managed key rotates transparently."""
    gen = _make_saas_test_client(monkeypatch, settings_store)
    test_client, mocks, saas_store = next(gen)
    try:
        settings = _base_settings()
        settings.llm_profiles.save(
            'managed',
            LLM(
                model='openhands/gpt-5.5',
                base_url=_MANAGED_URL,
                api_key=SecretStr('sk-old-managed'),
            ),
        )
        await _seed(saas_store, settings)

        resp = test_client.post('/api/v1/settings/profiles/managed/activate')

        assert resp.status_code == 200
        mocks['verify'].assert_awaited_once_with('sk-old-managed', 'test-user')
        mocks['rotate'].assert_awaited_once_with()

        stored = await saas_store.load()
        # Rotated key lands in active settings AND the saved profile.
        assert stored.agent_settings.llm.api_key.get_secret_value() == 'sk-fresh'
        assert (
            stored.llm_profiles.get('managed').api_key.get_secret_value() == 'sk-fresh'
        )
    finally:
        try:
            gen.close()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_activate_profile_skips_rotation_for_byok(monkeypatch, settings_store):
    """Activating a BYOK profile must not trigger managed-key rotation."""
    gen = _make_saas_test_client(monkeypatch, settings_store)
    test_client, mocks, saas_store = next(gen)
    try:
        settings = _base_settings()
        settings.llm_profiles.save(
            'byok',
            LLM(model='openai/gpt-4o', api_key=SecretStr('sk-byok')),
        )
        await _seed(saas_store, settings)

        resp = test_client.post('/api/v1/settings/profiles/byok/activate')

        assert resp.status_code == 200
        mocks['verify'].assert_not_called()
        mocks['rotate'].assert_not_called()
    finally:
        try:
            gen.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Integration: store_settings endpoint with rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_settings_rotates_stale_managed_key(monkeypatch, settings_store):
    """POST /api/v1/settings with a dead managed key rotates transparently."""
    gen = _make_saas_test_client(monkeypatch, settings_store)
    test_client, mocks, saas_store = next(gen)
    try:
        await _seed(saas_store, _base_settings())

        resp = test_client.post(
            '/api/v1/settings',
            json={
                'agent_settings_diff': {
                    'llm': {
                        'model': 'openhands/gpt-5.5',
                        'base_url': _MANAGED_URL,
                        'api_key': 'sk-old-managed',
                    }
                }
            },
        )

        assert resp.status_code == 200
        mocks['verify'].assert_awaited_once_with('sk-old-managed', 'test-user')
        mocks['rotate'].assert_awaited_once_with()

        stored = await saas_store.load()
        assert stored.agent_settings.llm.api_key.get_secret_value() == 'sk-fresh'
    finally:
        try:
            gen.close()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_store_settings_skips_rotation_for_byok(monkeypatch, settings_store):
    """POST /api/v1/settings with a BYOK model must not rotate."""
    gen = _make_saas_test_client(monkeypatch, settings_store)
    test_client, mocks, saas_store = next(gen)
    try:
        await _seed(saas_store, _base_settings())

        resp = test_client.post(
            '/api/v1/settings',
            json={
                'agent_settings_diff': {
                    'llm': {
                        'model': 'openai/gpt-4o',
                        'base_url': 'https://api.openai.com/v1',
                        'api_key': 'sk-byok',
                    }
                }
            },
        )

        assert resp.status_code == 200
        mocks['verify'].assert_not_called()
        mocks['rotate'].assert_not_called()
    finally:
        try:
            gen.close()
        except Exception:
            pass
