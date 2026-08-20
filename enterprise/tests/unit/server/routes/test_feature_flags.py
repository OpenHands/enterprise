"""Unit tests for the feature_flags admin REST routes.

These call the route handler functions directly with the store and service
patched, mirroring the handler-level style of ``test_api_keys.py``. The
``require_permission`` dependency is bypassed by patching it to a no-op
returner, so we focus on the handler logic rather than auth wiring (which is
covered by the super_admins routes integration).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from server.routes.feature_flags import (
    CreateFlagRequest,
    CreateRuleRequest,
    EvaluateRequest,
    UpdateFlagRequest,
    create_flag,
    create_rule,
    delete_flag,
    delete_rule,
    evaluate_flag,
    get_flag,
    list_flags,
    update_flag,
)
from storage.feature_flag import (
    FeatureFlag,
    FeatureFlagRule,
    FeatureFlagRuleEffect,
)


def _flag(key="f", enabled=True, description=None, fid=1):
    f = MagicMock(spec=FeatureFlag)
    f.key = key
    f.enabled = enabled
    f.description = description
    f.id = fid
    return f


def _rule(
    effect=FeatureFlagRuleEffect.INCLUDE,
    rid=1,
    user_id=None,
    org_id=None,
    email_pattern=None,
    percentage=None,
    priority=0,
    flag_id=1,
):
    r = MagicMock(spec=FeatureFlagRule)
    r.id = rid
    r.effect = effect.value
    r.user_id = user_id
    r.org_id = org_id
    r.email_pattern = email_pattern
    r.percentage = percentage
    r.priority = priority
    r.flag_id = flag_id
    return r


def _patch_store(flag=None, flags=None, rules=None):
    """Patch the store methods referenced by the routes."""
    return (
        patch(
            "server.routes.feature_flags.FeatureFlagStore.create_flag",
            new_callable=AsyncMock,
            side_effect=(
                lambda **k: _flag(
                    key=k["key"],
                    enabled=k.get("enabled", False),
                    description=k.get("description"),
                )
            ),
        ),
        patch(
            "server.routes.feature_flags.FeatureFlagStore.get_flag",
            new_callable=AsyncMock,
            return_value=flag,
        ),
        patch(
            "server.routes.feature_flags.FeatureFlagStore.list_flags",
            new_callable=AsyncMock,
            return_value=(flags or []),
        ),
        patch(
            "server.routes.feature_flags.FeatureFlagStore.list_rules",
            new_callable=AsyncMock,
            return_value=(rules or []),
        ),
        patch(
            "server.routes.feature_flags.FeatureFlagStore.update_flag",
            new_callable=AsyncMock,
            return_value=flag,
        ),
        patch(
            "server.routes.feature_flags.FeatureFlagStore.delete_flag",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "server.routes.feature_flags.FeatureFlagStore.create_rule",
            new_callable=AsyncMock,
            side_effect=ValueError("Flag with key does not exist") if False else None,
        ),
        patch(
            "server.routes.feature_flags.FeatureFlagStore.delete_rule",
            new_callable=AsyncMock,
            return_value=True,
        ),
    )


class TestListFlags:
    @pytest.mark.asyncio
    async def test_list_flags(self):
        flags = [_flag(key="a"), _flag(key="b", fid=2)]
        with (
            patch(
                "server.routes.feature_flags.FeatureFlagStore.list_flags",
                new_callable=AsyncMock,
                return_value=flags,
            ),
            patch(
                "server.routes.feature_flags.FeatureFlagStore.list_rules",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            result = await list_flags(_="u")
            assert {f.key for f in result} == {"a", "b"}

    @pytest.mark.asyncio
    async def test_get_flag_found(self):
        with (
            patch(
                "server.routes.feature_flags.FeatureFlagStore.get_flag",
                new_callable=AsyncMock,
                return_value=_flag(),
            ),
            patch(
                "server.routes.feature_flags.FeatureFlagStore.list_rules",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            result = await get_flag(key="f", _="u")
            assert result.key == "f"

    @pytest.mark.asyncio
    async def test_get_flag_missing_404(self):
        with patch(
            "server.routes.feature_flags.FeatureFlagStore.get_flag",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc:
                await get_flag(key="nope", _="u")
            assert exc.value.status_code == 404


class TestCreateFlag:
    @pytest.mark.asyncio
    async def test_create_flag(self):
        body = CreateFlagRequest(key="f", description="d", enabled=True)
        with (
            patch(
                "server.routes.feature_flags.FeatureFlagStore.create_flag",
                new_callable=AsyncMock,
            ),
            patch(
                "server.routes.feature_flags.FeatureFlagStore.get_flag",
                new_callable=AsyncMock,
                return_value=_flag(enabled=True),
            ),
            patch(
                "server.routes.feature_flags.FeatureFlagStore.list_rules",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("server.routes.feature_flags.feature_flag_service.invalidate") as inv,
        ):
            result = await create_flag(body=body, _="u")
            assert result.key == "f"
            inv.assert_called_once_with("f")

    @pytest.mark.asyncio
    async def test_create_flag_duplicate_409(self):
        body = CreateFlagRequest(key="f")
        with patch(
            "server.routes.feature_flags.FeatureFlagStore.create_flag",
            new_callable=AsyncMock,
            side_effect=ValueError("already exists"),
        ):
            with pytest.raises(HTTPException) as exc:
                await create_flag(body=body, _="u")
            assert exc.value.status_code == 409


class TestUpdateFlag:
    @pytest.mark.asyncio
    async def test_update_flag(self):
        body = UpdateFlagRequest(enabled=True)
        with (
            patch(
                "server.routes.feature_flags.FeatureFlagStore.update_flag",
                new_callable=AsyncMock,
                return_value=_flag(enabled=True),
            ),
            patch(
                "server.routes.feature_flags.FeatureFlagStore.get_flag",
                new_callable=AsyncMock,
                return_value=_flag(enabled=True),
            ),
            patch(
                "server.routes.feature_flags.FeatureFlagStore.list_rules",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("server.routes.feature_flags.feature_flag_service.invalidate") as inv,
        ):
            result = await update_flag(key="f", body=body, _="u")
            assert result.enabled is True
            inv.assert_called_once_with("f")

    @pytest.mark.asyncio
    async def test_update_flag_missing_404(self):
        body = UpdateFlagRequest(enabled=True)
        with patch(
            "server.routes.feature_flags.FeatureFlagStore.update_flag",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc:
                await update_flag(key="nope", body=body, _="u")
            assert exc.value.status_code == 404


class TestDeleteFlag:
    @pytest.mark.asyncio
    async def test_delete_flag(self):
        with (
            patch(
                "server.routes.feature_flags.FeatureFlagStore.delete_flag",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("server.routes.feature_flags.feature_flag_service.invalidate") as inv,
        ):
            await delete_flag(key="f", _="u")
            inv.assert_called_once_with("f")

    @pytest.mark.asyncio
    async def test_delete_flag_missing_404(self):
        with patch(
            "server.routes.feature_flags.FeatureFlagStore.delete_flag",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with pytest.raises(HTTPException) as exc:
                await delete_flag(key="nope", _="u")
            assert exc.value.status_code == 404


class TestRules:
    @pytest.mark.asyncio
    async def test_create_rule(self):
        body = CreateRuleRequest(effect=FeatureFlagRuleEffect.INCLUDE, user_id="u1")
        with (
            patch(
                "server.routes.feature_flags.FeatureFlagStore.create_rule",
                new_callable=AsyncMock,
            ),
            patch(
                "server.routes.feature_flags.FeatureFlagStore.get_flag",
                new_callable=AsyncMock,
                return_value=_flag(),
            ),
            patch(
                "server.routes.feature_flags.FeatureFlagStore.list_rules",
                new_callable=AsyncMock,
                return_value=[_rule()],
            ),
            patch("server.routes.feature_flags.feature_flag_service.invalidate") as inv,
        ):
            result = await create_rule(key="f", body=body, _="u")
            assert len(result.rules) == 1
            inv.assert_called_once_with("f")

    @pytest.mark.asyncio
    async def test_create_rule_flag_missing_404(self):
        body = CreateRuleRequest(effect=FeatureFlagRuleEffect.INCLUDE)
        with patch(
            "server.routes.feature_flags.FeatureFlagStore.create_rule",
            new_callable=AsyncMock,
            side_effect=ValueError("does not exist"),
        ):
            with pytest.raises(HTTPException) as exc:
                await create_rule(key="nope", body=body, _="u")
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_rule(self):
        with (
            patch(
                "server.routes.feature_flags.FeatureFlagStore.delete_rule",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "server.routes.feature_flags.FeatureFlagStore.get_flag",
                new_callable=AsyncMock,
                return_value=_flag(),
            ),
            patch(
                "server.routes.feature_flags.FeatureFlagStore.list_rules",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("server.routes.feature_flags.feature_flag_service.invalidate") as inv,
        ):
            result = await delete_rule(key="f", rule_id=1, _="u")
            assert result.rules == []
            inv.assert_called_once_with("f")

    @pytest.mark.asyncio
    async def test_delete_rule_missing_404(self):
        with patch(
            "server.routes.feature_flags.FeatureFlagStore.delete_rule",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with pytest.raises(HTTPException) as exc:
                await delete_rule(key="f", rule_id=999, _="u")
            assert exc.value.status_code == 404


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate_true(self):
        body = EvaluateRequest(user_id="u1")
        with patch(
            "server.routes.feature_flags.feature_flag_service.is_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await evaluate_flag(key="f", body=body, _="u")
            assert result.enabled is True

    @pytest.mark.asyncio
    async def test_evaluate_false(self):
        body = EvaluateRequest()
        with patch(
            "server.routes.feature_flags.feature_flag_service.is_enabled",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await evaluate_flag(key="f", body=body, _="u")
            assert result.enabled is False
