"""SCIM 2.0 users-only prototype, over HTTP against a real database.

Every request goes through the real ``scim_router``. Org access is observed
the way a client sees it: a probe route resolves the effective organization
with the production dependency, behind ``SaasUserAuth.get_instance``, using
real API keys. Only LiteLLM is switched off.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch
from uuid import UUID

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from openhands.app_server.integrations.service_types import ProviderType
from server.auth.auth_error import AuthError
from server.auth.org_access import OrgNotUsableError, assert_org_usable_for_product
from server.auth.org_context import resolve_effective_org_id
from server.auth.provider_identity import (
    add_user_to_mapped_orgs,
    resolve_or_create_user,
)
from server.auth.saas_user_auth import SaasUserAuth
from server.routes.scim import SCIM_MEDIA_TYPE, scim_router
from storage.api_key import ApiKey
from storage.api_key_store import ApiKeyStore
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import GroupOrgMapping
from storage.auth_provider_user import AuthProviderUser
from storage.default_org_service import DefaultOrgBootstrapService
from storage.org_member import OrgMember
from storage.org_member_store import OrgMemberStore
from storage.role import Role
from storage.scim import ScimUser
from storage.scim_store import ScimDeactivatedError, ScimStore
from storage.user import ACCOUNT_TYPE_SERVICE, User

ERROR_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:Error'
PATCH_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:PatchOp'


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(scim_router)

    @app.get('/probe/effective-org')
    async def effective_org(org_id: UUID = Depends(resolve_effective_org_id)):
        return {'org_id': str(org_id)}

    @app.middleware('http')
    async def authenticate(request: Request, call_next):
        if request.url.path.startswith('/probe'):
            try:
                request.state.user_auth = await SaasUserAuth.get_instance(request)
            except AuthError:
                return JSONResponse({'error': 'unauthenticated'}, status_code=401)
        return await call_next(request)

    return app


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
async def sso(create_git_provider, app_db_session, async_session_maker, roles):
    """Factory for enabled corporate SSO login providers, with LiteLLM off."""

    async def _create(allow_match_by_email: bool = True) -> AuthProvider:
        provider = await create_git_provider(ProviderType.ENTERPRISE_SSO, is_idp=True)
        async with async_session_maker() as session:
            row = await session.get(AuthProvider, provider.id)
            row.enabled = True
            row.allow_match_by_email = allow_match_by_email
            await session.commit()
            await session.refresh(row)
            return row

    with patch('storage.lite_llm_manager.ENABLE_LEGACY_LITELLM', False):
        yield _create


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url='http://scim') as c:
        yield c


class Directory:
    """One organization's SCIM credential, as its identity provider holds it."""

    def __init__(self, client, credential, provider, token, session_maker):
        self.client = client
        self.credential_id = credential.id
        self.org_id = credential.org_id
        self.provider = provider
        self.session_maker = session_maker
        self.headers = {'Authorization': f'Bearer {token}'}

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        return await self.client.request(
            method, f'/scim/v2{path}', headers=self.headers, **kwargs
        )

    async def create(self, email: str, **extra) -> httpx.Response:
        body = {
            'schemas': ['urn:ietf:params:scim:schemas:core:2.0:User'],
            'userName': email,
            'emails': [{'value': email, 'primary': True}],
            'active': True,
            **extra,
        }
        return await self.request('POST', '/Users', json=body)

    async def patch_active(self, scim_id: str, active) -> httpx.Response:
        body = {
            'schemas': [PATCH_SCHEMA],
            'Operations': [{'op': 'replace', 'value': {'active': active}}],
        }
        return await self.request('PATCH', f'/Users/{scim_id}', json=body)


@pytest.fixture
def directory(client, create_org, sso, session_maker):
    async def _create(provider: AuthProvider | None = None) -> Directory:
        org = create_org()
        provider = provider or await sso()
        credential, token = await ScimStore.mint_credential(org.id, provider.id)
        return Directory(client, credential, provider, token, session_maker)

    return _create


@pytest.fixture
def account(create_org, create_user, session_maker, roles):
    """Factory for an account as sign-up leaves one: on a personal workspace."""

    def _create(email: str, **fields) -> User:
        user_id = uuid.uuid4()
        create_org(id=user_id, name=f'user_{user_id}_org')
        user = create_user(id=user_id, current_org_id=user_id, email=email, **fields)
        _add_membership(session_maker, user_id, user_id, roles['owner'])
        return user

    return _create


def _add_membership(session_maker, org_id, user_id, role_id) -> None:
    with session_maker() as session:
        session.add(
            OrgMember(
                org_id=org_id,
                user_id=user_id,
                role_id=role_id,
                llm_api_key='synthetic-llm-key',
                status='active',
            )
        )
        session.commit()


def _membership(session_maker, org_id, user_id) -> OrgMember | None:
    with session_maker() as session:
        return session.get(OrgMember, (org_id, user_id))


def _record(session_maker, scim_id: str) -> ScimUser:
    with session_maker() as session:
        return session.get(ScimUser, UUID(scim_id))


def _account(session_maker, user_id) -> User:
    with session_maker() as session:
        return session.get(User, user_id)


def _accounts_with_email(session_maker, email: str) -> int:
    with session_maker() as session:
        return session.scalar(
            select(func.count()).select_from(User).where(User.email == email)
        )


