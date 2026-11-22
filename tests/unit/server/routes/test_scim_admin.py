"""SCIM admin API, over HTTP against a real database.

Requests go through the real ``scim_admin_router`` and ``require_permission``,
authenticated by real API keys behind ``SaasUserAuth.get_instance``. Roles,
memberships and providers are rows; only LiteLLM is switched off.
"""

from __future__ import annotations

import hashlib
import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from openhands.app_server.integrations.service_types import ProviderType
from server.auth.auth_error import AuthError
from server.auth.authorization import get_user_super_role
from server.auth.saas_user_auth import SaasUserAuth
from server.routes.scim import scim_router
from server.routes.scim_admin import scim_admin_router
from storage.api_key_store import ApiKeyStore
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import PasswordProviderDetails
from storage.org_member import OrgMember
from storage.role import Role
from storage.scim import ScimCredential


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(scim_admin_router)
    app.include_router(scim_router)

    @app.middleware('http')
    async def authenticate(request: Request, call_next):
        if request.url.path.startswith('/api'):
            try:
                request.state.user_auth = await SaasUserAuth.get_instance(request)
            except AuthError:
                return JSONResponse({'error': 'unauthenticated'}, status_code=401)
        return await call_next(request)

    return app


@pytest.fixture(autouse=True)
def super_roles_from_the_database():
    """Stack the real lookup over this directory's ``-> None`` default."""
    with patch('server.auth.authorization.get_user_super_role', get_user_super_role):
        yield


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url='http://scim') as c:
        yield c


@pytest.fixture
def roles(session_maker) -> dict[str, int]:
    with session_maker() as session:
        session.add_all(
            [
                Role(name='owner', rank=10),
                Role(name='admin', rank=20),
                Role(name='member', rank=1000),
                Role(name='service_account', rank=2000),
            ]
        )
        session.commit()
        return {role.name: role.id for role in session.scalars(select(Role))}


@pytest.fixture
async def provider(create_git_provider, app_db_session, async_session_maker, roles):
    """Factory for enabled login providers: corporate SSO unless told otherwise."""

    async def _create(
        provider_type: ProviderType = ProviderType.ENTERPRISE_SSO, **fields
    ) -> AuthProvider:
        row = await create_git_provider(provider_type, is_idp=True)
        async with async_session_maker() as session:
            row = await session.get(AuthProvider, row.id)
            row.enabled = True
            for name, value in fields.items():
                setattr(row, name, value)
            await session.commit()
            await session.refresh(row)
            return row

    with patch('storage.lite_llm_manager.ENABLE_LEGACY_LITELLM', False):
        yield _create


@pytest.fixture
def caller(create_org, create_user, session_maker, roles):
    """Factory for an account and the headers of an API key it holds.

    ``role`` is its role in ``org``; ``super_admin`` gives it the instance
    super role; ``key_org`` binds the key to an organization the account is
    also a member of. Every account
    owns a personal workspace whose id is ``user_id``.
    """

    async def _create(
        org=None, role=None, *, super_admin=False, key_org=None, user_id=None
    ):
        user_id = user_id or uuid.uuid4()
        create_org(id=user_id, name=f'user_{user_id}_org')
        create_user(
            id=user_id,
            current_org_id=user_id,
            email=f'{user_id.hex[:10]}@example.com',
            role_id=roles['admin'] if super_admin else None,
        )
        memberships = [(user_id, 'owner')]
        if org is not None and role is not None:
            memberships.append((org.id, role))
        if key_org is not None:
            memberships.append((key_org, 'member'))
        with session_maker() as session:
            for org_id, name in memberships:
                session.add(
                    OrgMember(
                        org_id=org_id,
                        user_id=user_id,
                        role_id=roles[name],
                        llm_api_key='synthetic-llm-key',
                        status='active',
                    )
                )
            session.commit()
        key = await ApiKeyStore.get_instance().create_api_key(
            user_id=str(user_id),
            name=f'key-{uuid.uuid4().hex[:8]}',
            org_id=key_org,
            use_current_org_fallback=False,
        )
        return {'Authorization': f'Bearer {key}'}

    return _create


