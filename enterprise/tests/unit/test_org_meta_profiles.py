import uuid
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import select
from storage.org import Org
from storage.role import Role
from storage.user import User

from openhands.sdk.llm import LLM
from openhands.sdk.llm.meta_profile_store import MetaProfile
from storage.saas_settings_store import SaasSettingsStore

with patch('storage.database.a_session_maker'):
    from server.routes.org_meta_profiles import (
        _load_meta_profiles,
        activate_meta_profile,
        delete_meta_profile,
        get_meta_profile,
        list_meta_profiles,
        save_meta_profile,
    )

ORG_ID = uuid.UUID('7694c7b6-f959-4b81-92e9-b09c206f5081')
ADMIN_USER_ID = uuid.UUID('7694c7b6-f959-4b81-92e9-b09c206f5082')
CONFIG = MetaProfile(
    classifier_model='classifier',
    default_model='default',
    prompt_template='Route {{ instance_text }}',
)


@pytest.fixture
def seeded_org(session_maker):
    with session_maker() as session:
        session.add(Role(id=10, name='member', rank=3))
        session.add(
            Org(
                id=ORG_ID,
                name='model-router-test-org',
                org_version=1,
                enable_proactive_conversation_starters=True,
                llm_profiles={
                    'profiles': {
                        'classifier': LLM(model='openai/classifier').model_dump(
                            mode='json'
                        ),
                        'default': LLM(model='openai/default').model_dump(mode='json'),
                    },
                    'active': 'default',
                },
            )
        )
        session.add(
            User(
                id=ADMIN_USER_ID,
                current_org_id=ORG_ID,
                user_consents_to_analytics=True,
            )
        )
        session.commit()


@pytest.fixture
def patch_route_db(async_session_maker, seeded_org):
    async def _fake_get_org(org_id, user_id):  # noqa: ARG001
        async with async_session_maker() as session:
            result = await session.execute(select(Org).where(Org.id == org_id))
            return result.scalars().first()

    with (
        patch(
            'server.routes.org_meta_profiles.a_session_maker',
            async_session_maker,
        ),
        patch(
            'server.routes.org_meta_profiles.OrgService.get_org_by_id',
            side_effect=_fake_get_org,
        ),
    ):
        yield


async def _read_org(async_session_maker):
    async with async_session_maker() as session:
        result = await session.execute(select(Org).where(Org.id == ORG_ID))
        return result.scalars().one()


@pytest.mark.asyncio
async def test_org_meta_profile_lifecycle(async_session_maker, patch_route_db):
    await save_meta_profile(
        org_id=ORG_ID,
        name='pareto',
        body=CONFIG,
        user_id=str(ADMIN_USER_ID),
    )
    listing = await list_meta_profiles(ORG_ID, str(ADMIN_USER_ID))
    assert [item.name for item in listing.meta_profiles] == ['pareto']
    detail = await get_meta_profile(ORG_ID, 'pareto', str(ADMIN_USER_ID))
    assert detail.config == CONFIG

    await activate_meta_profile(ORG_ID, 'pareto', str(ADMIN_USER_ID))
    org = await _read_org(async_session_maker)
    assert _load_meta_profiles(org).active == 'pareto'
    assert org.agent_settings['active_meta_profile'] == 'pareto'
    assert org.agent_settings['meta_profile'] == CONFIG.model_dump(mode='json')
    assert org.agent_settings['enable_classify_and_switch_llm_tool'] is True

    await delete_meta_profile(ORG_ID, 'pareto', str(ADMIN_USER_ID))
    org = await _read_org(async_session_maker)
    assert _load_meta_profiles(org).profiles == {}
    assert org.agent_settings['active_meta_profile'] is None
    assert org.agent_settings['meta_profile'] is None
    assert org.agent_settings['enable_classify_and_switch_llm_tool'] is False


@pytest.mark.asyncio
async def test_activate_missing_returns_404(patch_route_db):
    with pytest.raises(HTTPException) as exc:
        await activate_meta_profile(ORG_ID, 'missing', str(ADMIN_USER_ID))
    assert exc.value.status_code == 404


def test_launch_hydrates_inline_llms_with_secrets() -> None:
    org = Org(
        name='hydrate-router-org',
        org_version=1,
        llm_profiles={
            'profiles': {
                'classifier': LLM(
                    model='openai/classifier',
                    base_url='https://provider.example/v1',
                    api_key=SecretStr('classifier-secret'),
                ).model_dump(mode='json', context={'expose_secrets': True}),
                'default': LLM(
                    model='openai/default',
                    base_url='https://provider.example/v1',
                    api_key=SecretStr('default-secret'),
                ).model_dump(mode='json', context={'expose_secrets': True}),
            },
            'active': 'default',
        },
    )
    settings = {
        'enable_classify_and_switch_llm_tool': True,
        'meta_profile': CONFIG.model_dump(mode='json'),
    }

    SaasSettingsStore._hydrate_model_router_llms(org, settings, None)

    assert settings['meta_profile_llms']['classifier']['api_key'] == (
        'classifier-secret'
    )
    assert settings['meta_profile_llms']['default']['api_key'] == 'default-secret'