async def _api_key(user_id, org_id=None) -> str:
    return await ApiKeyStore.get_instance().create_api_key(
        user_id=str(user_id),
        name=f'key-{uuid.uuid4().hex[:8]}',
        org_id=org_id,
        use_current_org_fallback=False,
    )


async def _effective_org(client, api_key: str, x_org_id=None) -> httpx.Response:
    headers = {'Authorization': f'Bearer {api_key}'}
    if x_org_id is not None:
        headers['X-Org-Id'] = str(x_org_id)
    return await client.get('/probe/effective-org', headers=headers)


def _assert_scim_error(response: httpx.Response, status: int, scim_type=None) -> None:
    assert response.status_code == status, response.text
    assert response.headers['content-type'].startswith(SCIM_MEDIA_TYPE)
    body = response.json()
    assert body['schemas'] == [ERROR_SCHEMA]
    assert body['status'] == str(status)
    assert body.get('scimType') == scim_type


async def test_create_provisions_a_member_without_credentials_or_consent(
    directory, session_maker, roles
):
    acme = await directory()

    response = await acme.create(
        'ada@example.com',
        externalId='ext-ada',
        name={'givenName': 'Ada', 'familyName': 'Lovelace'},
    )

    assert response.status_code == 201, response.text
    assert response.headers['content-type'].startswith(SCIM_MEDIA_TYPE)
    body = response.json()
    assert response.headers['location'] == body['meta']['location']
    assert body['meta']['location'].endswith(f'/scim/v2/Users/{body["id"]}')
    assert body['userName'] == 'ada@example.com'
    assert body['externalId'] == 'ext-ada'
    assert body['active'] is True
    assert set(body) == {
        'schemas', 'id', 'externalId', 'userName', 'name', 'emails', 'active', 'meta'
    }  # fmt: skip

    record = _record(session_maker, body['id'])
    assert record.org_id == acme.org_id
    assert str(record.user_id) != body['id']
    user = _account(session_maker, record.user_id)
    assert user.email == 'ada@example.com'
    assert user.email_verified is False
    assert user.accepted_tos is None
    assert user.password_hash is None
    assert user.role_id is None
    assert user.current_org_id == acme.org_id
    assert _membership(session_maker, acme.org_id, user.id).role_id == roles['member']
    with session_maker() as session:
        assert session.scalar(select(func.count()).select_from(ApiKey)) == 0
        assert session.scalar(select(func.count()).select_from(AuthProviderUser)) == 0


async def _provision(directory_, email: str) -> tuple[str, UUID]:
    response = await directory_.create(email)
    assert response.status_code == 201, response.text
    scim_id = response.json()['id']
    with directory_.session_maker() as session:
        return scim_id, session.get(ScimUser, UUID(scim_id)).user_id


@pytest.fixture
def offboarded(directory, create_org, session_maker, roles, client):
    """Someone in two organizations, deactivated by the directory of one."""

    async def _create():
        acme = await directory()
        scim_id, user_id = await _provision(acme, 'grace@example.com')
        other_org = create_org()
        _add_membership(session_maker, other_org.id, user_id, roles['member'])
        keys = {
            'acme': await _api_key(user_id, acme.org_id),
            'other': await _api_key(user_id, other_org.id),
            'unbound': await _api_key(user_id),
        }
        before = await _effective_org(client, keys['acme'])
        assert before.json() == {'org_id': str(acme.org_id)}

        response = await acme.patch_active(scim_id, False)
        assert response.status_code == 200, response.text
        assert response.json()['active'] is False
        return acme, scim_id, user_id, other_org.id, keys

    return _create


async def test_deactivation_denies_the_org_and_keeps_other_org_access(
    offboarded, client, session_maker, roles
):
    acme, scim_id, user_id, other_org_id, keys = await offboarded()

    # The key bound to the organization no longer authenticates at all.
    assert (await _effective_org(client, keys['acme'])).status_code == 401
    # An unbound key cannot be pointed at it, and no longer defaults to it.
    assert (
        await _effective_org(client, keys['unbound'], acme.org_id)
    ).status_code == 403
    default = await _effective_org(client, keys['unbound'])
    assert default.json() == {'org_id': str(user_id)}
    # The other organization's grant and key are untouched.
    other = await _effective_org(client, keys['other'])
    assert other.json() == {'org_id': str(other_org_id)}
    assert (
        await _effective_org(client, keys['unbound'], other_org_id)
    ).status_code == 200

    assert _membership(session_maker, acme.org_id, user_id) is None
    assert _membership(session_maker, other_org_id, user_id).role_id == roles['member']
    record = _record(session_maker, scim_id)
    assert record.active is False
    assert record.deactivated_at is not None
    assert record.user_id == user_id
    with session_maker() as session:
        bound = session.scalars(select(ApiKey.org_id).where(ApiKey.org_id.is_not(None)))
        assert set(bound) == {other_org_id}