def _url(org_id, suffix: str = '') -> str:
    return f'/api/organizations/{org_id}/scim{suffix}'


async def _create(client, org_id, headers, provider_id, name='Okta'):
    return await client.post(
        _url(org_id, '/credentials'),
        headers=headers,
        json={'name': name, 'auth_provider_id': provider_id},
    )


async def _scim_status(client, token: str) -> int:
    response = await client.get(
        '/scim/v2/Users', headers={'Authorization': f'Bearer {token}'}
    )
    return response.status_code


def _rows(session_maker, org_id) -> list[ScimCredential]:
    with session_maker() as session:
        return list(
            session.scalars(
                select(ScimCredential).where(ScimCredential.org_id == org_id)
            )
        )


async def test_setup_offers_only_what_the_caller_may_bind(
    client, create_org, caller, provider, async_session_maker
):
    org = create_org()
    okta = await provider(title='Okta')
    await provider(title='Switched off', enabled=False)
    await provider(ProviderType.GITHUB, title='GitHub login')
    async with async_session_maker() as session:
        session.add(
            AuthProvider(
                title='Password',
                is_idp=True,
                enabled=True,
                details=PasswordProviderDetails(),
            )
        )
        await session.commit()
    super_admin = await caller(super_admin=True)
    admin = await caller(org, 'admin')

    as_super = await client.get(_url(org.id), headers=super_admin)
    as_admin = await client.get(_url(org.id), headers=admin)

    assert as_super.status_code == 200, as_super.text
    assert as_super.json() == {
        'base_url': 'https://scim/scim/v2',
        'can_bind_provider': True,
        'providers': [
            {
                'id': okta.id,
                'title': 'Okta',
                'protocol': 'oidc',
                'bound': False,
                'allow_match_by_email': True,
            }
        ],
    }
    assert as_admin.status_code == 200, as_admin.text
    assert as_admin.json()['can_bind_provider'] is False
    assert as_admin.json()['providers'] == []


async def test_a_super_admin_binds_and_the_token_is_shown_once(
    client, create_org, caller, provider, session_maker
):
    org = create_org()
    okta = await provider(title='Okta')
    super_admin = await caller(super_admin=True)

    created = await _create(client, org.id, super_admin, okta.id, name='  Okta prod ')

    assert created.status_code == 201, created.text
    assert created.headers['cache-control'] == 'no-store'
    body = created.json()
    token = body.pop('token')
    assert token.startswith('ohscim_')
    assert body['name'] == 'Okta prod'
    assert body['auth_provider_title'] == 'Okta'
    assert body['revoked_at'] is None
    assert await _scim_status(client, token) == 200

    (row,) = _rows(session_maker, org.id)
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()

    listed = await client.get(_url(org.id, '/credentials'), headers=super_admin)
    assert listed.status_code == 200
    (item,) = listed.json()['items']
    assert set(item) == {
        'id',
        'name',
        'auth_provider_id',
        'auth_provider_title',
        'created_at',
        'last_used_at',
        'revoked_at',
    }
    assert item['id'] == body['id']
    assert item['last_used_at'] is not None
    assert token not in listed.text
    assert row.token_hash not in listed.text


async def test_an_org_admin_cannot_bind_a_provider(
    client, create_org, caller, provider, session_maker
):
    org = create_org()
    okta = await provider()
    owner = await caller(org, 'owner')

    for provider_id in (okta.id, okta.id + 1000):
        response = await _create(client, org.id, owner, provider_id)
        assert response.status_code == 403, response.text
        assert 'Super Admin' in response.json()['detail']

    assert _rows(session_maker, org.id) == []


