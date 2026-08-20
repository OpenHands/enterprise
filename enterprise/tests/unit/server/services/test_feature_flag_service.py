"""Unit tests for FeatureFlagService evaluation logic."""

from unittest.mock import AsyncMock, patch

import pytest
from server.services.feature_flag_service import FeatureFlagService
from storage.feature_flag import FeatureFlag, FeatureFlagRule, FeatureFlagRuleEffect


def _make_flag(key: str, enabled: bool = True) -> FeatureFlag:
    return FeatureFlag(key=key, enabled=enabled, description=None)


def _make_rule(
    effect: FeatureFlagRuleEffect,
    *,
    user_id: str | None = None,
    org_id: str | None = None,
    email_pattern: str | None = None,
    percentage: float | None = None,
    priority: int = 0,
    rule_id: int = 1,
    flag_id: int = 1,
) -> FeatureFlagRule:
    return FeatureFlagRule(
        id=rule_id,
        flag_id=flag_id,
        effect=effect.value,
        user_id=user_id,
        org_id=org_id,
        email_pattern=email_pattern,
        percentage=percentage,
        priority=priority,
    )


@pytest.fixture
def service() -> FeatureFlagService:
    svc = FeatureFlagService(cache_ttl_seconds=0)
    return svc


def _patch_store(flag=None, rules=None):
    """Patch FeatureFlagStore.get_flag and list_rules with AsyncMocks."""
    return (
        patch(
            "server.services.feature_flag_service.FeatureFlagStore.get_flag",
            new_callable=AsyncMock,
            return_value=flag,
        ),
        patch(
            "server.services.feature_flag_service.FeatureFlagStore.list_rules",
            new_callable=AsyncMock,
            return_value=rules or [],
        ),
    )


class TestEvaluation:
    @pytest.mark.asyncio
    async def test_missing_flag_is_false(self, service):
        p1, p2 = _patch_store(flag=None, rules=[])
        with p1, p2:
            result = await service.is_enabled("missing")
            assert result is False

    @pytest.mark.asyncio
    async def test_globally_disabled_is_false(self, service):
        p1, p2 = _patch_store(flag=_make_flag("f", enabled=False), rules=[])
        with p1, p2:
            assert await service.is_enabled("f") is False

    @pytest.mark.asyncio
    async def test_enabled_no_rules_is_true(self, service):
        p1, p2 = _patch_store(flag=_make_flag("f", enabled=True), rules=[])
        with p1, p2:
            assert await service.is_enabled("f") is True

    @pytest.mark.asyncio
    async def test_exclude_rule_overrides_include(self, service):
        rules = [
            _make_rule(FeatureFlagRuleEffect.INCLUDE, user_id="u1", rule_id=1),
            _make_rule(
                FeatureFlagRuleEffect.EXCLUDE, user_id="u1", rule_id=2, priority=10
            ),
        ]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            assert await service.is_enabled("f", user_id="u1") is False

    @pytest.mark.asyncio
    async def test_exclude_by_email_pattern(self, service):
        rules = [
            _make_rule(FeatureFlagRuleEffect.EXCLUDE, email_pattern="%@blocked.com")
        ]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            assert await service.is_enabled("f", email="a@blocked.com") is False
            assert await service.is_enabled("f", email="a@ok.com") is True

    @pytest.mark.asyncio
    async def test_include_by_user_id(self, service):
        rules = [_make_rule(FeatureFlagRuleEffect.INCLUDE, user_id="u1")]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            assert await service.is_enabled("f", user_id="u1") is True
            # No matching include rule -> falls through to "has rules, none matched"
            assert await service.is_enabled("f", user_id="u2") is False

    @pytest.mark.asyncio
    async def test_include_by_org_id(self, service):
        rules = [_make_rule(FeatureFlagRuleEffect.INCLUDE, org_id="org1")]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            assert await service.is_enabled("f", org_id="org1") is True
            assert await service.is_enabled("f", org_id="org2") is False

    @pytest.mark.asyncio
    async def test_include_percentage_user_in_bucket(self, service):
        # percentage=100 means every user is in bucket
        rules = [_make_rule(FeatureFlagRuleEffect.INCLUDE, percentage=100.0)]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            assert await service.is_enabled("f", user_id="u1") is True

    @pytest.mark.asyncio
    async def test_include_percentage_zero_excludes_all(self, service):
        rules = [_make_rule(FeatureFlagRuleEffect.INCLUDE, percentage=0.0)]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            assert await service.is_enabled("f", user_id="u1") is False

    @pytest.mark.asyncio
    async def test_include_percentage_deterministic(self, service):
        # Same user+flag must give same result across calls
        rules = [_make_rule(FeatureFlagRuleEffect.INCLUDE, percentage=50.0)]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            r1 = await service.is_enabled("f", user_id="user-abc")
            r2 = await service.is_enabled("f", user_id="user-abc")
            assert r1 == r2

    @pytest.mark.asyncio
    async def test_include_percentage_no_user_skips_bucket(self, service):
        # Without a user_id, percentage rules still include (bucket skipped)
        rules = [_make_rule(FeatureFlagRuleEffect.INCLUDE, percentage=0.0)]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            assert await service.is_enabled("f") is True

    @pytest.mark.asyncio
    async def test_blanket_include_and_exclude(self, service):
        # Blanket exclude then specific include should still exclude the included
        # user because exclude is checked first and matches everything.
        rules = [
            _make_rule(FeatureFlagRuleEffect.INCLUDE, user_id="u1", rule_id=1),
            _make_rule(FeatureFlagRuleEffect.EXCLUDE, rule_id=2, priority=10),
        ]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            # Blanket exclude matches u1 -> False
            assert await service.is_enabled("f", user_id="u1") is False

    @pytest.mark.asyncio
    async def test_invalidate_clears_cache(self, service):
        flag = _make_flag("f", enabled=True)
        p1, p2 = _patch_store(flag=flag, rules=[])
        with p1 as mock_get, p2:
            assert await service.is_enabled("f") is True
            service.invalidate("f")
            # After invalidation, next call re-fetches; flag still True
            assert await service.is_enabled("f") is True
            assert mock_get.call_count >= 2