async def test_nothing_but_the_directory_gives_the_access_back(
    offboarded, client, session_maker, roles, monkeypatch
):
    acme, _, user_id, _, _ = await offboarded()

    # A new sign-in asserting a group mapped to the organization.
    await add_user_to_mapped_orgs(
        [GroupOrgMapping(group='engineering', org_id=acme.org_id)],
        str(user_id),
        ['engineering'],
    )
    assert _membership(session_maker, acme.org_id, user_id) is None

    # The default-organization auto-add that runs at login.
    with session_maker() as session:
        org = session.get(OrgMember.org.property.mapper.class_, acme.org_id)
    with pytest.raises(ScimDeactivatedError):
        await DefaultOrgBootstrapService._ensure_membership(
            org=org, user=_account(session_maker, user_id), auto_add_users=True
        )
    assert _membership(session_maker, acme.org_id, user_id) is None

    # Any other grant: invitations and admin adds end in the same store call.
    with pytest.raises(ScimDeactivatedError):
        await OrgMemberStore.add_user_to_org(
            org_id=acme.org_id,
            user_id=user_id,
            role_id=roles['admin'],
            llm_api_key='synthetic-llm-key',
            status='active',
        )
    assert _membership(session_maker, acme.org_id, user_id) is None


async def test_marker_denies_the_org_even_if_a_membership_row_reappears(
    offboarded, client, session_maker, roles
):
    """The auth guard reads the marker, not the membership, with no feature flag."""
    acme, _, user_id, _, keys = await offboarded()
    _add_membership(session_maker, acme.org_id, user_id, roles['member'])
    fresh_key = await _api_key(user_id, acme.org_id)

    for response in (
        await _effective_org(client, fresh_key),
        await _effective_org(client, keys['unbound'], acme.org_id),
    ):
        assert response.status_code == 403
        assert 'deactivated' in response.json()['detail']


async def test_deactivation_is_honored_with_the_scim_routes_switched_off(
    offboarded, session_maker, roles
):
    """Turning ``SCIM_ENABLED`` off removes the routes, not the markers."""
    acme, _, user_id, other_org_id, _ = await offboarded()

    with (
        patch('server.constants.SCIM_ENABLED', False),
        patch('server.auth.org_access.ENABLE_SUPER_ADMIN', False),
    ):
        with pytest.raises(OrgNotUsableError, match='deactivated'):
            await assert_org_usable_for_product(acme.org_id, user_id=str(user_id))
        await assert_org_usable_for_product(other_org_id, user_id=str(user_id))
        with pytest.raises(ScimDeactivatedError):
            await OrgMemberStore.add_user_to_org(
                org_id=acme.org_id,
                user_id=user_id,
                role_id=roles['member'],
                llm_api_key='synthetic-llm-key',
                status='active',
            )

    assert _membership(session_maker, acme.org_id, user_id) is None


async def test_reactivation_restores_the_same_user_without_old_keys(
    offboarded, client, session_maker, roles
):
    acme, scim_id, user_id, other_org_id, keys = await offboarded()

    response = await acme.patch_active(scim_id, True)

    assert response.status_code == 200, response.text
    assert response.json()['id'] == scim_id
    assert response.json()['active'] is True
    record = _record(session_maker, scim_id)
    assert record.user_id == user_id
    assert record.deactivated_at is None
    assert _membership(session_maker, acme.org_id, user_id).role_id == roles['member']
    assert _accounts_with_email(session_maker, 'grace@example.com') == 1
    # The key deleted at deactivation stays dead; a new one works.
    assert (await _effective_org(client, keys['acme'])).status_code == 401
    new_key = await _api_key(user_id, acme.org_id)
    assert (await _effective_org(client, new_key)).json() == {
        'org_id': str(acme.org_id)
    }
    assert (await _effective_org(client, keys['other'])).json() == {
        'org_id': str(other_org_id)
    }


# ── linking an existing account ──────────────────────────────────────────


async def test_unique_trusted_email_links_the_existing_account(
    directory, account, session_maker, roles
):
    acme = await directory()
    existing = account('linus@example.com')

    response = await acme.create('linus@example.com')

    assert response.status_code == 201, response.text
    record = _record(session_maker, response.json()['id'])
    assert record.user_id == existing.id
    assert record.membership_origin == 'scim'
    assert _accounts_with_email(session_maker, 'linus@example.com') == 1
    assert (
        _membership(session_maker, acme.org_id, existing.id).role_id == roles['member']
    )
    # Their own workspace is still theirs.
    assert (
        _membership(session_maker, existing.id, existing.id).role_id == roles['owner']
    )


async def test_sign_in_lands_on_the_provisioned_account(directory, session_maker):
    acme = await directory()
    scim_id = (await acme.create('mary@example.com')).json()['id']

    signed_in = await resolve_or_create_user(
        acme.provider, {'sub': 'idp-subject-1', 'email': 'mary@example.com'}
    )

    assert signed_in == str(_record(session_maker, scim_id).user_id)
    assert _accounts_with_email(session_maker, 'mary@example.com') == 1


async def test_existing_email_is_refused_when_the_provider_does_not_match_by_email(
    directory, sso, account, session_maker
):
    acme = await directory(await sso(allow_match_by_email=False))
    existing = account('nina@example.com')

    _assert_scim_error(await acme.create('nina@example.com'), 409)
    # Nor is an account created that sign-in could never reach.
    _assert_scim_error(await acme.create('nobody@example.com'), 409)

    assert _membership(session_maker, acme.org_id, existing.id) is None
    assert _accounts_with_email(session_maker, 'nobody@example.com') == 0
    with session_maker() as session:
        assert session.scalar(select(func.count()).select_from(ScimUser)) == 0


