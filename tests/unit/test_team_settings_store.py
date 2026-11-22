"""Real-PostgreSQL tests for the team settings scope store."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import sessionmaker

from openhands.app_server.settings.scoped import Inherit, Set
from storage.org_member import OrgMember
from storage.role import Role
from storage.settings_document import empty_settings_document
from storage.team import Team
from storage.team_member import TeamMember
from storage.team_settings_store import (
    TeamMembershipError,
    TeamSettingsRevisionConflict,
    load_team_settings,
    mutate_team_settings,
)


def _seed(
    session_maker: sessionmaker, create_org, create_user, *, with_membership=True
) -> tuple[UUID, UUID, UUID]:
    org_id = create_org().id
    user_id = create_user(current_org_id=org_id).id
    with session_maker() as session:
        role = Role(name=f'role-{uuid.uuid4().hex[:8]}', rank=3)
        session.add(role)
        session.flush()
        session.add(
            OrgMember(
                org_id=org_id,
                user_id=user_id,
                role_id=role.id,
                llm_api_key='k',
                status='active',
            )
        )
        team = Team(org_id=org_id, name='alpha')
        session.add(team)
        session.flush()
        if with_membership:
            session.add(TeamMember(org_id=org_id, team_id=team.id, user_id=user_id))
        session.commit()
        return org_id, user_id, team.id


async def test_load_requires_explicit_membership(
    session_maker: sessionmaker,
    async_session_maker: async_sessionmaker,
    create_org,
    create_user,
):
    org_id, user_id, team_id = _seed(
        session_maker, create_org, create_user, with_membership=False
    )

    async with async_session_maker() as session:
        with pytest.raises(TeamMembershipError):
            await load_team_settings(
                session, org_id=org_id, team_id=team_id, user_id=user_id
            )


async def test_load_rejects_cross_tenant_team_id(
    session_maker: sessionmaker,
    async_session_maker: async_sessionmaker,
    create_org,
    create_user,
):
    org_id, user_id, team_id = _seed(session_maker, create_org, create_user)
    other_org_id = create_org().id

    async with async_session_maker() as session:
        with pytest.raises(TeamMembershipError):
            await load_team_settings(
                session, org_id=other_org_id, team_id=team_id, user_id=user_id
            )


async def test_mutate_applies_explicit_operations_and_bumps_revision(
    session_maker: sessionmaker,
    async_session_maker: async_sessionmaker,
    create_org,
    create_user,
):
    org_id, user_id, team_id = _seed(session_maker, create_org, create_user)
    seen: list[dict] = []

    async with async_session_maker() as session:
        team = await mutate_team_settings(
            session,
            org_id=org_id,
            team_id=team_id,
            user_id=user_id,
            operations=[
                Set(('agent_settings', 'llm', 'model'), 'openhands/model'),
                Set(('agent_settings', 'tools'), None),
                Set(('conversation_settings', 'max_iterations'), 10),
            ],
            validate=seen.append,
        )
        await session.commit()
        assert team.revision == 1
        assert team.updated_by == user_id

    assert seen == [
        {
            'schema_version': 1,
            'agent_settings': {'llm': {'model': 'openhands/model'}, 'tools': None},
            'conversation_settings': {'max_iterations': 10},
        }
    ]

    async with async_session_maker() as session:
        loaded = await load_team_settings(
            session, org_id=org_id, team_id=team_id, user_id=user_id
        )
        assert loaded.settings == seen[0]
        assert loaded.revision == 1


async def test_inherit_removes_only_the_local_path(
    session_maker: sessionmaker,
    async_session_maker: async_sessionmaker,
    create_org,
    create_user,
):
    org_id, user_id, team_id = _seed(session_maker, create_org, create_user)

    async with async_session_maker() as session:
        await mutate_team_settings(
            session,
            org_id=org_id,
            team_id=team_id,
            user_id=user_id,
            operations=[
                Set(('agent_settings', 'llm', 'model'), 'local-model'),
                Set(('agent_settings', 'llm', 'base_url'), 'https://local'),
                Set(('conversation_settings', 'max_iterations'), 10),
            ],
        )
        await session.commit()

    async with async_session_maker() as session:
        team = await mutate_team_settings(
            session,
            org_id=org_id,
            team_id=team_id,
            user_id=user_id,
            operations=[Inherit(('agent_settings', 'llm', 'model'))],
        )
        await session.commit()

    assert team.settings == {
        'schema_version': 1,
        'agent_settings': {'llm': {'base_url': 'https://local'}},
        'conversation_settings': {'max_iterations': 10},
    }


async def test_stale_revision_is_rejected(
    session_maker: sessionmaker,
    async_session_maker: async_sessionmaker,
    create_org,
    create_user,
):
    org_id, user_id, team_id = _seed(session_maker, create_org, create_user)

    async with async_session_maker() as session:
        await mutate_team_settings(
            session,
            org_id=org_id,
            team_id=team_id,
            user_id=user_id,
            operations=[Set(('agent_settings', 'agent'), 'first')],
        )
        await session.commit()

    async with async_session_maker() as session:
        with pytest.raises(TeamSettingsRevisionConflict):
            await mutate_team_settings(
                session,
                org_id=org_id,
                team_id=team_id,
                user_id=user_id,
                operations=[Set(('agent_settings', 'agent'), 'second')],
                expected_revision=0,
            )
        await session.rollback()

    async with async_session_maker() as session:
        loaded = await load_team_settings(
            session, org_id=org_id, team_id=team_id, user_id=user_id
        )
        assert loaded.settings['agent_settings'] == {'agent': 'first'}
        assert loaded.revision == 1


async def test_validator_failure_does_not_persist_an_edit(
    session_maker: sessionmaker,
    async_session_maker: async_sessionmaker,
    create_org,
    create_user,
):
    org_id, user_id, team_id = _seed(session_maker, create_org, create_user)

    def reject(_document: dict) -> None:
        raise ValueError('invalid resolved settings')

    async with async_session_maker() as session:
        with pytest.raises(ValueError, match='invalid resolved settings'):
            await mutate_team_settings(
                session,
                org_id=org_id,
                team_id=team_id,
                user_id=user_id,
                operations=[Set(('agent_settings', 'agent'), 'bad')],
                validate=reject,
            )
        await session.rollback()

    async with async_session_maker() as session:
        loaded = await load_team_settings(
            session, org_id=org_id, team_id=team_id, user_id=user_id
        )
        assert loaded.settings == empty_settings_document()
        assert loaded.revision == 0


async def test_empty_operation_path_is_rejected(
    session_maker: sessionmaker,
    async_session_maker: async_sessionmaker,
    create_org,
    create_user,
):
    org_id, user_id, team_id = _seed(session_maker, create_org, create_user)

    async with async_session_maker() as session:
        with pytest.raises(ValueError, match='must name a path'):
            await mutate_team_settings(
                session,
                org_id=org_id,
                team_id=team_id,
                user_id=user_id,
                operations=[Set((), 'value')],
            )
        await session.rollback()

    with session_maker() as session:
        revision = session.execute(
            text('SELECT revision FROM team WHERE org_id = :org_id AND id = :team_id'),
            {'org_id': org_id, 'team_id': team_id},
        ).scalar()
    assert revision == 0
