"""Tests for ``ServiceAccountService.create_service_account`` (real DB)."""

import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from openhands.app_server.settings.settings_models import Settings
from server.routes.org_models import OrgNotFoundError
from server.services.service_account_service import (
    ServiceAccountNameExistsError,
    ServiceAccountService,
)
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User


def _litellm_settings(api_key: str = 'sk-service-account') -> Settings:
    settings = Settings(language='en')
    settings.update({'agent_settings_diff': {'llm': {'api_key': api_key}}})
    return settings


@pytest.fixture
async def seeded(async_session_maker):
    """An org and the ``service_account`` role. Returns ``(org_id, role_id)``."""
    org_id = uuid.uuid4()
    async with async_session_maker() as session:
        role = Role(name='service_account', rank=2000)
        session.add_all([role, Org(id=org_id, name='Acme')])
        await session.commit()
        return org_id, role.id


@pytest.fixture
def stores(async_session_maker):
    """Point the stores at the test DB and mock LiteLLM."""
    with ExitStack() as stack:
        for module in (
            'storage.user_store',
            'storage.org_store',
            'storage.role_store',
        ):
            stack.enter_context(patch(f'{module}.a_session_maker', async_session_maker))
        create_default_settings = stack.enter_context(
            patch(
                'server.services.service_account_service.UserStore.create_default_settings',
                new_callable=AsyncMock,
                return_value=_litellm_settings(),
            )
        )
        delete_litellm_user = stack.enter_context(
            patch(
                'server.services.service_account_service.LiteLlmManager.delete_user',
                new_callable=AsyncMock,
            )
        )
        yield create_default_settings, delete_litellm_user


@pytest.mark.asyncio
async def test_create_service_account_has_no_personal_org(
    async_session_maker, seeded, stores
):
    """GIVEN an org WHEN a service account is created THEN it is one service user and one service_account membership, with no personal org."""
    org_id, role_id = seeded
    create_default_settings, _ = stores

    user = await ServiceAccountService.create_service_account(org_id, '  CI bot ')

    create_default_settings.assert_awaited_once_with(
        org_id=str(org_id), user_id=str(user.id), create_user=True
    )
    async with async_session_maker() as session:
        stored = await session.get(User, user.id)
        assert stored is not None
        assert stored.account_type == 'service'
        assert stored.display_name == 'CI bot'
        assert stored.email is None
        assert stored.password_hash is None
        assert stored.role_id is None
        assert stored.current_org_id == org_id
        assert await session.get(Org, user.id) is None
        members = (
            (await session.execute(select(OrgMember).filter_by(user_id=user.id)))
            .scalars()
            .all()
        )
        assert len(members) == 1
        assert members[0].org_id == org_id
        assert members[0].role_id == role_id
        assert members[0].status == 'active'
        assert members[0].llm_api_key.get_secret_value() == 'sk-service-account'


@pytest.mark.asyncio
async def test_create_service_account_refuses_duplicate_name_in_org(
    async_session_maker, seeded, stores
):
    """GIVEN a service account named "CI bot" WHEN another is created with the same name THEN it is refused and its LiteLLM user is removed."""
    org_id, _ = seeded
    _, delete_litellm_user = stores
    await ServiceAccountService.create_service_account(org_id, 'CI bot')

    with pytest.raises(ServiceAccountNameExistsError):
        await ServiceAccountService.create_service_account(org_id, 'ci BOT')

    async with async_session_maker() as session:
        count = len(
            (await session.execute(select(User).filter_by(account_type='service')))
            .scalars()
            .all()
        )
    assert count == 1
    delete_litellm_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_service_account_unknown_org(seeded, stores):
    """GIVEN no such org WHEN a service account is created THEN OrgNotFoundError, and LiteLLM is not called."""
    create_default_settings, _ = stores

    with pytest.raises(OrgNotFoundError):
        await ServiceAccountService.create_service_account(uuid.uuid4(), 'CI bot')

    create_default_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_service_account_refuses_blank_name(seeded, stores):
    """GIVEN a blank name WHEN a service account is created THEN ValueError, and LiteLLM is not called."""
    org_id, _ = seeded
    create_default_settings, _ = stores

    with pytest.raises(ValueError):
        await ServiceAccountService.create_service_account(org_id, '   ')

    create_default_settings.assert_not_awaited()
