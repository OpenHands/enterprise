"""Tests for SaaS DB-backed model discovery."""

from unittest.mock import AsyncMock, patch

import pytest
from server.verified_models.verified_model_router import SaaSLLMModelService
from server.verified_models.verified_model_service import VerifiedModelService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from storage.base import Base


@pytest.fixture
async def async_engine():
    engine = create_async_engine(
        'sqlite+aiosqlite:///:memory:',
        poolclass=StaticPool,
        connect_args={'check_same_thread': False},
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture
async def async_session_maker(async_engine):
    return async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


class TestSaaSLLMModelService:
    async def test_db_verified_flags_override_static_catalogue(
        self, async_session_maker
    ):
        async with async_session_maker() as session:
            verified_service = VerifiedModelService(session)
            await verified_service.create_verified_model(
                model_name='deepseek-v4-flash',
                provider='openhands',
                is_verified=False,
            )
            await verified_service.create_verified_model(
                model_name='db-added-model',
                provider='openhands',
                is_verified=True,
                is_default=True,
            )
            await verified_service.create_verified_model(
                model_name='db-openai-model',
                provider='openai',
                is_verified=True,
            )
            await verified_service.create_verified_model(
                model_name='disabled-db-model',
                provider='openai',
                is_enabled=False,
                is_verified=True,
            )

            service = SaaSLLMModelService(session)
            openhands_page = await service.search_llm_models(
                provider_eq='openhands', limit=10000
            )
            openai_page = await service.search_llm_models(
                provider_eq='openai', limit=10000
            )

        openhands_by_name = {m.name: m for m in openhands_page.items}
        openai_by_name = {m.name: m for m in openai_page.items}

        assert openhands_by_name['deepseek-v4-flash'].verified is False
        assert openhands_by_name['db-added-model'].verified is True
        assert openhands_by_name['db-added-model'].default is True
        assert service._cached_response is not None
        assert service._cached_response.verified_models == ['db-added-model']
        assert openai_by_name['db-openai-model'].verified is True
        assert 'disabled-db-model' not in openai_by_name

    async def test_db_free_and_default_flags_surface_in_model_search(
        self, async_session_maker
    ):
        async with async_session_maker() as session:
            verified_service = VerifiedModelService(session)
            with patch.object(
                verified_service,
                '_sync_litellm_free_model_allowlists',
                new=AsyncMock(),
            ):
                await verified_service.create_verified_model(
                    model_name='gpt-5.2',
                    provider='openhands',
                    is_free=True,
                    is_default=True,
                )

            service = SaaSLLMModelService(session)
            page = await service.search_llm_models(
                query='gpt-5.2', provider_eq='openhands', limit=100
            )

        models_by_name = {m.name: m for m in page.items}
        assert models_by_name['gpt-5.2'].free is True
        assert models_by_name['gpt-5.2'].default is True
        assert models_by_name['gpt-5.2'].verified is True
