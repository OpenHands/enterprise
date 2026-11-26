"""PostgreSQL tests for migration 227 (tenant-safe team settings scopes)."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from storage.org_member import OrgMember
from storage.role import Role
from storage.settings_document import empty_settings_document
from storage.team import Team
from tests import postgres_testdb


def _seed(
    session_maker: sessionmaker, create_org, create_user
) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    """Two tenants, one member each, and one team in tenant A."""
    org_a = create_org().id
    org_b = create_org().id
    user_a = create_user(current_org_id=org_a).id
    user_b = create_user(current_org_id=org_b).id

    with session_maker() as session:
        role = Role(name=f'role-{uuid.uuid4().hex[:8]}', rank=3)
        session.add(role)
        session.flush()
        session.add(
            OrgMember(
                org_id=org_a,
                user_id=user_a,
                role_id=role.id,
                llm_api_key='k',
                status='active',
            )
        )
        session.add(
            OrgMember(
                org_id=org_b,
                user_id=user_b,
                role_id=role.id,
                llm_api_key='k',
                status='active',
            )
        )
        team = Team(org_id=org_a, name='alpha')
        session.add(team)
        session.commit()
        team_id = team.id
    return org_a, org_b, user_a, user_b, team_id


def _table_names(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    'SELECT table_name FROM information_schema.tables '
                    "WHERE table_schema = 'public'"
                )
            ).scalars()
        )


def _insert_team_member(engine: Engine, org_id, team_id, user_id) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO team_member (org_id, team_id, user_id) '
                'VALUES (:org_id, :team_id, :user_id)'
            ),
            {'org_id': org_id, 'team_id': team_id, 'user_id': user_id},
        )


def test_team_row_owns_document_and_revision(
    engine: Engine, session_maker: sessionmaker, create_org
):
    org_id = create_org().id
    with session_maker() as session:
        team = Team(org_id=org_id, name='owners')
        session.add(team)
        session.commit()
        assert team.settings == empty_settings_document()
        assert team.revision == 0

    # There is no second settings table to drift from the team row.
    assert 'team_settings' not in _table_names(engine)


def test_membership_requires_same_tenant(
    engine: Engine,
    session_maker: sessionmaker,
    create_org,
    create_user,
):
    org_a, org_b, user_a, user_b, team_id = _seed(
        session_maker, create_org, create_user
    )

    # A member of tenant A in a team of tenant A is valid.
    _insert_team_member(engine, org_a, team_id, user_a)

    # Same team id, wrong tenant: the composite team foreign key rejects it.
    with pytest.raises(DBAPIError, match='fk_team_member_team'):
        _insert_team_member(engine, org_b, team_id, user_b)

    # Right tenant, user who is not an org member: rejected as well.
    with pytest.raises(DBAPIError, match='fk_team_member_org_member'):
        _insert_team_member(engine, org_a, team_id, user_b)


def test_team_name_is_unique_per_tenant(
    engine: Engine,
    session_maker: sessionmaker,
    create_org,
    create_user,
):
    org_a, org_b, _user_a, _user_b, _team_id = _seed(
        session_maker, create_org, create_user
    )

    with pytest.raises(IntegrityError, match='uq_team_org_id_name'):
        with engine.begin() as conn:
            conn.execute(
                text('INSERT INTO team (id, org_id, name) VALUES (:id, :org, :name)'),
                {'id': uuid.uuid4(), 'org': org_a, 'name': 'alpha'},
            )

    # The same display name in another tenant is allowed.
    with engine.begin() as conn:
        conn.execute(
            text('INSERT INTO team (id, org_id, name) VALUES (:id, :org, :name)'),
            {'id': uuid.uuid4(), 'org': org_b, 'name': 'alpha'},
        )


def test_downgrade_drops_only_team_tables(
    engine: Engine,
    session_maker: sessionmaker,
    create_org,
    create_user,
    test_database: postgres_testdb.TestDatabase,
):
    _seed(session_maker, create_org, create_user)
    assert {'team', 'team_member'} <= _table_names(engine)

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '226'
    )

    tables = _table_names(engine)
    assert 'team' not in tables
    assert 'team_member' not in tables
    # The org/member document migration stays applied.
    with engine.connect() as conn:
        columns = set(
            conn.execute(
                text(
                    'SELECT column_name FROM information_schema.columns '
                    "WHERE table_name = 'org'"
                )
            ).scalars()
        )
    assert 'settings' in columns

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '227'
    )
    assert {'team', 'team_member'} <= _table_names(engine)


def test_team_member_is_scoped_by_composite_primary_key(
    engine: Engine,
    session_maker: sessionmaker,
    create_org,
    create_user,
):
    org_a, _org_b, user_a, _user_b, team_id = _seed(
        session_maker, create_org, create_user
    )
    _insert_team_member(engine, org_a, team_id, user_a)

    with pytest.raises(IntegrityError, match='pk_team_member'):
        _insert_team_member(engine, org_a, team_id, user_a)

    with session_maker() as session:
        members = session.execute(text('SELECT COUNT(*) FROM team_member')).scalar()
    assert members == 1