async def test_an_org_admin_rotates_within_the_binding(
    client, create_org, caller, provider
):
    org = create_org()
    okta, entra = await provider(title='Okta'), await provider(title='Entra')
    super_admin = await caller(super_admin=True)
    admin = await caller(org, 'admin')
    old = (await _create(client, org.id, super_admin, okta.id, 'old')).json()

    setup = (await client.get(_url(org.id), headers=admin)).json()
    assert [(p['id'], p['bound']) for p in setup['providers']] == [(okta.id, True)]
    other = await _create(client, org.id, admin, entra.id)
    assert other.status_code == 403, other.text
    new = await _create(client, org.id, admin, okta.id, 'new')
    assert new.status_code == 201, new.text
    new = new.json()

    assert await _scim_status(client, old['token']) == 200
    assert await _scim_status(client, new['token']) == 200

    revoked = await client.delete(
        _url(org.id, f'/credentials/{old["id"]}'), headers=admin
    )
    assert revoked.status_code == 204, revoked.text
    assert await _scim_status(client, old['token']) == 401
    assert await _scim_status(client, new['token']) == 200

    items = (await client.get(_url(org.id, '/credentials'), headers=admin)).json()
    assert [(i['name'], i['revoked_at'] is None) for i in items['items']] == [
        ('new', True),
        ('old', False),
    ]


async def test_revoking_the_last_credential_ends_the_binding(
    client, create_org, caller, provider
):
    org = create_org()
    okta = await provider()
    super_admin = await caller(super_admin=True)
    admin = await caller(org, 'admin')
    only = (await _create(client, org.id, super_admin, okta.id)).json()
    path = _url(org.id, f'/credentials/{only["id"]}')

    first = await client.delete(path, headers=admin)
    listed = (await client.get(_url(org.id, '/credentials'), headers=admin)).json()
    again = await client.delete(path, headers=admin)
    relisted = (await client.get(_url(org.id, '/credentials'), headers=admin)).json()

    assert (first.status_code, again.status_code) == (204, 204)
    assert listed['items'][0]['revoked_at'] is not None
    assert relisted['items'][0]['revoked_at'] == listed['items'][0]['revoked_at']
    assert (await _create(client, org.id, admin, okta.id)).status_code == 403
    assert (await _create(client, org.id, super_admin, okta.id)).status_code == 201


async def test_members_outsiders_and_strangers_are_refused(
    client, create_org, caller, provider, session_maker
):
    org = create_org()
    okta = await provider()
    super_admin = await caller(super_admin=True)
    existing = (await _create(client, org.id, super_admin, okta.id)).json()
    credential_path = _url(org.id, f'/credentials/{existing["id"]}')
    create_body = {'name': 'x', 'auth_provider_id': okta.id}
    requests = [
        ('GET', _url(org.id), None),
        ('GET', _url(org.id, '/credentials'), None),
        ('POST', _url(org.id, '/credentials'), create_body),
        ('DELETE', credential_path, None),
    ]
    refused = {
        403: [
            await caller(org, 'member'),
            await caller(org, 'service_account'),
            await caller(create_org(), 'owner'),
            await caller(),
        ],
        401: [
            {},
            {'Authorization': 'Bearer not-a-key'},
            # A SCIM token is not a way to manage SCIM tokens.
            {'Authorization': f'Bearer {existing["token"]}'},
        ],
    }

    for status, callers in refused.items():
        for headers in callers:
            for method, path, body in requests:
                response = await client.request(
                    method, path, headers=headers, json=body
                )
                assert response.status_code == status, (method, path, response.text)
                assert existing['token'] not in response.text

    (row,) = _rows(session_maker, org.id)
    assert row.revoked_at is None