async def test_an_email_shared_by_two_accounts_is_a_conflict(
    directory, account, session_maker
):
    acme = await directory()
    first, second = account('dup@example.com'), account('dup@example.com')

    _assert_scim_error(await acme.create('dup@example.com'), 409)

    assert _membership(session_maker, acme.org_id, first.id) is None
    assert _membership(session_maker, acme.org_id, second.id) is None


@pytest.mark.parametrize(
    'fields',
    [
        {'is_disabled': True},
        {'deletion_pending': True},
        {'account_type': ACCOUNT_TYPE_SERVICE},
        {'role_id': 'admin'},
    ],
    ids=['disabled', 'deletion-pending', 'service-account', 'instance-super-role'],
)
async def test_accounts_scim_must_not_manage_are_not_linked(
    directory, account, session_maker, roles, fields
):
    acme = await directory()
    if 'role_id' in fields:
        fields = {'role_id': roles[fields['role_id']]}
    protected = account('protected@example.com', **fields)

    _assert_scim_error(await acme.create('protected@example.com'), 409)

    assert _membership(session_maker, acme.org_id, protected.id) is None


# ── members SCIM did not create, and roles it does not manage ────────────


async def test_a_direct_plain_member_is_adopted_and_then_fully_managed(
    directory, account, session_maker, roles, client
):
    acme = await directory()
    existing = account('olga@example.com')
    _add_membership(session_maker, acme.org_id, existing.id, roles['member'])
    key = await _api_key(existing.id, acme.org_id)

    created = await acme.create('olga@example.com')

    assert created.status_code == 201, created.text
    scim_id = created.json()['id']
    assert _record(session_maker, scim_id).membership_origin == 'preexisting'
    assert (await _effective_org(client, key)).status_code == 200

    assert (await acme.patch_active(scim_id, False)).status_code == 200
    assert _membership(session_maker, acme.org_id, existing.id) is None
    assert (await _effective_org(client, key)).status_code == 401


@pytest.mark.parametrize('role', ['owner', 'admin', 'service_account'])
async def test_an_existing_privileged_member_is_not_adopted(
    directory, account, session_maker, roles, role
):
    acme = await directory()
    existing = account('pat@example.com')
    _add_membership(session_maker, acme.org_id, existing.id, roles[role])

    for active in (True, False):
        _assert_scim_error(await acme.create('pat@example.com', active=active), 409)

    assert _membership(session_maker, acme.org_id, existing.id).role_id == roles[role]
    with session_maker() as session:
        assert session.scalar(select(func.count()).select_from(ScimUser)) == 0


@pytest.mark.parametrize('role', ['owner', 'admin'])
@pytest.mark.parametrize('method', ['PATCH', 'PUT'])
async def test_a_member_promoted_after_provisioning_is_not_removed(
    directory, session_maker, roles, client, role, method
):
    acme = await directory()
    scim_id, user_id = await _provision(acme, 'quinn@example.com')
    key = await _api_key(user_id, acme.org_id)
    await OrgMemberStore.update_user_role_in_org(acme.org_id, user_id, roles[role])

    if method == 'PATCH':
        response = await acme.patch_active(scim_id, False)
    else:
        response = await acme.request(
            'PUT',
            f'/Users/{scim_id}',
            json={'userName': 'quinn@example.com', 'active': False},
        )

    _assert_scim_error(response, 409)
    record = _record(session_maker, scim_id)
    assert record.active is True
    assert record.deactivated_at is None
    assert _membership(session_maker, acme.org_id, user_id).role_id == roles[role]
    assert (await _effective_org(client, key)).json() == {'org_id': str(acme.org_id)}


async def test_reactivation_does_not_override_an_account_level_block(
    offboarded, session_maker
):
    acme, scim_id, user_id, _, _ = await offboarded()
    with session_maker() as session:
        session.get(User, user_id).is_disabled = True
        session.commit()

    _assert_scim_error(await acme.patch_active(scim_id, True), 409)

    assert _record(session_maker, scim_id).active is False
    assert _membership(session_maker, acme.org_id, user_id) is None


async def test_a_member_removed_by_hand_is_not_re_added_while_still_active(
    directory, session_maker
):
    acme = await directory()
    scim_id, user_id = await _provision(acme, 'rita@example.com')
    await OrgMemberStore.remove_user_from_org(acme.org_id, user_id)

    patched = await acme.patch_active(scim_id, True)
    replaced = await acme.request(
        'PUT', f'/Users/{scim_id}', json={'userName': 'rita@example.com'}
    )
    again = await acme.create('rita@example.com')

    assert patched.status_code == replaced.status_code == 200
    _assert_scim_error(again, 409, 'uniqueness')
    assert _membership(session_maker, acme.org_id, user_id) is None


# ── a record only ever speaks for its own account ────────────────────────


