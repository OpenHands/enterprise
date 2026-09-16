"""Enrollment HTTP contracts and redacted credential responses."""

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from openhands.app_server.user_auth import get_user_id
from server.auth import auth_config
from server.auth.native_types import SessionFactory
from server.routes import auth_accounts, auth_invitations, native_enrollment
from server.services.native_account_admin_service import NativeAccountAdminService
from server.services.native_enrollment_service import NativeEnrollmentService
from storage.org_member import OrgMember
from storage.role import Role
from tests.unit.server.auth.enrollment_fixtures import (
    PASSWORD,
    NativeFixture,
    enroll,
    link_token,
    present,
)

pytest_plugins = ['tests.unit.server.auth.enrollment_fixtures']


async def test_enrollment_routes_preserve_contract_and_redact_invalid_inputs(
    native: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, admin_id = native

    def enrollment_service() -> NativeEnrollmentService:
        return service

    def account_service() -> NativeAccountAdminService:
        return NativeAccountAdminService(configured)

    async def actor() -> str:
        return str(admin_id)

    monkeypatch.setattr(
        native_enrollment, 'get_native_enrollment_service', enrollment_service
    )
    monkeypatch.setattr(
        auth_invitations, 'get_native_enrollment_service', enrollment_service
    )
    monkeypatch.setattr(
        auth_accounts, 'get_native_account_admin_service', account_service
    )
    app = FastAPI()
    app.include_router(native_enrollment.native_enrollment_router)
    app.include_router(auth_invitations.auth_invitations_router)
    app.include_router(auth_accounts.auth_accounts_router)
    app.dependency_overrides[auth_invitations.MANAGE_USERS] = actor
    app.dependency_overrides[auth_accounts.MANAGE_USERS] = actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url='https://native.example.test',
    ) as client:
        issued = await client.post(
            '/api/admin/auth-invitations', json={'email': 'route@example.com'}
        )
        assert issued.status_code == 201
        assert issued.headers['cache-control'] == 'no-store'
        token = issued.json()['invite_url'].split('#token=', 1)[1]
        inspected = await client.post(
            '/api/auth/enrollment/inspect', json={'token': token}
        )
        assert inspected.json()['action'] == 'set_password'
        client.cookies.set(
            'api_key', 'stale-browser-key', domain='native.example.test', path='/'
        )
        completed = await client.post(
            '/api/auth/enrollment/complete',
            json={'token': token, 'password': PASSWORD},
        )
        assert completed.json()['action'] == 'complete'
        assert 'openhands_session=' in completed.headers['set-cookie']
        assert 'api_key' not in list(client.cookies)
        listing = await client.get('/api/admin/auth-accounts')
        assert listing.status_code == 200 and listing.json()['total'] == 2
        invalid = await client.post(
            '/api/auth/enrollment/complete',
            json={'token': {'secret': 'never-reflect-this'}, 'password': PASSWORD},
        )
        assert invalid.status_code == 422 and 'never-reflect-this' not in invalid.text
        monkeypatch.setattr(auth_config, 'ENABLE_KEYCLOAK', True)
        assert (
            await client.post('/api/auth/enrollment/inspect', json={'token': token})
        ).status_code == 404


async def test_membership_acceptance_requires_the_invited_authenticated_account(
    native: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, admin_id = native
    invited = await enroll(service, admin_id)
    async with configured() as session:
        member_role = present(
            await session.scalar(select(Role.id).where(Role.name == 'member'))
        )
    link = await service.issue_invitation(
        admin_id,
        'person@example.com',
        admin_id,
        member_role,
    )
    actor_id = admin_id

    def enrollment_service() -> NativeEnrollmentService:
        return service

    async def actor() -> str:
        return str(actor_id)

    monkeypatch.setattr(
        native_enrollment, 'get_native_enrollment_service', enrollment_service
    )
    app = FastAPI()
    app.include_router(native_enrollment.native_enrollment_router)
    app.dependency_overrides[get_user_id] = actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url='https://native.example.test',
    ) as client:
        body = {'token': link_token(link)}
        denied = await client.post('/api/auth/enrollment/accept-membership', json=body)
        assert denied.status_code == 403
        actor_id = invited.principal.account_id
        accepted = await client.post(
            '/api/auth/enrollment/accept-membership', json=body
        )
        assert accepted.status_code == 204
    async with configured() as session:
        membership = present(await session.get(OrgMember, (admin_id, actor_id)))
        assert membership.role_id == member_role