async def test_another_organizations_credential_is_out_of_reach(
    client, create_org, caller, provider, session_maker
):
    acme, globex = create_org(), create_org()
    okta = await provider()
    super_admin = await caller(super_admin=True)
    acme_admin = await caller(acme, 'admin')
    await _create(client, acme.id, super_admin, okta.id, 'acme')
    theirs = (await _create(client, globex.id, super_admin, okta.id, 'globex')).json()

    through_own_org = await client.delete(
        _url(acme.id, f'/credentials/{theirs["id"]}'), headers=acme_admin
    )
    listed = await client.get(_url(acme.id, '/credentials'), headers=acme_admin)

    assert through_own_org.status_code == 404, through_own_org.text
    assert [item['name'] for item in listed.json()['items']] == ['acme']
    (row,) = _rows(session_maker, globex.id)
    assert row.revoked_at is None
    assert await _scim_status(client, theirs['token']) == 200


async def test_a_key_or_header_for_another_org_does_not_reach_this_one(
    client, create_org, caller, provider
):
    acme, globex = create_org(), create_org()
    await provider()
    both = await caller(acme, 'admin', key_org=None)
    bound_elsewhere = await caller(acme, 'admin', key_org=globex.id)
    super_bound_elsewhere = await caller(super_admin=True, key_org=globex.id)

    assert (await client.get(_url(acme.id), headers=both)).status_code == 200
    for headers in (bound_elsewhere, super_bound_elsewhere):
        response = await client.get(_url(acme.id), headers=headers)
        assert response.status_code == 403, response.text
    mismatched = await client.get(
        _url(acme.id), headers={**both, 'X-Org-Id': str(globex.id)}
    )
    assert mismatched.status_code == 400, mismatched.text


async def test_a_personal_workspace_cannot_be_set_up(
    client, caller, provider, session_maker
):
    okta = await provider()
    user_id = uuid.uuid4()
    owner = await caller(user_id=user_id)
    super_admin = await caller(super_admin=True)

    for headers in (owner, super_admin):
        responses = [
            await client.get(_url(user_id), headers=headers),
            await client.get(_url(user_id, '/credentials'), headers=headers),
            await _create(client, user_id, headers, okta.id),
        ]
        assert [r.status_code for r in responses] == [400, 400, 400]

    assert _rows(session_maker, user_id) == []
    unknown = await client.get(_url(uuid.uuid4()), headers=super_admin)
    assert unknown.status_code == 404


async def test_only_an_enabled_corporate_sso_provider_can_be_bound(
    client, create_org, caller, provider, async_session_maker
):
    org = create_org()
    super_admin = await caller(super_admin=True)
    github = await provider(ProviderType.GITHUB)
    disabled = await provider(enabled=False)
    okta = await provider()

    for provider_id in (github.id, disabled.id, okta.id + 1000):
        response = await _create(client, org.id, super_admin, provider_id)
        assert response.status_code == 400, response.text

    token = (await _create(client, org.id, super_admin, okta.id)).json()['token']
    assert await _scim_status(client, token) == 200
    async with async_session_maker() as session:
        (await session.get(AuthProvider, okta.id)).is_idp = False
        await session.commit()
    assert await _scim_status(client, token) == 403


async def test_the_name_is_required_and_active_credentials_are_capped(
    client, create_org, caller, provider
):
    org = create_org()
    okta = await provider()
    super_admin = await caller(super_admin=True)

    for name in ('', '   ', 'x' * 256):
        response = await _create(client, org.id, super_admin, okta.id, name=name)
        assert response.status_code == 422, response.text

    with patch('storage.scim_store.MAX_ACTIVE_CREDENTIALS_PER_ORG', 2):
        first = await _create(client, org.id, super_admin, okta.id, 'one')
        assert first.status_code == 201
        assert (await _create(client, org.id, super_admin, okta.id)).status_code == 201
        full = await _create(client, org.id, super_admin, okta.id)
        assert full.status_code == 409, full.text
        await client.delete(
            _url(org.id, f'/credentials/{first.json()["id"]}'), headers=super_admin
        )
        assert (await _create(client, org.id, super_admin, okta.id)).status_code == 201
