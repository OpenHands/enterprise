"""Tests for org-level daily conversation quota resolution."""

import os
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
        SimpleNamespace(daily_conversation_limit=50, current_org_id=ORG_ID),
        SimpleNamespace(daily_conversation_limit=100),  # org
    ]
    service = DailyConversationQuotaService(session)
    assert await service.get_limit(USER_ID) == 50


@pytest.mark.asyncio
async def test_org_override_when_user_is_null():
    """When user override is NULL, org override applies."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None, current_org_id=ORG_ID),
        SimpleNamespace(daily_conversation_limit=100),  # org
    ]
    service = DailyConversationQuotaService(session)
    assert await service.get_limit(USER_ID) == 100


@pytest.mark.asyncio
async def test_org_exempt_means_unlimited():
    """When org limit is -1, the user is exempt (returns None = unlimited)."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None, current_org_id=ORG_ID),
        SimpleNamespace(daily_conversation_limit=-1),  # exempt
    ]
    service = DailyConversationQuotaService(session)
    assert await service.get_limit(USER_ID) is None


@pytest.mark.asyncio
async def test_falls_back_to_env_when_both_null():
    """When both user and org overrides are NULL, env default applies."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None, current_org_id=ORG_ID),
        SimpleNamespace(daily_conversation_limit=None),  # not set
    ]
    with patch.dict('os.environ', {'OH_DAILY_CONVERSATION_LIMIT': '20'}):
        service = DailyConversationQuotaService(session)
        assert await service.get_limit(USER_ID) == 20


@pytest.mark.asyncio
async def test_falls_back_to_none_when_unset():
    """When everything is unset, returns None (unlimited)."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None, current_org_id=ORG_ID),
        SimpleNamespace(daily_conversation_limit=None),
    ]
    with patch.dict('os.environ', {}, clear=True):
        service = DailyConversationQuotaService(session)
        assert await service.get_limit(USER_ID) is None


@pytest.mark.asyncio
async def test_user_null_org_exempt_takes_precedence_over_env():
    """Org exemption (-1) takes precedence over env default."""
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(daily_conversation_limit=None, current_org_id=ORG_ID),
        SimpleNamespace(daily_conversation_limit=-1),  # exempt
    ]
    with patch.dict('os.environ', {'OH_DAILY_CONVERSATION_LIMIT': '20'}):
        service = DailyConversationQuotaService(session)
        assert await service.get_limit(USER_ID) is None