async def test_an_inactive_record_cannot_be_re_pointed_at_another_account(
    offboarded, account, session_maker, roles, client
):
    acme, scim_id, user_id, _, _ = await offboarded()
    owner = account('owner@example.com')
    _add_membership(session_maker, acme.org_id, owner.id, roles['owner'])
    owner_key = await _api_key(owner.id, acme.org_id)

    for active in (False, True):
        response = await acme.request(
            'PUT',
            f'/Users/{scim_id}',
            json={
                'userName': 'grace@example.com',
                'emails': [{'value': 'owner@example.com', 'primary': True}],
                'active': active,
            },
        )
        _assert_scim_error(response, 400, 'mutability')

    record = _record(session_maker, scim_id)
    assert (record.email, record.user_id) == ('grace@example.com', user_id)
    assert (await _effective_org(client, owner_key)).status_code == 200
    assert _membership(session_maker, acme.org_id, owner.id).role_id == roles['owner']


async def test_a_linked_inactive_record_never_matches_another_account_by_email(
    offboarded, account, session_maker, roles, client
):
    """Even with the stored email overwritten, the link decides whose marker it is."""
    acme, scim_id, _, _, _ = await offboarded()
    owner = account('owner@example.com')
    _add_membership(session_maker, acme.org_id, owner.id, roles['owner'])
    owner_key = await _api_key(owner.id, acme.org_id)
    newcomer = account('newcomer@example.com')
    with session_maker() as session:
        session.get(ScimUser, UUID(scim_id)).email = 'owner@example.com'
        session.commit()

    assert await ScimStore.is_deactivated(acme.org_id, owner.id) is False
    assert (await _effective_org(client, owner_key)).status_code == 200

    with session_maker() as session:
        session.get(ScimUser, UUID(scim_id)).email = 'newcomer@example.com'
        session.commit()
    await OrgMemberStore.add_user_to_org(
        org_id=acme.org_id,
        user_id=newcomer.id,
        role_id=roles['member'],
        llm_api_key='synthetic-llm-key',
        status='active',
    )
    assert _membership(session_maker, acme.org_id, newcomer.id) is not None


async def test_an_unlinked_inactive_record_blocks_whoever_holds_its_email(
    directory, session_maker
):
    acme = await directory()
    created = await acme.create('later@example.com', active=False)
    assert created.status_code == 201, created.text
    assert created.json()['active'] is False
    record = _record(session_maker, created.json()['id'])
    assert record.user_id is None
    assert record.deactivated_at is not None
    assert _accounts_with_email(session_maker, 'later@example.com') == 0

    user_id = await resolve_or_create_user(
        acme.provider, {'sub': 'idp-subject-2', 'email': 'later@example.com'}
    )
    await add_user_to_mapped_orgs(
        [GroupOrgMapping(group='engineering', org_id=acme.org_id)],
        user_id,
        ['engineering'],
    )

    assert _membership(session_maker, acme.org_id, UUID(user_id)) is None


async def test_a_record_detached_from_its_account_still_blocks_that_email(
    offboarded, account, session_maker, roles
):
    acme, scim_id, _, _, _ = await offboarded()
    with session_maker() as session:
        # What ON DELETE SET NULL leaves behind when the account is deleted.
        session.get(ScimUser, UUID(scim_id)).user_id = None
        session.execute(
            User.__table__.update()
            .where(User.email == 'grace@example.com')
            .values(email='gone@example.com')
        )
        session.commit()
    returned = account('grace@example.com')

    with pytest.raises(ScimDeactivatedError):
        await OrgMemberStore.add_user_to_org(
            org_id=acme.org_id,
            user_id=returned.id,
            role_id=roles['member'],
            llm_api_key='synthetic-llm-key',
            status='active',
        )


# ── PUT ──────────────────────────────────────────────────────────────────


async def test_put_replaces_the_profile_and_drives_the_lifecycle(
    directory, session_maker, roles, client
):
    acme = await directory()
    scim_id, user_id = await _provision(acme, 'sam@example.com')
    key = await _api_key(user_id, acme.org_id)
    body = {
        'userName': 'sam@example.com',
        'externalId': 'ext-sam',
        'name': {'givenName': 'Sam', 'familyName': 'Vimes'},
        'displayName': 'Sam Vimes',
        'emails': [{'value': 'Sam@Example.com', 'primary': True}],
    }

    updated = await acme.request('PUT', f'/Users/{scim_id}', json=body)
    assert updated.status_code == 200, updated.text
    assert updated.json()['id'] == scim_id
    assert updated.json()['displayName'] == 'Sam Vimes'
    assert updated.json()['name'] == {'givenName': 'Sam', 'familyName': 'Vimes'}
    assert updated.json()['active'] is True
    assert (await _effective_org(client, key)).status_code == 200

    off = await acme.request('PUT', f'/Users/{scim_id}', json={**body, 'active': False})
    assert off.status_code == 200, off.text
    assert off.json()['active'] is False
    assert _membership(session_maker, acme.org_id, user_id) is None
    assert (await _effective_org(client, key)).status_code == 401

    on = await acme.request('PUT', f'/Users/{scim_id}', json={**body, 'active': True})
    assert on.status_code == 200, on.text
    assert on.json()['active'] is True
    assert _record(session_maker, scim_id).user_id == user_id
    assert _membership(session_maker, acme.org_id, user_id).role_id == roles['member']
    assert (await _effective_org(client, key)).status_code == 401


