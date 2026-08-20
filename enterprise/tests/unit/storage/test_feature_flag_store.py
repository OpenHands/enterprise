"""Unit tests for FeatureFlagStore using SQLite in-memory database."""

from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from storage.base import Base
from storage.feature_flag import FeatureFlagRuleEffect
from storage.feature_flag_store import FeatureFlagStore


@pytest.fixture
async def async_engine():
    """Create an async SQLite engine for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    return engine


@pytest.fixture
async def async_session_maker(async_engine):
    """Create an async session maker bound to the async engine."""
    session_maker = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_maker


class TestCreateAndGetFlag:
    @pytest.mark.asyncio
    async def test_create_flag_defaults_disabled(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            flag = await FeatureFlagStore.create_flag(key="my_flag")
            assert flag.key == "my_flag"
            assert flag.enabled is False
            assert flag.description is None

    @pytest.mark.asyncio
    async def test_create_flag_with_description_and_enabled(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            flag = await FeatureFlagStore.create_flag(
                key="my_flag", description="A flag", enabled=True
            )
            assert flag.description == "A flag"
            assert flag.enabled is True

    @pytest.mark.asyncio
    async def test_create_duplicate_flag_raises(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            await FeatureFlagStore.create_flag(key="my_flag")
            with pytest.raises(ValueError, match="already exists"):
                await FeatureFlagStore.create_flag(key="my_flag")

    @pytest.mark.asyncio
    async def test_get_flag_returns_none_for_missing(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            flag = await FeatureFlagStore.get_flag("nope")
            assert flag is None

    @pytest.mark.asyncio
    async def test_get_flag_returns_flag(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            await FeatureFlagStore.create_flag(key="my_flag", enabled=True)
            flag = await FeatureFlagStore.get_flag("my_flag")
            assert flag is not None
            assert flag.key == "my_flag"
            assert flag.enabled is True


class TestListAndUpdateAndDeleteFlag:
    @pytest.mark.asyncio
    async def test_list_flags(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            await FeatureFlagStore.create_flag(key="a")
            await FeatureFlagStore.create_flag(key="b")
            flags = await FeatureFlagStore.list_flags()
            assert {f.key for f in flags} == {"a", "b"}

    @pytest.mark.asyncio
    async def test_update_flag_fields(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            await FeatureFlagStore.create_flag(key="my_flag")
            updated = await FeatureFlagStore.update_flag(
                key="my_flag", description="desc", enabled=True
            )
            assert updated is not None
            assert updated.description == "desc"
            assert updated.enabled is True

    @pytest.mark.asyncio
    async def test_update_flag_partial_only_provided(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            await FeatureFlagStore.create_flag(
                key="my_flag", description="keep", enabled=False
            )
            updated = await FeatureFlagStore.update_flag(key="my_flag", enabled=True)
            assert updated is not None
            assert updated.description == "keep"
            assert updated.enabled is True

    @pytest.mark.asyncio
    async def test_update_flag_missing_returns_none(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            assert await FeatureFlagStore.update_flag(key="nope", enabled=True) is None

    @pytest.mark.asyncio
    async def test_delete_flag(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            await FeatureFlagStore.create_flag(key="my_flag")
            assert await FeatureFlagStore.delete_flag("my_flag") is True
            assert await FeatureFlagStore.get_flag("my_flag") is None

    @pytest.mark.asyncio
    async def test_delete_flag_missing_returns_false(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            assert await FeatureFlagStore.delete_flag("nope") is False


class TestRules:
    @pytest.mark.asyncio
    async def test_create_rule_for_missing_flag_raises(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            with pytest.raises(ValueError, match="does not exist"):
                await FeatureFlagStore.create_rule(
                    flag_key="nope", effect=FeatureFlagRuleEffect.INCLUDE
                )

    @pytest.mark.asyncio
    async def test_list_rules_empty(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            await FeatureFlagStore.create_flag(key="my_flag")
            rules = await FeatureFlagStore.list_rules("my_flag")
            assert rules == []

    @pytest.mark.asyncio
    async def test_create_and_list_rules(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            await FeatureFlagStore.create_flag(key="my_flag")
            await FeatureFlagStore.create_rule(
                flag_key="my_flag",
                effect=FeatureFlagRuleEffect.INCLUDE,
                user_id="u1",
            )
            await FeatureFlagStore.create_rule(
                flag_key="my_flag",
                effect=FeatureFlagRuleEffect.EXCLUDE,
                email_pattern="%@blocked.com",
                priority=10,
            )
            rules = await FeatureFlagStore.list_rules("my_flag")
            assert len(rules) == 2
            # Higher priority first
            assert rules[0].effect == FeatureFlagRuleEffect.EXCLUDE.value
            assert rules[0].priority == 10
            assert rules[1].user_id == "u1"

    @pytest.mark.asyncio
    async def test_delete_rule(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            await FeatureFlagStore.create_flag(key="my_flag")
            rule = await FeatureFlagStore.create_rule(
                flag_key="my_flag", effect=FeatureFlagRuleEffect.INCLUDE
            )
            assert await FeatureFlagStore.delete_rule(rule.id) is True
            assert await FeatureFlagStore.list_rules("my_flag") == []

    @pytest.mark.asyncio
    async def test_delete_rule_missing_returns_false(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            assert await FeatureFlagStore.delete_rule(99999) is False

    @pytest.mark.asyncio
    async def test_delete_flag_cascades_rules(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            await FeatureFlagStore.create_flag(key="my_flag")
            await FeatureFlagStore.create_rule(
                flag_key="my_flag", effect=FeatureFlagRuleEffect.INCLUDE
            )
            await FeatureFlagStore.delete_flag("my_flag")
            await FeatureFlagStore.create_flag(key="my_flag")
            # Re-created flag should have no rules.
            assert await FeatureFlagStore.list_rules("my_flag") == []

    @pytest.mark.asyncio
    async def test_provided_session_works(self, async_session_maker):
        with patch("storage.feature_flag_store.a_session_maker", async_session_maker):
            async with async_session_maker() as session:
                flag = await FeatureFlagStore.create_flag(
                    key="my_flag", enabled=True, session=session
                )
                assert flag.id is not None
                fetched = await FeatureFlagStore.get_flag("my_flag", session=session)
                assert fetched is not None
                assert fetched.key == "my_flag"
