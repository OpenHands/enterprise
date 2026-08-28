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
    async def test_exclude_rule_does_not_match_anonymous_user(self, service):
        # A per-user exclude rule must not exclude an anonymous caller; the
        # flag falls back to its global enabled state.
        rules = [
            _make_rule(
                FeatureFlagRuleEffect.EXCLUDE, user_id="u1", rule_id=1, priority=10
            ),
        ]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            # No user_id -> the per-user exclude does not apply -> global on.
            assert await service.is_enabled("f") is True
            # The targeted user is still excluded.
            assert await service.is_enabled("f", user_id="u1") is False

    @pytest.mark.asyncio
    async def test_exclude_org_rule_does_not_match_anonymous(self, service):
        rules = [
            _make_rule(
                FeatureFlagRuleEffect.EXCLUDE, org_id="org1", rule_id=1, priority=10
            ),
        ]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            # Anonymous caller: org exclude does not apply -> global on.
            assert await service.is_enabled("f") is True
            # The targeted org is excluded.
            assert await service.is_enabled("f", org_id="org1") is False

    @pytest.mark.asyncio
    async def test_exclude_email_rule_does_not_match_anonymous(self, service):
        rules = [
            _make_rule(
                FeatureFlagRuleEffect.EXCLUDE,
                email_pattern="%@blocked.com",
                rule_id=1,
                priority=10,
            ),
        ]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            # No email -> the email-pattern exclude does not apply.
            assert await service.is_enabled("f") is True
            assert await service.is_enabled("f", email="a@blocked.com") is False

    @pytest.mark.asyncio
    async def test_include_rule_does_not_grant_anonymous(self, service):
        # A per-user include rule must not grant an anonymous caller; the flag
        # has include rules but none matched an anonymous context, so it's off.
        rules = [_make_rule(FeatureFlagRuleEffect.INCLUDE, user_id="u1")]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            assert await service.is_enabled("f") is False
            assert await service.is_enabled("f", user_id="u1") is True

    @pytest.mark.asyncio
    async def test_blanket_exclude_still_matches_anonymous(self, service):
        # A fully-blank exclude rule has no targeting dimensions, so it DOES
        # match an anonymous context (and excludes it).
        rules = [
            _make_rule(FeatureFlagRuleEffect.EXCLUDE, rule_id=1, priority=10),
        ]
        p1, p2 = _patch_store(flag=_make_flag("f"), rules=rules)
        with p1, p2:
            assert await service.is_enabled("f") is False

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


class TestGetGlobalFlags:
    """``get_global_flags`` returns only rule-less flags for anonymous paths."""

    @pytest.mark.asyncio
    async def test_returns_only_rule_less_flags(self, service):
        # Two global flags (one on, one off) and one targeted flag (has a rule).
        flags = [
            _make_flag("global_on", enabled=True),
            _make_flag("global_off", enabled=False),
            _make_flag("targeted", enabled=True),
        ]
        rules_by_key = {
            "global_on": [],
            "global_off": [],
            "targeted": [_make_rule(FeatureFlagRuleEffect.INCLUDE, user_id="u1")],
        }

        with (
            patch(
                "server.services.feature_flag_service.FeatureFlagStore.list_flags",
                new_callable=AsyncMock,
                return_value=flags,
            ),
            patch(
                "server.services.feature_flag_service.FeatureFlagStore.list_rules",
                new_callable=AsyncMock,
                side_effect=lambda key: rules_by_key[key],
            ),
        ):
            result = await service.get_global_flags()
        assert result == {"global_on": True, "global_off": False}

    @pytest.mark.asyncio
    async def test_excludes_targeted_flags(self, service):
        # A flag with an exclude-only rule is still targeted (has rules) and
        # must NOT appear in the global set.
        flags = [_make_flag("excluded_user", enabled=True)]
        rules_by_key = {
            "excluded_user": [
                _make_rule(FeatureFlagRuleEffect.EXCLUDE, user_id="u1")
            ],
        }
        with (
            patch(
                "server.services.feature_flag_service.FeatureFlagStore.list_flags",
                new_callable=AsyncMock,
                return_value=flags,
            ),
            patch(
                "server.services.feature_flag_service.FeatureFlagStore.list_rules",
                new_callable=AsyncMock,
                side_effect=lambda key: rules_by_key[key],
            ),
        ):
            result = await service.get_global_flags()
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_when_no_flags(self, service):
        with (
            patch(
                "server.services.feature_flag_service.FeatureFlagStore.list_flags",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "server.services.feature_flag_service.FeatureFlagStore.list_rules",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            result = await service.get_global_flags()
        assert result == {}

    @pytest.mark.asyncio
    async def test_invalidate_drops_global_cache(self, service):
        flags = [_make_flag("global_on", enabled=True)]
        rules_by_key = {"global_on": []}
        with (
            patch(
                "server.services.feature_flag_service.FeatureFlagStore.list_flags",
                new_callable=AsyncMock,
                return_value=flags,
            ) as mock_list_flags,
            patch(
                "server.services.feature_flag_service.FeatureFlagStore.list_rules",
                new_callable=AsyncMock,
                side_effect=lambda key: rules_by_key[key],
            ),
        ):
            assert await service.get_global_flags() == {"global_on": True}
            # Within TTL, the cached snapshot is reused.
            assert await service.get_global_flags() == {"global_on": True}
            assert mock_list_flags.call_count == 1
            service.invalidate("global_on")
            assert await service.get_global_flags() == {"global_on": True}
            assert mock_list_flags.call_count >= 2

    @pytest.mark.asyncio
    async def test_global_cache_ttl_defaults_to_60s_and_is_independent(self):
        """The global snapshot TTL defaults to 60s and is independent of the
        per-flag ``is_enabled`` cache TTL (which defaults to 5s)."""
        from server.services.feature_flag_service import (
            _DEFAULT_CACHE_TTL_SECONDS,
            _DEFAULT_GLOBAL_CACHE_TTL_SECONDS,
            FeatureFlagService,
        )

        assert _DEFAULT_CACHE_TTL_SECONDS == 5
        assert _DEFAULT_GLOBAL_CACHE_TTL_SECONDS == 60
        svc = FeatureFlagService()
        assert svc._cache_ttl == 5
        assert svc._global_cache_ttl == 60
        # The two TTLs can be configured independently.
        svc2 = FeatureFlagService(
            cache_ttl_seconds=1, global_cache_ttl_seconds=30
        )
        assert svc2._cache_ttl == 1
        assert svc2._global_cache_ttl == 30

    @pytest.mark.asyncio
    async def test_global_cache_respects_its_own_ttl(self):
        """A 0s global TTL means the snapshot is rebuilt on every call."""
        svc = FeatureFlagService(
            cache_ttl_seconds=5, global_cache_ttl_seconds=0
        )
        flags = [_make_flag("global_on", enabled=True)]
        rules_by_key = {"global_on": []}
        with (
            patch(
                "server.services.feature_flag_service.FeatureFlagStore.list_flags",
                new_callable=AsyncMock,
                return_value=flags,
            ) as mock_list_flags,
            patch(
                "server.services.feature_flag_service.FeatureFlagStore.list_rules",
                new_callable=AsyncMock,
                side_effect=lambda key: rules_by_key[key],
            ),
        ):
            assert await svc.get_global_flags() == {"global_on": True}
            assert await svc.get_global_flags() == {"global_on": True}
            # global_cache_ttl_seconds=0 -> no reuse -> rebuilt both times.
            assert mock_list_flags.call_count == 2