async def test_a_rejected_put_deactivation_leaves_membership_and_keys_alone(
    directory, session_maker, roles, client
):
    acme = await directory()
    scim_id, user_id = await _provision(acme, 'tess@example.com')
    assert (await acme.create('ursula@example.com')).status_code == 201
    key = await _api_key(user_id, acme.org_id)

    response = await acme.request(
        'PUT',
        f'/Users/{scim_id}',
        json={
            'userName': 'ursula@example.com',
            'emails': [{'value': 'tess@example.com', 'primary': True}],
            'active': False,
        },
    )

    _assert_scim_error(response, 409, 'uniqueness')
    record = _record(session_maker, scim_id)
    assert (record.active, record.user_name) == (True, 'tess@example.com')
    assert record.deactivated_at is None
    assert _membership(session_maker, acme.org_id, user_id).role_id == roles['member']
    assert (await _effective_org(client, key)).json() == {'org_id': str(acme.org_id)}


# ── organization scope of a credential ───────────────────────────────────


async def test_another_organizations_credential_cannot_see_or_change_a_user(
    directory, session_maker, roles, client
):
    acme, globex = await directory(), await directory()
    scim_id, user_id = await _provision(acme, 'vera@example.com')
    key = await _api_key(user_id, acme.org_id)
    body = {'userName': 'vera@example.com', 'active': False}

    _assert_scim_error(await globex.request('GET', f'/Users/{scim_id}'), 404)
    _assert_scim_error(await globex.request('PUT', f'/Users/{scim_id}', json=body), 404)
    _assert_scim_error(await globex.patch_active(scim_id, False), 404)
    listed = await globex.request('GET', '/Users')
    assert listed.json()['totalResults'] == 0
    found = await globex.request(
        'GET', '/Users', params={'filter': 'userName eq "vera@example.com"'}
    )
    assert found.json()['Resources'] == []

    assert _record(session_maker, scim_id).active is True
    assert (await _effective_org(client, key)).json() == {'org_id': str(acme.org_id)}


async def test_the_same_person_in_two_directories_is_two_independent_records(
    directory, session_maker, roles, client
):
    acme, globex = await directory(), await directory()
    acme_id, user_id = await _provision(acme, 'walt@example.com')
    globex_id, same_user_id = await _provision(globex, 'walt@example.com')
    globex_key = await _api_key(user_id, globex.org_id)

    assert acme_id != globex_id
    assert same_user_id == user_id
    assert _accounts_with_email(session_maker, 'walt@example.com') == 1

    assert (await acme.patch_active(acme_id, False)).status_code == 200

    assert _membership(session_maker, acme.org_id, user_id) is None
    assert _record(session_maker, globex_id).active is True
    assert _membership(session_maker, globex.org_id, user_id).role_id == roles['member']
    assert (await _effective_org(client, globex_key)).json() == {
        'org_id': str(globex.org_id)
    }


# ── credentials ──────────────────────────────────────────────────────────


async def test_only_a_live_scim_token_authenticates(directory, account, client):
    acme = await directory()
    api_key = await _api_key(account('xena@example.com').id)

    for headers in (
        {},
        {'Authorization': 'Bearer ohscim_not-a-real-token'},
        {'Authorization': f'Bearer {api_key}'},
        {'Authorization': acme.headers['Authorization'].replace('Bearer', 'Basic')},
    ):
        response = await client.get('/scim/v2/Users', headers=headers)
        _assert_scim_error(response, 401)
        assert response.headers['www-authenticate'] == 'Bearer'

    assert (await acme.request('GET', '/Users')).status_code == 200
    await ScimStore.revoke_credential(acme.credential_id)
    _assert_scim_error(await acme.request('GET', '/Users'), 401)


async def test_a_scim_token_is_not_an_api_key(directory, client):
    acme = await directory()

    response = await client.get('/probe/effective-org', headers=acme.headers)

    assert response.status_code == 401


async def test_a_credential_needs_an_enabled_corporate_sso_provider(
    directory, async_session_maker
):
    acme = await directory()
    async with async_session_maker() as session:
        (await session.get(AuthProvider, acme.provider.id)).enabled = False
        await session.commit()

    _assert_scim_error(await acme.request('GET', '/Users'), 403)
    _assert_scim_error(await acme.create('yan@example.com'), 403)


async def test_a_personal_workspace_cannot_have_a_directory(account, sso, client):
    user = account('zoe@example.com')
    _, token = await ScimStore.mint_credential(user.id, (await sso()).id)

    response = await client.get(
        '/scim/v2/Users', headers={'Authorization': f'Bearer {token}'}
    )

    _assert_scim_error(response, 403)


# ── lookup, list and discovery ───────────────────────────────────────────


async def test_lookup_by_user_name_external_id_and_id(directory):
    acme = await directory()
    created = (await acme.create('Amy@Example.com', externalId='ext-amy')).json()
    await acme.create('ben@example.com', externalId='ext-ben')

    for filter_ in (
        'userName eq "amy@example.com"',
        'username EQ "AMY@example.com"',
        'externalId eq "ext-amy"',
    ):
        found = await acme.request('GET', '/Users', params={'filter': filter_})
        assert found.status_code == 200, found.text
        assert found.headers['content-type'].startswith(SCIM_MEDIA_TYPE)
        body = found.json()
        assert body['schemas'] == ['urn:ietf:params:scim:api:messages:2.0:ListResponse']
        assert (body['totalResults'], body['startIndex'], body['itemsPerPage']) == (
            1, 1, 1
        )  # fmt: skip
        assert body['Resources'] == [created]

    missing = await acme.request(
        'GET', '/Users', params={'filter': 'userName eq "nobody@example.com"'}
    )
    assert missing.json()['totalResults'] == 0
    assert missing.json()['Resources'] == []

    assert (await acme.request('GET', f'/Users/{created["id"]}')).json() == created
    _assert_scim_error(await acme.request('GET', f'/Users/{uuid.uuid4()}'), 404)
    _assert_scim_error(await acme.request('GET', '/Users/not-a-uuid'), 404)


