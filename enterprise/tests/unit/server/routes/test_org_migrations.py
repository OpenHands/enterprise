from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.stored_custom_secrets import StoredCustomSecrets
from storage.user import User

from server.auth.org_context import resolve_effective_org_id
from server.routes.org_migrations import org_migration_router
from server.services import org_migration_service

from openhands.app_server.user_auth import get_user_id


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
            agent_settings_diff={},
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

        session.add_all([source_member, target_member, secret])
        session.commit()


@pytest.mark.asyncio
async def test_org_migration_route_dry_run(
    async_session_maker, session_maker, monkeypatch
):
    user_id = uuid.uuid4()
    target_org_id = uuid.uuid4()
    email = 'user@example.com'

    _patch_session_makers(monkeypatch, async_session_maker)
    _seed_user_data(
        session_maker, user_id=user_id, target_org_id=target_org_id, email=email
    )

    app = FastAPI()
    app.include_router(org_migration_router)

    app.dependency_overrides[get_user_id] = lambda: str(user_id)
    app.dependency_overrides[resolve_effective_org_id] = lambda: target_org_id

    role = MagicMock()
    role.name = 'admin'

    with (
        patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=role),
        ),
        patch(
            'server.auth.org_context.resolve_target_org_id_for_permission_check',
            AsyncMock(return_value=target_org_id),
        ),
    ):
        client = TestClient(app)
        response = client.post(
            '/api/organizations/migrations',
            json={
                'source': {'mode': 'personal'},
                'users': [email],
                'types': ['secrets'],
                'dry_run': True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload['missing_identifiers'] == []
    assert payload['results']
    assert payload['results'][0]['actions'] == ['Would migrated 1 secret(s).']

    async with async_session_maker() as session:
        result = await session.execute(
            select(StoredCustomSecrets).filter(
                StoredCustomSecrets.keycloak_user_id == str(user_id),
                StoredCustomSecrets.org_id == target_org_id,
            )
        )
        assert result.scalars().first() is None
