import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from storage.org import Org
from storage.org_store import OrgStore

pytestmark = pytest.mark.postgresql


@pytest.fixture
async def postgres_session_maker():
    dsn = os.getenv('POSTGRES_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('POSTGRES_TEST_DATABASE_URL is required')

    engine = create_async_engine(dsn, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Org.__table__.drop, checkfirst=True)
        await conn.run_sync(Org.__table__.create)

    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Org.__table__.drop, checkfirst=True)
        await engine.dispose()


async def _insert_orgs(session_maker, rows):
    async with session_maker() as session:
        async with session.begin():
            for name, agent_settings in rows:
                session.add(
                    Org(
                        id=uuid.uuid4(),
                        name=name,
                        agent_settings=agent_settings,
                    )
                )


async def _settings_by_name(session_maker):
    async with session_maker() as session:
        rows = (await session.execute(select(Org.name, Org.agent_settings))).all()
    return {name: settings for name, settings in rows}


@pytest.mark.asyncio
async def test_reconcile_non_overwrite_updates_only_unset_applicable_rows(
    postgres_session_maker,
):
    await _insert_orgs(
        postgres_session_maker,
        [
            ('missing-condenser', {'agent_kind': 'openhands'}),
            ('json-null-condenser', {'condenser': None}),
            ('missing-max-tokens', {'condenser': {'enabled': True}}),
            ('json-null-max-tokens', {'condenser': {'max_tokens': None}}),
            ('existing-max-tokens', {'condenser': {'max_tokens': 123456}}),
            ('legacy-llm-kind', {'agent_kind': 'llm', 'condenser': {}}),
            ('acp-agent', {'agent_kind': 'acp'}),
            ('noop-condenser', {'condenser': {'condenser_kind': 'no_op'}}),
            ('malformed-condenser', {'condenser': ['bad']}),
        ],
    )

    async with postgres_session_maker() as session:
        async with session.begin():
            result = await OrgStore.reconcile_applicable_org_condenser_max_tokens(
                session,
                max_tokens=200000,
                overwrite_existing=False,
            )

    assert result.updated_count == 6
    assert result.skipped_agent_variant_count == 1
    assert result.skipped_condenser_variant_count == 1
    assert result.malformed_repaired_count == 2

    settings = await _settings_by_name(postgres_session_maker)
    assert settings['missing-condenser']['condenser']['max_tokens'] == 200000
    assert settings['json-null-condenser']['condenser']['max_tokens'] == 200000
    assert settings['missing-max-tokens']['condenser']['enabled'] is True
    assert settings['missing-max-tokens']['condenser']['max_tokens'] == 200000
    assert settings['json-null-max-tokens']['condenser']['max_tokens'] == 200000
    assert settings['existing-max-tokens']['condenser']['max_tokens'] == 123456
    assert settings['legacy-llm-kind']['condenser']['max_tokens'] == 200000
    assert 'condenser' not in settings['acp-agent']
    assert settings['noop-condenser']['condenser'] == {'condenser_kind': 'no_op'}
    assert settings['malformed-condenser']['condenser'] == {
        'condenser_kind': 'llm_summarizing',
        'max_tokens': 200000,
    }


@pytest.mark.asyncio
async def test_reconcile_overwrite_forces_applicable_existing_values(
    postgres_session_maker,
):
    await _insert_orgs(
        postgres_session_maker,
        [
            ('existing-max-tokens', {'condenser': {'max_tokens': 123456}}),
            ('noop-condenser', {'condenser': {'condenser_kind': 'no_op'}}),
            ('acp-agent', {'agent_kind': 'acp'}),
        ],
    )

    async with postgres_session_maker() as session:
        async with session.begin():
            result = await OrgStore.reconcile_applicable_org_condenser_max_tokens(
                session,
                max_tokens=200000,
                overwrite_existing=True,
            )

    assert result.updated_count == 1
    assert result.skipped_agent_variant_count == 1
    assert result.skipped_condenser_variant_count == 1
    assert result.malformed_repaired_count == 0

    settings = await _settings_by_name(postgres_session_maker)
    assert settings['existing-max-tokens']['condenser']['max_tokens'] == 200000
    assert settings['noop-condenser']['condenser'] == {'condenser_kind': 'no_op'}
    assert 'condenser' not in settings['acp-agent']