async def test_list_is_paginated_with_start_index_and_count(directory):
    acme = await directory()
    ids = [(await acme.create(f'user{n}@example.com')).json()['id'] for n in range(3)]

    async def page(**params) -> dict:
        response = await acme.request('GET', '/Users', params=params)
        assert response.status_code == 200, response.text
        return response.json()

    everything = await page()
    assert [r['id'] for r in everything['Resources']] == ids
    assert everything['totalResults'] == 3

    second = await page(startIndex=2, count=1)
    assert [r['id'] for r in second['Resources']] == [ids[1]]
    assert (second['totalResults'], second['startIndex'], second['itemsPerPage']) == (
        3, 2, 1
    )  # fmt: skip

    count_only = await page(count=0)
    assert (count_only['totalResults'], count_only['Resources']) == (3, [])
    assert (await page(startIndex=4))['Resources'] == []
    assert (await page(startIndex=0))['startIndex'] == 1


@pytest.mark.parametrize(
    'params, scim_type',
    [
        ({'filter': 'emails.value eq "a@example.com"'}, 'invalidFilter'),
        ({'filter': 'userName co "amy"'}, 'invalidFilter'),
        ({'filter': 'userName eq "a" or userName eq "b"'}, 'invalidFilter'),
        ({'startIndex': 'first'}, 'invalidValue'),
    ],
)
async def test_unsupported_list_queries_are_scim_errors(directory, params, scim_type):
    acme = await directory()

    _assert_scim_error(
        await acme.request('GET', '/Users', params=params), 400, scim_type
    )


async def test_discovery_documents(directory):
    acme = await directory()

    config = await acme.request('GET', '/ServiceProviderConfig')
    types = await acme.request('GET', '/ResourceTypes')
    schemas = await acme.request('GET', '/Schemas')

    for response in (config, types, schemas):
        assert response.status_code == 200, response.text
        assert response.headers['content-type'].startswith(SCIM_MEDIA_TYPE)
    assert config.json()['patch'] == {'supported': True}
    assert config.json()['bulk']['supported'] is False
    assert config.json()['filter']['supported'] is True
    assert [r['endpoint'] for r in types.json()['Resources']] == ['/Users']
    assert [s['id'] for s in schemas.json()['Resources']] == [
        'urn:ietf:params:scim:schemas:core:2.0:User'
    ]


# ── request validation ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    'body, scim_type',
    [
        ({'emails': [{'value': 'a@example.com'}]}, 'invalidValue'),
        ({'userName': 'not-an-email'}, 'invalidValue'),
        ({'userName': 'a@example.com', 'active': 'true'}, 'invalidValue'),
        ({'userName': 'a@example.com', 'emails': [{'value': 'nope'}]}, 'invalidValue'),
        (['not', 'an', 'object'], 'invalidSyntax'),
    ],
)
async def test_invalid_create_bodies_are_scim_errors(
    directory, session_maker, body, scim_type
):
    acme = await directory()

    _assert_scim_error(await acme.request('POST', '/Users', json=body), 400, scim_type)
    malformed = await acme.request('POST', '/Users', content=b'{not json')
    _assert_scim_error(malformed, 400, 'invalidSyntax')

    with session_maker() as session:
        assert session.scalar(select(func.count()).select_from(ScimUser)) == 0


@pytest.mark.parametrize(
    'operations, scim_type',
    [
        ([{'op': 'replace', 'value': {'active': 'false'}}], 'invalidValue'),
        ([{'op': 'replace', 'path': 'active', 'value': 'False'}], 'invalidValue'),
        ([{'op': 'replace', 'path': 'active', 'value': 0}], 'invalidValue'),
        (
            [{'op': 'replace', 'path': 'userName', 'value': 'x@example.com'}],
            'invalidPath',
        ),
        (
            [{'op': 'replace', 'value': {'active': False, 'userName': 'x'}}],
            'invalidPath',
        ),
        ([{'op': 'remove', 'path': 'active'}], 'invalidPath'),
        ([], 'invalidValue'),
    ],
)
async def test_patch_accepts_only_an_explicit_boolean_active(
    directory, session_maker, operations, scim_type
):
    acme = await directory()
    scim_id, user_id = await _provision(acme, 'cleo@example.com')

    response = await acme.request(
        'PATCH',
        f'/Users/{scim_id}',
        json={'schemas': [PATCH_SCHEMA], 'Operations': operations},
    )

    _assert_scim_error(response, 400, scim_type)
    assert _record(session_maker, scim_id).active is True
    assert _membership(session_maker, acme.org_id, user_id) is not None


