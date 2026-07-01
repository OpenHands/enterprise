from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from storage.api_key import ApiKey
from storage.api_key_store import ApiKeyStore
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.stored_custom_secrets import StoredCustomSecrets
from storage.user import User

from server.services import org_migration_service


def _patch_session_makers(monkeypatch, async_session_maker) -> None:
    from storage import (
        api_key_store,
        database,
        org_member_store,
        org_store,
        role_store,
        user_store,
    )

    monkeypatch.setattr(database, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(org_store, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(user_store, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(org_member_store, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(role_store, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(api_key_store, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(org_migration_service, 'a_session_maker', async_session_maker)


def _seed_user_data(session_maker, *, user_id, target_org_id, email):
    with session_maker() as session:
        role = Role(id=1, name='member', rank=1)
        personal_org = Org(id=user_id, name=f'personal-{user_id}')
        target_org = Org(id=target_org_id, name='target-org')
        user = User(id=user_id, current_org_id=user_id, email=email)
        session.add_all([role, personal_org, target_org, user])
        session.flush()

        source_member = OrgMember(
            org_id=user_id,
            user_id=user_id,
            role_id=role.id,
            llm_api_key='personal-llm-key',
            agent_settings_diff={'mcp_config': {'servers': []}},
            conversation_settings_diff={},
            status='active',
        )
        target_member = OrgMember(
            org_id=target_org_id,
            user_id=user_id,
            role_id=role.id,
            llm_api_key='target-llm-key',
            agent_settings_diff={},
            conversation_settings_diff={},
            status='active',
        )

        secret = StoredCustomSecrets(
            keycloak_user_id=str(user_id),
            org_id=user_id,
            secret_name='API_TOKEN',
            secret_value='secret-value',
        )

        user_key = ApiKey(
            key='sk-user-key',
            user_id=str(user_id),
            org_id=user_id,
            name='user-key',
        )
        mcp_key = ApiKey(
            key='sk-mcp-key',
            user_id=str(user_id),
            org_id=user_id,
            name='MCP_API_KEY',
        )
        automation_key = ApiKey(
            key='sk-automation-key',
            user_id=str(user_id),
            org_id=user_id,
            name=ApiKeyStore.make_system_key_name('OPENHANDS_API_KEY'),
        )

        session.add_all(
            [source_member, target_member, secret, user_key, mcp_key, automation_key]
        )
        session.commit()


@pytest.mark.asyncio
async def test_migrate_users_moves_secrets_keys_and_mcp(
    async_session_maker, session_maker, monkeypatch
):
    user_id = uuid.uuid4()
    target_org_id = uuid.uuid4()
    email = 'user@example.com'

    _patch_session_makers(monkeypatch, async_session_maker)
    _seed_user_data(
        session_maker, user_id=user_id, target_org_id=target_org_id, email=email
    )

    results = await org_migration_service.migrate_users(
        users=[await org_migration_service.UserStore.get_user_by_id(str(user_id))],
        source_mode='personal',
        source_org_id=None,
        target_org_id=target_org_id,
        types=set(org_migration_service.MIGRATION_TYPES),
        dry_run=False,
    )

    assert results
    assert results[0].errors == []

    async with async_session_maker() as session:
        secret_result = await session.execute(
            select(StoredCustomSecrets).filter(
                StoredCustomSecrets.keycloak_user_id == str(user_id),
                StoredCustomSecrets.org_id == target_org_id,
            )
        )
        assert secret_result.scalars().first() is not None

        api_keys_result = await session.execute(
            select(ApiKey).filter(
                ApiKey.user_id == str(user_id),
                ApiKey.org_id == target_org_id,
            )
        )
        key_names = {key.name for key in api_keys_result.scalars().all()}
        assert 'user-key' in key_names
        assert 'MCP_API_KEY' in key_names
        assert ApiKeyStore.make_system_key_name('OPENHANDS_API_KEY') in key_names

        source_member_result = await session.execute(
            select(OrgMember).filter(
                OrgMember.org_id == user_id,
                OrgMember.user_id == user_id,
            )
        )
        source_member = source_member_result.scalars().first()
        assert source_member is not None
        assert source_member.agent_settings_diff.get('mcp_config') is None

        target_member_result = await session.execute(
            select(OrgMember).filter(
                OrgMember.org_id == target_org_id,
                OrgMember.user_id == user_id,
            )
        )
        target_member = target_member_result.scalars().first()
        assert target_member is not None
        assert target_member.agent_settings_diff.get('mcp_config') == {'servers': []}
