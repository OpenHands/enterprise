"""DB-backed tests for ``UserStore.count_users_with_email``.

The email-match login path (``server.auth.provider_identity``) uses this as
its duplicate-email guard, since ``User.email`` has no unique constraint.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from storage.user_store import UserStore


@pytest.fixture
def patched_user_session(async_session_maker):
    """Patch ``a_session_maker`` in ``storage.user_store`` to the test DB."""
    with patch('storage.user_store.a_session_maker', async_session_maker) as p:
        yield p


@pytest.mark.asyncio
async def test_zero_when_no_user_has_the_email(patched_user_session, create_user):
    create_user(email='other@example.com')

    assert await UserStore.count_users_with_email('alice@example.com') == 0


@pytest.mark.asyncio
async def test_counts_every_user_with_the_email(patched_user_session, create_user):
    create_user(email='alice@example.com', email_verified=True)
    create_user(email='alice@example.com', email_verified=False)
    create_user(email='other@example.com')

    assert await UserStore.count_users_with_email('alice@example.com') == 2


@pytest.mark.asyncio
async def test_normalizes_the_queried_email(patched_user_session, create_user):
    create_user(email='alice@example.com')

    assert await UserStore.count_users_with_email('  Alice@Example.COM ') == 1


@pytest.mark.asyncio
async def test_empty_email_counts_zero(patched_user_session, create_user):
    create_user(email=None)

    assert await UserStore.count_users_with_email('') == 0