async def test_patch_with_a_path_deactivates(directory, session_maker):
    acme = await directory()
    scim_id, user_id = await _provision(acme, 'dana@example.com')

    response = await acme.request(
        'PATCH',
        f'/Users/{scim_id}',
        json={
            'schemas': [PATCH_SCHEMA],
            'Operations': [{'op': 'Replace', 'path': 'active', 'value': False}],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()['active'] is False
    assert _membership(session_maker, acme.org_id, user_id) is None


# ── retries and races ────────────────────────────────────────────────────


async def test_repeating_a_create_is_a_conflict_and_changes_nothing(
    directory, session_maker
):
    acme = await directory()
    first = await acme.create('eve@example.com', externalId='ext-eve')
    assert first.status_code == 201, first.text

    same_name = await acme.create('EVE@example.com')
    same_external_id = await acme.create('eve2@example.com', externalId='ext-eve')

    _assert_scim_error(same_name, 409, 'uniqueness')
    _assert_scim_error(same_external_id, 409, 'uniqueness')
    assert _accounts_with_email(session_maker, 'eve@example.com') == 1
    assert _accounts_with_email(session_maker, 'eve2@example.com') == 0
    with session_maker() as session:
        assert session.scalar(select(func.count()).select_from(ScimUser)) == 1


async def test_concurrent_creates_of_one_user_make_one_account(
    directory, session_maker
):
    acme = await directory()

    responses = await asyncio.gather(
        *(acme.create('finn@example.com') for _ in range(4))
    )

    assert sorted(r.status_code for r in responses) == [201, 409, 409, 409]
    assert _accounts_with_email(session_maker, 'finn@example.com') == 1
    with session_maker() as session:
        assert session.scalar(select(func.count()).select_from(ScimUser)) == 1
        assert session.scalar(select(func.count()).select_from(OrgMember).where(
            OrgMember.org_id == acme.org_id
        )) == 1  # fmt: skip


async def test_repeated_and_concurrent_lifecycle_changes_are_idempotent(
    directory, session_maker, roles
):
    acme = await directory()
    scim_id, user_id = await _provision(acme, 'gail@example.com')

    off = await asyncio.gather(*(acme.patch_active(scim_id, False) for _ in range(3)))
    assert [r.status_code for r in off] == [200, 200, 200]
    assert all(r.json()['active'] is False for r in off)
    assert _membership(session_maker, acme.org_id, user_id) is None

    on = await asyncio.gather(*(acme.patch_active(scim_id, True) for _ in range(3)))
    assert [r.status_code for r in on] == [200, 200, 200]
    assert _record(session_maker, scim_id).active is True
    assert _membership(session_maker, acme.org_id, user_id).role_id == roles['member']


# ── a record still being created is not a deactivation ───────────────────


async def _reserve(directory_, email: str) -> ScimUser:
    """The row ``create`` inserts before it has linked or checked anyone."""
    return await ScimStore.insert_user(
        org_id=directory_.org_id, user_name=email, email=email, active=False
    )


async def test_a_staged_reservation_does_not_deny_an_existing_account(
    directory, account, session_maker, roles, client
):
    acme = await directory()
    owner = account('owner@example.com')
    _add_membership(session_maker, acme.org_id, owner.id, roles['owner'])
    owner_key = await _api_key(owner.id, acme.org_id)
    invitee = account('invitee@example.com')

    await _reserve(acme, 'owner@example.com')
    await _reserve(acme, 'invitee@example.com')

    assert await ScimStore.is_deactivated(acme.org_id, owner.id) is False
    assert (await _effective_org(client, owner_key)).json() == {
        'org_id': str(acme.org_id)
    }
    await OrgMemberStore.add_user_to_org(
        org_id=acme.org_id,
        user_id=invitee.id,
        role_id=roles['member'],
        llm_api_key='synthetic-llm-key',
        status='active',
    )
    assert _membership(session_maker, acme.org_id, invitee.id) is not None


async def test_deactivating_a_leftover_reservation_checks_who_holds_the_email(
    directory, account, session_maker, roles, client
):
    acme = await directory()
    owner = account('owner@example.com')
    _add_membership(session_maker, acme.org_id, owner.id, roles['owner'])
    owner_key = await _api_key(owner.id, acme.org_id)
    member = account('member@example.com')
    _add_membership(session_maker, acme.org_id, member.id, roles['member'])
    member_key = await _api_key(member.id, acme.org_id)
    owner_row = await _reserve(acme, 'owner@example.com')
    member_row = await _reserve(acme, 'member@example.com')

    refused = await acme.patch_active(str(owner_row.id), False)
    done = await acme.patch_active(str(member_row.id), False)

    _assert_scim_error(refused, 409)
    assert _record(session_maker, str(owner_row.id)).deactivated_at is None
    assert (await _effective_org(client, owner_key)).status_code == 200
    assert _membership(session_maker, acme.org_id, owner.id).role_id == roles['owner']

    # A deactivation that goes through is a marker, and denies.
    assert done.status_code == 200, done.text
    record = _record(session_maker, str(member_row.id))
    assert (record.user_id, record.active) == (member.id, False)
    assert record.deactivated_at is not None
    assert await ScimStore.is_deactivated(acme.org_id, member.id) is True
    assert (await _effective_org(client, member_key)).status_code == 401
    assert _membership(session_maker, acme.org_id, member.id) is None
