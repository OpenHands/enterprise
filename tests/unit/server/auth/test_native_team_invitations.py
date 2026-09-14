"""Team seeding joins real native credentials, authorization, routes and PostgreSQL."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from server import constants
from server.auth.native_password import NativeAuthError
from server.auth.native_session import csrf_for_token
from server.auth.native_types import SessionFactory
from server.routes import auth_accounts, native_auth, orgs
from server.services import org_invitation_service
from server.services.native_account_service import add_membership, set_superadmin
from server.services.native_auth_service import NativeAuthService, NativeLogin
from storage import database, org_member_store, org_store
from storage.native_auth import AccountInvitation, PasswordCredential
from storage.org import Org
from storage.org_invitation import OrgInvitation
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User
from tests.unit.server.auth.native_test_types import (
    NativeRuntime,
    present,
)
from tests.unit.server.auth.test_native_runtime import ORIGIN, PASSWORD, native_app

pytest_plugins = ['tests.unit.server.auth.test_native_runtime']


@pytest.fixture
def team_app(
    native_runtime: NativeRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> FastAPI:
    service, _, _ = native_runtime
    for module in (auth_accounts, native_auth):
        monkeypatch.setattr(module, 'get_native_auth_service', lambda: service)
    for module in (database, org_store, org_member_store):
        monkeypatch.setattr(module, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(org_invitation_service, 'ENABLE_KEYCLOAK', False)
    monkeypatch.setattr(constants, 'OPENHANDS_LLM_PROVIDER_ROUTE', 'direct')
    monkeypatch.setattr(constants, 'OPENHANDS_DEFAULT_LLM_MODEL', 'openai/test')
    monkeypatch.setattr(
        constants, 'OPENHANDS_DEFAULT_LLM_BASE_URL', 'http://localhost:1'
    )
    monkeypatch.setattr(constants, 'OPENHANDS_DEFAULT_LLM_API_KEY', None)
    app = native_app()
    app.include_router(native_auth.native_auth_router)
    app.include_router(auth_accounts.auth_accounts_router)
    app.include_router(orgs.org_router)
    return app


async def role_id(sessions: SessionFactory, name: str) -> int:
    async with sessions() as session:
        return present(await session.scalar(select(Role.id).where(Role.name == name)))


async def enroll(service: NativeAuthService, admin_id: UUID, email: str) -> NativeLogin:
    invitation = await service.issue_invitation(admin_id, email)
    return present(
        await service.complete_enrollment(
            invitation['invite_url'].split('#token=', 1)[1], PASSWORD, client_ip='1'
        )
    )


async def team(sessions: SessionFactory) -> UUID:
    async with sessions() as session, session.begin():
        org = Org(id=uuid4(), name=f'Team {uuid4()}')
        session.add(org)
        return org.id


@pytest.mark.asyncio
async def test_bootstrap_admin_creates_team_and_seeds_first_owner_without_joining(
    native_runtime: NativeRuntime,
    team_app: FastAPI,
    async_session_maker: SessionFactory,
) -> None:
    service, login, _ = native_runtime
    admin_id = login.principal.account_id
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=team_app),
        base_url=ORIGIN,
        cookies={'openhands_session': login.token},
        headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf_for_token(login.token)},
    ) as client:
        created = await client.post(
            '/api/organizations',
            json={
                'name': 'Unseeded team',
                'contact_name': 'Native administrator',
                'contact_email': 'native@example.com',
            },
        )
        assert created.status_code == 201, created.text
        org_id = UUID(created.json()['id'])
        choices = await client.get('/api/admin/auth-organizations')
        assert choices.status_code == 200
        assert choices.json() == {
            'items': [{'id': str(org_id), 'name': 'Unseeded team'}],
            'total': 1,
        }
        memberships = await client.get('/api/organizations')
        assert org_id not in [UUID(org['id']) for org in memberships.json()['items']]
        owner_role = await role_id(async_session_maker, 'owner')
        issued = await client.post(
            '/api/admin/auth-invitations',
            json={
                'email': 'first-owner@example.com',
                'org_id': str(org_id),
                'org_role_id': owner_role,
            },
        )
        assert issued.status_code == 201, issued.text
    enrolled = await service.complete_enrollment(
        issued.json()['invite_url'].split('#token=', 1)[1], PASSWORD, client_ip='1'
    )
    async with async_session_maker() as session:
        assert await session.get(OrgMember, (org_id, admin_id)) is None
        member = await session.get(
            OrgMember, (org_id, present(enrolled).principal.account_id)
        )
        assert present(member).role_id == owner_role
        user = await session.get(User, present(enrolled).principal.account_id)
        assert present(user).role_id is None
        assert present(user).current_org_id == org_id


@pytest.mark.asyncio
async def test_choices_paginate_all_teams_and_exclude_every_personal_workspace(
    native_runtime: NativeRuntime,
    team_app: FastAPI,
    async_session_maker: SessionFactory,
) -> None:
    service, login, _ = native_runtime
    other = await enroll(service, login.principal.account_id, 'other@example.com')
    async with async_session_maker() as session, session.begin():
        session.add_all([Org(name=f'Team {index:03}') for index in range(105)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=team_app),
        base_url=ORIGIN,
        cookies={'openhands_session': login.token},
    ) as client:
        first = (await client.get('/api/admin/auth-organizations')).json()
        second = (await client.get('/api/admin/auth-organizations?offset=100')).json()
        assert first['total'] == second['total'] == 105
        assert len(first['items']) == 100 and len(second['items']) == 5
        items = first['items'] + second['items']
        assert len({org['id'] for org in items}) == 105
        assert [org['name'] for org in items] == [f'Team {i:03}' for i in range(105)]
        assert all(set(org) == {'id', 'name'} for org in items)
        assert str(login.principal.account_id) not in {org['id'] for org in items}
        assert str(other.principal.account_id) not in {org['id'] for org in items}
        assert (
            await client.get('/api/admin/auth-organizations?limit=201')
        ).status_code == 422


@pytest.mark.asyncio
async def test_regular_owner_cannot_list_admin_teams_or_issue_credentials(
    native_runtime: NativeRuntime,
    team_app: FastAPI,
    async_session_maker: SessionFactory,
) -> None:
    service, login, _ = native_runtime
    owner = await enroll(service, login.principal.account_id, 'owner@example.com')
    async with async_session_maker() as session, session.begin():
        (
            present(await session.get(User, owner.principal.account_id))
        ).accepted_tos = datetime.now()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=team_app),
        base_url=ORIGIN,
        cookies={'openhands_session': owner.token},
        headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf_for_token(owner.token)},
    ) as client:
        assert (await client.get('/api/admin/auth-organizations')).status_code == 403
        denied = await client.post(
            '/api/admin/auth-invitations',
            json={
                'email': 'new@example.com',
                'org_id': str(owner.principal.account_id),
                'org_role_id': await role_id(async_session_maker, 'member'),
            },
        )
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_global_fallback_does_not_authorize_another_personal_workspace(
    native_runtime: NativeRuntime,
    team_app: FastAPI,
    async_session_maker: SessionFactory,
) -> None:
    service, login, _ = native_runtime
    other = await enroll(service, login.principal.account_id, 'other@example.com')
    with pytest.raises(NativeAuthError, match='Not authorized'):
        await service.issue_invitation(
            login.principal.account_id,
            'new@example.com',
            other.principal.account_id,
            await role_id(async_session_maker, 'member'),
        )


@pytest.mark.asyncio
async def test_org_admin_cannot_grant_owner_even_with_global_role(
    native_runtime: NativeRuntime,
    team_app: FastAPI,
    async_session_maker: SessionFactory,
) -> None:
    service, login, _ = native_runtime
    org_id = await team(async_session_maker)
    async with async_session_maker() as session, session.begin():
        await add_membership(
            session,
            present(await session.get(User, login.principal.account_id)),
            present(await session.get(Org, org_id)),
            await role_id(async_session_maker, 'admin'),
        )
    with pytest.raises(NativeAuthError, match='Not authorized'):
        await service.issue_invitation(
            login.principal.account_id,
            'new@example.com',
            org_id,
            await role_id(async_session_maker, 'owner'),
        )
    await service.issue_invitation(
        login.principal.account_id,
        'new@example.com',
        org_id,
        await role_id(async_session_maker, 'member'),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('existing', [False, True])
async def test_global_revocation_revalidates_outstanding_team_invitation(
    native_runtime: NativeRuntime,
    team_app: FastAPI,
    async_session_maker: SessionFactory,
    existing: bool,
) -> None:
    service, login, _ = native_runtime
    admin_id = login.principal.account_id
    second = await enroll(service, admin_id, 'second-admin@example.com')
    async with async_session_maker() as session, session.begin():
        await set_superadmin(session, second.principal.account_id, True)
    recipient = (
        await enroll(service, admin_id, 'recipient@example.com') if existing else None
    )
    org_id = await team(async_session_maker)
    issued = await service.issue_invitation(
        admin_id,
        'recipient@example.com',
        org_id,
        await role_id(async_session_maker, 'owner'),
    )
    token = issued['invite_url'].split('#token=', 1)[1]
    async with async_session_maker() as session, session.begin():
        await set_superadmin(session, admin_id, False)
    with pytest.raises(NativeAuthError, match='Global user management'):
        if recipient:
            await service.accept_membership(recipient.principal.account_id, token)
        else:
            await service.complete_enrollment(token, PASSWORD, client_ip='1')
    with pytest.raises(NativeAuthError, match='Global user management'):
        await service.issue_invitation(
            admin_id,
            'next@example.com',
            org_id,
            await role_id(async_session_maker, 'member'),
        )
    with pytest.raises(NativeAuthError, match='Global user management'):
        await service.list_invitation_organizations(admin_id)
    async with async_session_maker() as session:
        invitation = await session.get(AccountInvitation, UUID(issued['invitation_id']))
        assert present(invitation).consumed_at is None
        assert (
            await session.scalar(select(OrgMember).where(OrgMember.org_id == org_id))
            is None
        )


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['account', 'organization'])
async def test_existing_account_membership_keeps_credentials_with_nonmember_inviter(
    native_runtime: NativeRuntime,
    team_app: FastAPI,
    async_session_maker: SessionFactory,
    kind: str,
) -> None:
    service, login, _ = native_runtime
    admin_id = login.principal.account_id
    person = await enroll(service, admin_id, 'existing@example.com')
    org_id = await team(async_session_maker)
    target_role = await role_id(async_session_maker, 'owner')
    async with async_session_maker() as session:
        old_hash = (
            present(await session.get(PasswordCredential, person.principal.account_id))
        ).password_hash
    if kind == 'account':
        issued = await service.issue_invitation(
            admin_id, 'existing@example.com', org_id, target_role
        )
        token = issued['invite_url'].split('#token=', 1)[1]
        assert (await service.inspect_invitation(token))['action'] == 'login'
        await service.accept_membership(person.principal.account_id, token)
    else:
        token = 'local-membership-test-token'
        async with async_session_maker() as session, session.begin():
            session.add(
                OrgInvitation(
                    token=token,
                    email='existing@example.com',
                    org_id=org_id,
                    role_id=target_role,
                    inviter_id=admin_id,
                    status='pending',
                    expires_at=datetime.now(UTC).replace(tzinfo=None)
                    + timedelta(days=1),
                )
            )
        await org_invitation_service.OrgInvitationService.accept_invitation(
            token, person.principal.account_id
        )
    async with async_session_maker() as session:
        assert await session.get(OrgMember, (org_id, admin_id)) is None
        assert (
            present(await session.get(OrgMember, (org_id, person.principal.account_id)))
        ).role_id == target_role
        assert (
            present(await session.get(PasswordCredential, person.principal.account_id))
        ).password_hash == old_hash
    assert (
        await service.login('existing@example.com', PASSWORD, client_ip='1')
    ).principal.account_id == person.principal.account_id
