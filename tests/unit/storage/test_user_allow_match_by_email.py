"""DB-backed tests for the ``allow_match_by_email`` IDP swap-over seeding flow.

Covers the ``UserStore`` methods added in ALL-5978:
- ``get_user_by_email_opted_in`` (filters on the flag + email)
- ``count_opted_in_users_with_email`` (duplicate-email guard)
- ``clear_allow_match_by_email`` (self-clear after a successful link)
- ``bulk_set_allow_match_by_email`` (operator swap-over toggle + duplicate report)

These hit a real PostgreSQL database (via the shared fixtures) so the
``nullable=False, server_default='false'`` column and the ``group_by`` /
``having`` duplicate-detection query are exercised with real SQL.
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


class TestGetUserByEmailOptedIn:
    @pytest.mark.asyncio
    async def test_returns_opted_in_user(self, patched_user_session, create_user):
        user = create_user(email='alice@example.com', allow_match_by_email=True)
        got = await UserStore.get_user_by_email_opted_in('alice@example.com')
        assert got is not None
        assert got.id == user.id

    @pytest.mark.asyncio
    async def test_skips_user_without_flag(self, patched_user_session, create_user):
        create_user(email='bob@example.com', allow_match_by_email=False)
        assert await UserStore.get_user_by_email_opted_in('bob@example.com') is None

    @pytest.mark.asyncio
    async def test_email_normalized_lower_strip(
        self, patched_user_session, create_user
    ):
        user = create_user(email='carol@example.com', allow_match_by_email=True)
        got = await UserStore.get_user_by_email_opted_in('  CAROL@example.com  ')
        assert got is not None
        assert got.id == user.id

    @pytest.mark.asyncio
    async def test_empty_email_returns_none(self, patched_user_session):
        assert await UserStore.get_user_by_email_opted_in('') is None


class TestCountOptedInUsersWithEmail:
    @pytest.mark.asyncio
    async def test_zero_when_none(self, patched_user_session, create_user):
        create_user(email='dave@example.com', allow_match_by_email=False)
        assert await UserStore.count_opted_in_users_with_email('dave@example.com') == 0

    @pytest.mark.asyncio
    async def test_one(self, patched_user_session, create_user):
        create_user(email='erin@example.com', allow_match_by_email=True)
        assert await UserStore.count_opted_in_users_with_email('erin@example.com') == 1

    @pytest.mark.asyncio
    async def test_duplicate_count(self, patched_user_session, create_user):
        create_user(email='shared@example.com', allow_match_by_email=True)
        create_user(email='shared@example.com', allow_match_by_email=True)
        assert (
            await UserStore.count_opted_in_users_with_email('shared@example.com') == 2
        )


class TestClearAllowMatchByEmail:
    @pytest.mark.asyncio
    async def test_clears_flag(self, patched_user_session, create_user):
        user = create_user(allow_match_by_email=True)
        await UserStore.clear_allow_match_by_email(str(user.id))
        got = await UserStore.get_user_by_email_opted_in(user.email)
        # Flag cleared → no longer matches via the opted-in lookup.
        assert got is None

    @pytest.mark.asyncio
    async def test_missing_user_logs_and_no_raise(self, patched_user_session):
        import uuid

        # Should not raise.
        await UserStore.clear_allow_match_by_email(str(uuid.uuid4()))


class TestBulkSetAllowMatchByEmail:
    @pytest.mark.asyncio
    async def test_enables_all_and_reports_duplicates(
        self, patched_user_session, create_user
    ):
        create_user(email='uniq@example.com', allow_match_by_email=False)
        create_user(email='dup@example.com', allow_match_by_email=False)
        create_user(email='dup@example.com', allow_match_by_email=False)

        result = await UserStore.bulk_set_allow_match_by_email(True)
        assert result['updated'] >= 3
        assert 'dup@example.com' in result['duplicate_emails']
        assert 'uniq@example.com' not in result['duplicate_emails']

    @pytest.mark.asyncio
    async def test_disables_all(self, patched_user_session, create_user):
        user = create_user(email='flip@example.com', allow_match_by_email=True)
        result = await UserStore.bulk_set_allow_match_by_email(False)
        assert result['updated'] >= 1
        assert await UserStore.get_user_by_email_opted_in(user.email) is None
