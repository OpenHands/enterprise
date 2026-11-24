"""Group -> organization mapping on the OAuth v2 / SAML login path (OHE-3891).

A login provider can map identity provider groups to organizations. A user
whose login asserts a mapped group becomes a member of that organization;
nothing is ever changed or removed for a user who is already in it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from server.auth.provider_identity import add_user_to_mapped_orgs, asserted_groups
from server.routes.org_models import LiteLLMIntegrationError
from storage.auth_provider_details import GroupOrgMapping
from storage.org_member import OrgMember
from storage.org_service import OrgService
from storage.role import Role
from storage.user import ACCOUNT_TYPE_SERVICE, User

MEMBER_LLM_KEY = 'sk-member-key'


def _litellm_settings() -> SimpleNamespace:
    return SimpleNamespace(
        agent_settings=SimpleNamespace(
            llm=SimpleNamespace(api_key=SecretStr(MEMBER_LLM_KEY))
        )
    )


@pytest.fixture
def roles(session_maker) -> dict[str, int]:
    """The org roles a real install has; ids by name."""
    with session_maker() as session:
        session.add_all(
            [
                Role(name='owner', rank=10),
                Role(name='admin', rank=20),
                Role(name='member', rank=1000),
            ]
        )
        session.commit()
        return {role.name: role.id for role in session.scalars(select(Role))}


@pytest.fixture
def litellm(app_db_session, oauth_v2_stores, roles):
    """Stores on the test database, with only the LiteLLM call replaced."""
    with patch.object(
        OrgService,
        'create_litellm_integration',
        new=AsyncMock(return_value=_litellm_settings()),
    ) as create_litellm_integration:
        yield create_litellm_integration


@pytest.fixture
def new_user(create_org, create_user, session_maker, roles, litellm):
    """Factory for a user as sign-up leaves one: on their personal workspace."""

    def _create() -> User:
        user_id = uuid.uuid4()
        create_org(id=user_id, name=f'user_{user_id}_org')
        user = create_user(id=user_id, current_org_id=user_id)
        _add_membership(session_maker, user_id, user_id, roles['owner'])
        return user

    return _create


def _add_membership(session_maker, org_id, user_id, role_id, status='active') -> None:
    with session_maker() as session:
        session.add(
            OrgMember(
                org_id=org_id,
                user_id=user_id,
                role_id=role_id,
                llm_api_key='existing-key',
                status=status,
            )
        )
        session.commit()


def _memberships(session_maker, user_id) -> dict[uuid.UUID, OrgMember]:
    with session_maker() as session:
        rows = session.scalars(select(OrgMember).where(OrgMember.user_id == user_id))
        return {row.org_id: row for row in rows}


def _current_org_id(session_maker, user_id) -> uuid.UUID:
    with session_maker() as session:
        return session.get(User, user_id).current_org_id


def _mappings(**org_id_by_group) -> list[GroupOrgMapping]:
    return [
        GroupOrgMapping(group=group, org_id=org_id)
        for group, org_id in org_id_by_group.items()
    ]


async def test_user_in_a_mapped_group_joins_the_organization_as_a_member(
    new_user, create_org, session_maker, roles
):
    user = new_user()
    platform, operations = create_org(), create_org()
    mappings = _mappings(Platform=platform.id, Operations=operations.id)

    await add_user_to_mapped_orgs(mappings, str(user.id), ['Everyone', 'Platform'])

    memberships = _memberships(session_maker, user.id)
    assert set(memberships) == {user.id, platform.id}
    assert memberships[platform.id].role_id == roles['member']
    assert memberships[platform.id].status == 'active'
    assert memberships[platform.id].llm_api_key.get_secret_value() == MEMBER_LLM_KEY


async def test_user_on_their_personal_workspace_lands_in_the_organization(
    new_user, create_org, session_maker
):
    user = new_user()
    org = create_org()

    await add_user_to_mapped_orgs(
        _mappings(Platform=org.id), str(user.id), ['Platform']
    )

    assert _current_org_id(session_maker, user.id) == org.id


async def test_user_working_in_another_organization_is_not_moved(
    new_user, create_org, session_maker, roles
):
    user = new_user()
    chosen, mapped = create_org(), create_org()
    _add_membership(session_maker, chosen.id, user.id, roles['member'])
    with session_maker() as session:
        session.get(User, user.id).current_org_id = chosen.id
        session.commit()

    await add_user_to_mapped_orgs(
        _mappings(Platform=mapped.id), str(user.id), ['Platform']
    )

    assert mapped.id in _memberships(session_maker, user.id)
    assert _current_org_id(session_maker, user.id) == chosen.id


async def test_user_in_no_mapped_group_joins_nothing(
    new_user, create_org, session_maker
):
    user = new_user()
    org = create_org()

    # Matching is exact: neither another case nor a longer name is the group.
    await add_user_to_mapped_orgs(
        _mappings(Platform=org.id), str(user.id), ['platform', 'Platform-Admins']
    )

    assert set(_memberships(session_maker, user.id)) == {user.id}


async def test_existing_membership_is_left_as_it_is(
    new_user, create_org, session_maker, roles, litellm
):
    user = new_user()
    org = create_org()
    _add_membership(session_maker, org.id, user.id, roles['admin'], status='inactive')

    await add_user_to_mapped_orgs(
        _mappings(Platform=org.id), str(user.id), ['Platform']
    )

    membership = _memberships(session_maker, user.id)[org.id]
    assert (membership.role_id, membership.status) == (roles['admin'], 'inactive')
    assert _current_org_id(session_maker, user.id) == user.id
    litellm.assert_not_awaited()


@pytest.mark.parametrize('unavailable', ['deleted', 'personal_workspace', 'suspended'])
async def test_user_is_not_added_to_an_unavailable_organization(
    unavailable, new_user, create_org, session_maker
):
    user = new_user()
    org_id = {
        'deleted': uuid.uuid4,
        'personal_workspace': lambda: new_user().id,
        'suspended': lambda: create_org(status='suspended').id,
    }[unavailable]()

    await add_user_to_mapped_orgs(
        _mappings(Platform=org_id), str(user.id), ['Platform']
    )

    assert set(_memberships(session_maker, user.id)) == {user.id}
    assert _current_org_id(session_maker, user.id) == user.id


async def test_failed_add_does_not_stop_the_other_organizations(
    new_user, create_org, session_maker, litellm
):
    user = new_user()
    failing, working = create_org(), create_org()

    async def _create_litellm_integration(org_id, user_id):
        if org_id == failing.id:
            raise LiteLLMIntegrationError('LiteLLM integration failed')
        return _litellm_settings()

    litellm.side_effect = _create_litellm_integration

    await add_user_to_mapped_orgs(
        _mappings(Platform=failing.id, Operations=working.id),
        str(user.id),
        ['Platform', 'Operations'],
    )

    assert set(_memberships(session_maker, user.id)) == {user.id, working.id}
    assert _current_org_id(session_maker, user.id) == working.id


async def test_two_groups_mapped_to_one_organization_add_the_user_once(
    new_user, create_org, session_maker, litellm
):
    user = new_user()
    org = create_org()

    await add_user_to_mapped_orgs(
        _mappings(Platform=org.id, Engineering=org.id),
        str(user.id),
        ['Platform', 'Engineering'],
    )

    assert set(_memberships(session_maker, user.id)) == {user.id, org.id}
    litellm.assert_awaited_once()


async def test_service_account_joins_nothing(
    create_org, create_user, session_maker, litellm
):
    account = create_user(account_type=ACCOUNT_TYPE_SERVICE)
    org = create_org()

    await add_user_to_mapped_orgs(
        _mappings(Platform=org.id), str(account.id), ['Platform']
    )

    assert _memberships(session_maker, account.id) == {}


@pytest.mark.parametrize(
    'claims, expected',
    [
        ({'groups': ['Everyone', 'Platform']}, ['Everyone', 'Platform']),
        ({'groups': 'Platform, Operations'}, ['Platform, Operations']),
        ({'groups': ['Platform', 7, None]}, ['Platform']),
        ({'groups': {'Platform': True}}, []),
        ({'groups': None}, []),
        ({}, []),
    ],
    ids=['list', 'single-string', 'non-string-entries', 'not-a-list', 'null', 'absent'],
)
def test_asserted_groups(claims, expected):
    assert asserted_groups(claims) == expected
