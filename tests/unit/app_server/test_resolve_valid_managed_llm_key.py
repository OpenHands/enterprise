"""Tests for ``SaasSettingsStore.resolve_valid_managed_llm_key``.

This backs the sandbox-facing managed-key refresh endpoint (#5189): return the
current managed key when it still verifies, otherwise force-rotate and return
the replacement, and return ``None`` for non-managed / BYOK configs so the
caller surfaces the original 401 instead of masking it.
"""

from unittest.mock import AsyncMock, patch

import pytest

from storage.lite_llm_manager import LiteLlmManager
from storage.saas_settings_store import (
    ManagedLlmKeyRotation,
    ManagedLlmKeyStatus,
    SaasSettingsStore,
)


def _store() -> SaasSettingsStore:
    return SaasSettingsStore(user_id='test-user')


@pytest.mark.asyncio
async def test_returns_current_key_when_still_valid():
    """A verifying current key is returned as-is; no rotation, no deletion."""
    store = _store()
    with (
        patch.object(
            SaasSettingsStore,
            'get_current_managed_llm_key',
            new=AsyncMock(return_value='current-key'),
        ),
        patch.object(LiteLlmManager, 'verify_key', new=AsyncMock(return_value=True)),
        patch.object(
            SaasSettingsStore, 'rotate_managed_llm_key', new=AsyncMock()
        ) as rotate,
        patch.object(LiteLlmManager, 'delete_key', new=AsyncMock()) as delete,
    ):
        result = await store.resolve_valid_managed_llm_key()

    assert result == 'current-key'
    rotate.assert_not_awaited()
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotates_when_current_key_is_stale():
    """A stale current key triggers rotation; the new key is returned and the
    old one is cleaned up best-effort."""
    store = _store()
    rotation = ManagedLlmKeyRotation(
        status=ManagedLlmKeyStatus.ROTATED,
        old_key='old-key',
        new_key='new-key',
    )
    with (
        patch.object(
            SaasSettingsStore,
            'get_current_managed_llm_key',
            new=AsyncMock(return_value='old-key'),
        ),
        patch.object(LiteLlmManager, 'verify_key', new=AsyncMock(return_value=False)),
        patch.object(
            SaasSettingsStore,
            'rotate_managed_llm_key',
            new=AsyncMock(return_value=rotation),
        ),
        patch.object(LiteLlmManager, 'delete_key', new=AsyncMock()) as delete,
    ):
        result = await store.resolve_valid_managed_llm_key()

    assert result == 'new-key'
    delete.assert_awaited_once_with('old-key')


@pytest.mark.asyncio
async def test_returns_none_for_non_managed_config():
    """No current key and a non-managed rotation status yields ``None`` (the
    caller then surfaces the original auth error)."""
    store = _store()
    rotation = ManagedLlmKeyRotation(status=ManagedLlmKeyStatus.NOT_MANAGED)
    with (
        patch.object(
            SaasSettingsStore,
            'get_current_managed_llm_key',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            SaasSettingsStore,
            'rotate_managed_llm_key',
            new=AsyncMock(return_value=rotation),
        ),
        patch.object(LiteLlmManager, 'delete_key', new=AsyncMock()) as delete,
    ):
        result = await store.resolve_valid_managed_llm_key()

    assert result is None
    delete.assert_not_awaited()
