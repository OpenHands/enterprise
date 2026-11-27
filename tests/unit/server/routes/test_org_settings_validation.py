"""Org default settings that fail SDK validation return a 422, not a 500.

These tests call the real route, ``OrgService``, ``OrgStore`` and SDK
validation on the PostgreSQL test database. Only authentication and the
best-effort managed-key rotation (which calls LiteLLM) are overridden.
"""

import uuid
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from openhands.app_server.user_auth import get_user_id, get_user_settings_store
from server.routes.orgs import org_router
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User

MODEL = 'anthropic/claude-sonnet-4-5-20250929'


@pytest.fixture
def owner_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def org_id(session_maker, owner_id):
    org_id = uuid.uuid4()
    with session_maker() as session:
        owner = session.execute(select(Role).where(Role.name == 'owner')).scalar()
        if owner is None:
            owner = Role(name='owner', rank=10)
            session.add(owner)
            session.flush()
        session.add(Org(id=org_id, name=f'org-{org_id.hex[:8]}', org_version=5))
        session.add(User(id=owner_id, current_org_id=org_id))
        session.add(
            OrgMember(
                org_id=org_id,
                user_id=owner_id,
                role_id=owner.id,
                status='active',
                _llm_api_key='key',
            )
        )
        session.commit()
    return org_id


@pytest.fixture
def client(org_id, owner_id, async_session_maker, monkeypatch):
    # The unit-test suite skips the SDK context-window check by default.
    monkeypatch.delenv('ALLOW_SHORT_CONTEXT_WINDOWS', raising=False)

    app = FastAPI()
    app.include_router(org_router)
    app.dependency_overrides[get_user_id] = lambda: str(owner_id)
    app.dependency_overrides[get_user_settings_store] = lambda: None
    with (
        patch('storage.org_store.a_session_maker', async_session_maker),
        patch('storage.org_member_store.a_session_maker', async_session_maker),
        patch('storage.role_store.a_session_maker', async_session_maker),
        patch('storage.user_store.a_session_maker', async_session_maker),
    ):
        yield TestClient(app, raise_server_exceptions=False)


def _persisted_llm(session_maker, org_id) -> dict:
    with session_maker() as session:
        return (session.get(Org, org_id).agent_settings or {}).get('llm', {})


def _llm_diff(**llm) -> dict:
    return {'agent_settings_diff': {'llm': llm}}


def test_too_small_context_window_is_rejected_with_actionable_422(
    client, org_id, session_maker
):
    response = client.patch(
        f'/api/organizations/{org_id}/settings',
        json=_llm_diff(model=MODEL, max_input_tokens=8192),
    )

    assert response.status_code == 422, response.json()
    detail = response.json()['detail']
    assert detail['field'] == 'agent_settings_diff.llm.max_input_tokens'
    assert detail['context_window'] == 8192
    assert detail['min_required'] == 16384
    assert '16,384' in detail['message']
    assert _persisted_llm(session_maker, org_id) == {}

    response = client.patch(
        f'/api/organizations/{org_id}/settings',
        json=_llm_diff(model=MODEL, max_input_tokens=32768),
    )

    assert response.status_code == 200, response.json()
    persisted = _persisted_llm(session_maker, org_id)
    assert persisted['model'] == MODEL
    assert persisted['max_input_tokens'] == 32768


def test_invalid_llm_field_is_rejected_with_field_level_422(
    client, org_id, session_maker
):
    response = client.patch(
        f'/api/organizations/{org_id}/settings',
        json=_llm_diff(temperature='not-a-number'),
    )

    assert response.status_code == 422, response.json()
    detail = response.json()['detail']
    assert detail['field'].startswith('agent_settings_diff.')
    assert detail['field'].endswith('llm.temperature')
    assert detail['message'].startswith(detail['field'])
    assert _persisted_llm(session_maker, org_id) == {}


def test_org_update_route_rejects_invalid_settings_with_422(client, org_id):
    response = client.patch(
        f'/api/organizations/{org_id}',
        json=_llm_diff(model=MODEL, max_input_tokens=8192),
    )

    assert response.status_code == 422, response.json()
    assert response.json()['detail']['min_required'] == 16384
