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
async def test_returns_new_key_even_if_old_key_deletion_fails():
    """A cleanup failure must not become a refresh failure.

    Deleting the stale key is best-effort: if ``delete_key`` raises (e.g. a
    transient LiteLLM hiccup), the freshly-minted key must still be returned so
    the sandbox 401->retry path succeeds instead of turning a *successful*
    rotation into a 500.
    """
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
        patch.object(
            LiteLlmManager,
            'delete_key',
            new=AsyncMock(side_effect=RuntimeError('boom')),
        ),
    ):
        result = await store.resolve_valid_managed_llm_key()

    assert result == 'new-key'


@pytest.mark.asyncio
async def test_verify_key_called_with_current_key_and_user_id():
    """The verify step must check *this* user's key against *this* user's scope.

    ``verify_key``'s arguments are part of the behaviour, so pin that the
    current key and the store's user id are the exact values passed.
    """
    store = _store()
    verify = AsyncMock(return_value=True)
    with (
        patch.object(
            SaasSettingsStore,
            'get_current_managed_llm_key',
            new=AsyncMock(return_value='current-key'),
        ),
        patch.object(LiteLlmManager, 'verify_key', new=verify),
    ):
        result = await store.resolve_valid_managed_llm_key()

    assert result == 'current-key'
    verify.assert_awaited_once_with('current-key', 'test-user')


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
