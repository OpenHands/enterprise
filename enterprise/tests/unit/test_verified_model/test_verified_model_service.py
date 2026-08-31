"""Unit tests for VerifiedModelService."""

import uuid

import pytest
from server.verified_models.default_model_replacement import (
    _build_default_model_replacements,
    replace_model_values,
)
from server.verified_models.verified_model_service import (
    VerifiedModelService,
)
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User
from storage.user_settings import UserSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from storage.base import Base


@pytest.fixture
async def async_engine():
    """Create an async SQLite engine for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        echo=False,
    )

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture
async def async_session_maker(async_engine):
    """Create an async session maker for testing."""
    return async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def _seed_models(async_session_maker):
    """Seed the database with test models."""
    async with async_session_maker() as session:
        service = VerifiedModelService(session)
        await service.create_verified_model(
            model_name="claude-sonnet", provider="openhands"
        )
        await service.create_verified_model(
            model_name="claude-sonnet", provider="anthropic"
        )
        await service.create_verified_model(
            model_name="gpt-4o", provider="openhands", is_enabled=False
        )


class TestCreateVerifiedModel:
    async def test_create_model(self, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            model = await service.create_verified_model(
                model_name="test-model", provider="test-provider"
            )
            assert model.model_name == "test-model"
            assert model.provider == "test-provider"
            assert model.is_enabled is True
            assert model.id is not None

    async def test_create_duplicate_raises(self, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            await service.create_verified_model(
                model_name="test-model", provider="test"
            )
            with pytest.raises(ValueError, match="test/test-model already exists"):
                await service.create_verified_model(
                    model_name="test-model", provider="test"
                )

    async def test_same_name_different_provider_allowed(self, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            await service.create_verified_model(
                model_name="claude", provider="openhands"
            )
            model = await service.create_verified_model(
                model_name="claude", provider="anthropic"
            )
            assert model.provider == "anthropic"


class TestGetModel:
    async def test_get_model(self, _seed_models, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            model = await service.get_model("claude-sonnet", "openhands")
            assert model is not None
            assert model.provider == "openhands"

    async def test_get_model_not_found(self, _seed_models, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            assert await service.get_model("nonexistent", "openhands") is None

    async def test_get_model_wrong_provider(self, _seed_models, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            assert await service.get_model("claude-sonnet", "openai") is None


class TestSearchVerifiedModels:
    async def test_search_models_no_filters(self, _seed_models, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            result = await service.search_verified_models()
            assert len(result.items) == 2  # Only enabled models
            assert result.next_page_id is None

    async def test_search_models_enabled_only_true(
        self, _seed_models, async_session_maker
    ):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            result = await service.search_verified_models(enabled_only=True)
            assert len(result.items) == 2
            names = {m.model_name for m in result.items}
            assert "gpt-4o" not in names  # Disabled model not included

    async def test_search_models_enabled_only_false(
        self, _seed_models, async_session_maker
    ):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            result = await service.search_verified_models(enabled_only=False)
            assert len(result.items) == 3  # All models including disabled

    async def test_search_models_by_provider(self, _seed_models, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            result = await service.search_verified_models(provider="openhands")
            assert len(result.items) == 1
            assert result.items[0].model_name == "claude-sonnet"

    async def test_search_models_pagination(self, _seed_models, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            # Create more models for pagination testing
            await service.create_verified_model(model_name="model-1", provider="test")
            await service.create_verified_model(model_name="model-2", provider="test")
            await service.create_verified_model(model_name="model-3", provider="test")
            await service.create_verified_model(model_name="model-4", provider="test")

        # Total: 7 models (3 initial + 4 new)
        # First page
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            result = await service.search_verified_models(
                enabled_only=False, page_id="0", limit=3
            )
            assert len(result.items) == 3
            assert result.next_page_id == "3"  # 4 more items after position 2

        # Second page (page_id 3)
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            result = await service.search_verified_models(
                enabled_only=False, page_id="3", limit=3
            )
            assert len(result.items) == 3
            # There are 4 items total starting at offset 3 (positions 3,4,5,6), so next_page_id exists
            assert result.next_page_id == "6"

        # Third page (page_id 6) - last item
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            result = await service.search_verified_models(
                enabled_only=False, page_id="6", limit=3
            )
            assert len(result.items) == 1
            assert result.next_page_id is None  # No more items after position 6


class TestUpdateVerifiedModel:
    async def test_update_model(self, _seed_models, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            updated = await service.update_verified_model(
                model_name="claude-sonnet", provider="openhands", is_enabled=False
            )
            assert updated is not None
            assert updated.is_enabled is False

    async def test_update_not_found(self, _seed_models, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            assert (
                await service.update_verified_model(
                    model_name="nonexistent", provider="openhands", is_enabled=False
                )
                is None
            )

    async def test_update_no_change(self, _seed_models, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            updated = await service.update_verified_model(
                model_name="claude-sonnet", provider="openhands"
            )
            assert updated is not None
            assert updated.is_enabled is True


class TestDeleteVerifiedModel:
    async def test_delete_model(self, _seed_models, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            await service.delete_verified_model("claude-sonnet", "openhands")

        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            assert await service.get_model("claude-sonnet", "openhands") is None
            # Other provider's version should still exist
            assert await service.get_model("claude-sonnet", "anthropic") is not None

    async def test_delete_not_found(self, _seed_models, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            with pytest.raises(ValueError):
                assert await service.delete_verified_model("nonexistent", "openhands")


class TestFreeFlag:
    async def test_create_defaults_to_not_free(self, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            model = await service.create_verified_model(
                model_name="m", provider="openhands"
            )
            assert model.is_free is False

    async def test_create_free(self, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            model = await service.create_verified_model(
                model_name="m", provider="openhands", is_free=True
            )
            assert model.is_free is True

    async def test_update_free_flag(self, _seed_models, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            updated = await service.update_verified_model(
                model_name="claude-sonnet", provider="openhands", is_free=True
            )
            assert updated is not None
            assert updated.is_free is True
            # Unrelated flags are untouched.
            assert updated.is_enabled is True


class TestVerifiedFlag:
    async def test_create_defaults_to_verified(self, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            model = await service.create_verified_model(
                model_name="m", provider="openhands"
            )
            assert model.is_verified is True

    async def test_create_unverified(self, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            model = await service.create_verified_model(
                model_name="m", provider="openhands", is_verified=False
            )
            assert model.is_verified is False

    async def test_update_verified_flag(self, _seed_models, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            updated = await service.update_verified_model(
                model_name="claude-sonnet",
                provider="openhands",
                is_verified=False,
            )
            assert updated is not None
            assert updated.is_verified is False
            assert updated.is_enabled is True


class TestDefaultFlag:
    async def test_create_default_clears_previous(self, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            first = await service.create_verified_model(
                model_name="a", provider="openhands", is_default=True
            )
            second = await service.create_verified_model(
                model_name="b", provider="openhands", is_default=True
            )
            assert second.is_default is True
            refreshed_first = await service.get_model("a", "openhands")
            assert refreshed_first is not None
            assert refreshed_first.is_default is False

    async def test_default_is_per_provider(self, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            oh = await service.create_verified_model(
                model_name="a", provider="openhands", is_default=True
            )
            anthropic = await service.create_verified_model(
                model_name="a", provider="anthropic", is_default=True
            )
            # Setting a default for a different provider must not clear the
            # openhands default.
            assert anthropic.is_default is True
            refreshed_oh = await service.get_model("a", "openhands")
            assert refreshed_oh is not None
            assert refreshed_oh.is_default is True
            assert oh.provider == "openhands"

    async def test_update_default_clears_previous(self, async_session_maker):
        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            await service.create_verified_model(
                model_name="a", provider="openhands", is_default=True
            )
            await service.create_verified_model(model_name="b", provider="openhands")
            await service.update_verified_model(
                model_name="b", provider="openhands", is_default=True
            )
            a = await service.get_model("a", "openhands")
            b = await service.get_model("b", "openhands")
            assert a is not None and a.is_default is False
            assert b is not None and b.is_default is True


class TestDefaultModelReplacement:
    def test_replacement_set_is_exact_and_managed_only(self):
        replacements = _build_default_model_replacements("old-model", "new-model")

        assert replacements == {
            "openhands/old-model": "openhands/new-model",
            "litellm_proxy/old-model": "litellm_proxy/new-model",
        }
        assert "old-model" not in replacements
        assert "anthropic/old-model" not in replacements

    def test_replace_model_values_updates_nested_models_only(self):
        payload = {
            "llm": {"model": "openhands/old-model"},
            "profiles": {
                "Default": {"model": "litellm_proxy/old-model"},
                "Pinned": {"model": "anthropic/old-model"},
                "Bare": {"model": "old-model"},
            },
        }

        replaced, changed = replace_model_values(
            payload,
            _build_default_model_replacements("old-model", "new-model"),
        )

        assert changed is True
        assert replaced["llm"]["model"] == "openhands/new-model"
        assert replaced["profiles"]["Default"]["model"] == "litellm_proxy/new-model"
        assert replaced["profiles"]["Pinned"]["model"] == "anthropic/old-model"
        assert replaced["profiles"]["Bare"]["model"] == "old-model"
        assert payload["llm"]["model"] == "openhands/old-model"

    async def test_openhands_default_change_rewrites_saved_managed_models(
        self, async_session_maker
    ):
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        async with async_session_maker() as session:
            session.add(Role(id=1, name="admin", rank=1))
            session.add(
                Org(
                    id=org_id,
                    name="test-org",
                    agent_settings={
                        "llm": {"model": "openhands/old-model"},
                        "nested": [{"model": "anthropic/old-model"}],
                    },
                    llm_profiles={
                        "profiles": {
                            "Default": {"model": "openhands/old-model"},
                            "Pinned": {"model": "old-model"},
                        },
                        "active": "Default",
                    },
                )
            )
            session.add(
                User(
                    id=user_id,
                    current_org_id=org_id,
                    llm_profiles={
                        "profiles": {
                            "Default": {"model": "litellm_proxy/old-model"},
                        },
                        "active": "Default",
                    },
                )
            )
            session.add(
                OrgMember(
                    org_id=org_id,
                    user_id=user_id,
                    role_id=1,
                    _llm_api_key="placeholder",
                    agent_settings_diff={
                        "llm": {"model": "litellm_proxy/old-model"},
                    },
                )
            )
            session.add(
                UserSettings(
                    keycloak_user_id=str(user_id),
                    agent_settings={"llm": {"model": "openhands/old-model"}},
                )
            )
            service = VerifiedModelService(session)
            await service.create_verified_model(
                model_name="old-model", provider="openhands", is_default=True
            )
            await session.commit()

        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            await service.create_verified_model(
                model_name="new-model", provider="openhands", is_default=True
            )

        async with async_session_maker() as session:
            org = await session.get(Org, org_id)
            user = await session.get(User, user_id)
            member = await session.get(OrgMember, (org_id, user_id))
            user_settings = (
                (
                    await session.execute(
                        select(UserSettings).where(
                            UserSettings.keycloak_user_id == str(user_id)
                        )
                    )
                )
                .scalars()
                .one()
            )

            assert org is not None
            assert user is not None
            assert member is not None
            assert org.agent_settings["llm"]["model"] == "openhands/new-model"
            assert org.agent_settings["nested"][0]["model"] == "anthropic/old-model"
            assert org.llm_profiles["profiles"]["Default"]["model"] == (
                "openhands/new-model"
            )
            assert org.llm_profiles["profiles"]["Pinned"]["model"] == "old-model"
            assert user.llm_profiles["profiles"]["Default"]["model"] == (
                "litellm_proxy/new-model"
            )
            assert member.agent_settings_diff["llm"]["model"] == (
                "litellm_proxy/new-model"
            )
            assert user_settings.agent_settings["llm"]["model"] == (
                "openhands/new-model"
            )

    async def test_non_openhands_default_does_not_rewrite_settings(
        self, async_session_maker
    ):
        org_id = uuid.uuid4()
        async with async_session_maker() as session:
            session.add(
                Org(
                    id=org_id,
                    name="test-org",
                    agent_settings={"llm": {"model": "openhands/old-model"}},
                )
            )
            service = VerifiedModelService(session)
            await service.create_verified_model(
                model_name="old-model", provider="anthropic", is_default=True
            )
            await session.commit()

        async with async_session_maker() as session:
            service = VerifiedModelService(session)
            await service.create_verified_model(
                model_name="new-model", provider="anthropic", is_default=True
            )

        async with async_session_maker() as session:
            org = await session.get(Org, org_id)
            assert org is not None
            assert org.agent_settings["llm"]["model"] == "openhands/old-model"
