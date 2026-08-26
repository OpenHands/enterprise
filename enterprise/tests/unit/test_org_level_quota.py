"""Tests for org-level daily conversation quota resolution."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from server.services.daily_conversation_quota_service import (
    DailyConversationQuotaService,
)

USER_ID = str(uuid4())
ORG_ID = uuid4()


@pytest.mark.asyncio
async def test_user_override_takes_precedence():
    """User-level override wins over org and env."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=50),  # user
        SimpleNamespace(daily_conversation_limit=100),  # org (never reached)
    ]
    service = DailyConversationQuotaService(session)
    assert await service.get_limit(USER_ID, ORG_ID) == 50
    # The org row is not even queried once the user override resolves.
    assert session.scalar.await_count == 1


@pytest.mark.asyncio
async def test_org_override_when_user_is_null():
    """When user override is NULL, org override applies."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None),  # user
        SimpleNamespace(daily_conversation_limit=100),  # org
    ]
    service = DailyConversationQuotaService(session)
    assert await service.get_limit(USER_ID, ORG_ID) == 100


@pytest.mark.asyncio
async def test_org_exempt_means_unlimited():
    """When org limit is -1, the user is exempt (returns None = unlimited)."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None),  # user
        SimpleNamespace(daily_conversation_limit=-1),  # exempt
    ]
    service = DailyConversationQuotaService(session)
    assert await service.get_limit(USER_ID, ORG_ID) is None


@pytest.mark.asyncio
async def test_user_exempt_means_unlimited():
    """-1 means exempt at the user level too, not a literal limit of -1."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=-1),  # user: exempt
        SimpleNamespace(daily_conversation_limit=5),  # org (never reached)
    ]
    with patch.dict('os.environ', {'OH_DAILY_CONVERSATION_LIMIT': '20'}):
        service = DailyConversationQuotaService(session)
        assert await service.get_limit(USER_ID, ORG_ID) is None


@pytest.mark.asyncio
async def test_falls_back_to_env_when_both_null():
    """When both user and org overrides are NULL, env default applies."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None),  # user
        SimpleNamespace(daily_conversation_limit=None),  # org: not set
    ]
    with patch.dict('os.environ', {'OH_DAILY_CONVERSATION_LIMIT': '20'}):
        service = DailyConversationQuotaService(session)
        assert await service.get_limit(USER_ID, ORG_ID) == 20


@pytest.mark.asyncio
async def test_falls_back_to_none_when_unset():
    """When everything is unset, returns None (unlimited)."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None),  # user
        SimpleNamespace(daily_conversation_limit=None),  # org
    ]
    with patch.dict('os.environ', {}, clear=True):
        service = DailyConversationQuotaService(session)
        assert await service.get_limit(USER_ID, ORG_ID) is None


@pytest.mark.asyncio
async def test_user_null_org_exempt_takes_precedence_over_env():
    """Org exemption (-1) takes precedence over env default."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None),  # user
        SimpleNamespace(daily_conversation_limit=-1),  # exempt
    ]
    with patch.dict('os.environ', {'OH_DAILY_CONVERSATION_LIMIT': '20'}):
        service = DailyConversationQuotaService(session)
        assert await service.get_limit(USER_ID, ORG_ID) is None


@pytest.mark.asyncio
async def test_missing_user_row_still_resolves_org_override():
    """A missing user row falls through to the org, not straight to env."""
    session = AsyncMock()
    session.scalar.side_effect = [
        None,  # user row absent
        SimpleNamespace(daily_conversation_limit=7),  # org
    ]
    with patch.dict('os.environ', {'OH_DAILY_CONVERSATION_LIMIT': '20'}):
        service = DailyConversationQuotaService(session)
        assert await service.get_limit(USER_ID, ORG_ID) == 7


@pytest.mark.asyncio
async def test_limit_is_resolved_for_the_org_passed_in():
    """The org queried is the caller-supplied effective org.

    Regression guard: resolution must not fall back to the user's
    ``current_org_id``, which is only their last-selected org and would
    apply the wrong org's quota whenever the request is scoped elsewhere
    via ``X-Org-Id`` or an org-bound API key.
    """
    other_org = uuid4()
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None, current_org_id=other_org),
        SimpleNamespace(daily_conversation_limit=42),
    ]
    service = DailyConversationQuotaService(session)
    assert await service.get_limit(USER_ID, ORG_ID) == 42

    org_query = session.scalar.await_args_list[1].args[0]
    compiled = str(org_query.compile(compile_kwargs={'literal_binds': True}))
    assert ORG_ID.hex in compiled
    assert other_org.hex not in compiled
