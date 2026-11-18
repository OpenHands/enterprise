"""Real-DB tests for migration 219: rollback is non-destructive and re-upgrade is idempotent."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine

from storage.customer_gateway import (
    CustomerGatewayMemberCredential,
    CustomerGatewayOrgConfig,
)
from storage.org_member import OrgMember
from storage.role import Role
from tests import postgres_testdb


def _seed(session_maker, create_org, create_user) -> tuple[UUID, UUID]:
    """Rows built through the real ORM models and shared fixtures, so every NOT NULL
    column gets its repo default (raw SQL would miss Python-side defaults)."""
    org = create_org().id
    user = create_user(current_org_id=org).id
    with session_maker() as session:
        role = Role(name=f'member-{uuid4().hex[:8]}', rank=3)
        session.add(role)
        session.flush()
        session.add(
            OrgMember(
                org_id=org,
                user_id=user,
                role_id=role.id,
                llm_api_key='k',
                status='active',
            )
        )
        session.flush()
        session.add(
            CustomerGatewayOrgConfig(
                org_id=org,
                gateway_id='gw',
                enabled=True,
                profile={'base_url': 'https://gw/v1', 'models': {}},
                updated_by='op',
            )
        )
        session.add(
            CustomerGatewayMemberCredential(
                org_id=org,
                user_id=user,
                ciphertext='CIPHERTEXT-CANARY',
                fingerprint='fp',
                allowed_models=[],
                updated_by='op',
            )
        )
        session.commit()
    return org, user


def _ciphertexts(engine: Engine) -> list[str]:
    with engine.connect() as c:
        return list(
            c.execute(
                text('SELECT ciphertext FROM customer_gateway_member_credential')
            ).scalars()
        )


def test_downgrade_retains_tables_and_reupgrade_is_idempotent(
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
    session_maker,
    create_org,
    create_user,
):
    _seed(session_maker, create_org, create_user)
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '215'
    )
    assert _ciphertexts(engine) == ['CIPHERTEXT-CANARY']
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '219'
    )
    assert _ciphertexts(engine) == ['CIPHERTEXT-CANARY']
    with engine.connect() as c:
        assert (
            c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            == '219'
        )


def test_org_delete_cascades_gateway_rows(
    engine: Engine, session_maker, create_org, create_user
):
    org, _ = _seed(session_maker, create_org, create_user)
    with engine.begin() as c:
        c.execute(text('DELETE FROM org_member WHERE org_id = :o'), {'o': org})
        c.execute(text('DELETE FROM "user" WHERE current_org_id = :o'), {'o': org})
        c.execute(text('DELETE FROM org WHERE id = :o'), {'o': org})
    with engine.connect() as c:
        assert (
            c.execute(
                text('SELECT count(*) FROM customer_gateway_org_config')
            ).scalar_one()
            == 0
        )
        assert (
            c.execute(
                text('SELECT count(*) FROM customer_gateway_member_credential')
            ).scalar_one()
            == 0
        )
