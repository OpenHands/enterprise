"""Real PostgreSQL coverage for durable budget intent and cross-worker exclusion."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from storage.budget_control import (
    BudgetControlConflict,
    BudgetWriteDenied,
    _lock_key,
    budget_control_session,
    budget_request_hash,
    current_budget_control,
)
from storage.org_budget_operation import OrgBudgetOperation
from storage.org_budget_settings import OrgBudgetSettings


@pytest.fixture
async def controlled_org(create_org, async_session_maker):
    org = create_org()
    async with async_session_maker() as session:
        session.add(OrgBudgetSettings(org_id=org.id, control_mode='needs_adoption'))
        await session.commit()
    return org.id


async def reserve(control, **kwargs):
    defaults = {
        'idempotency_key': 'adoption-1',
        'request_hash': budget_request_hash({'remaining': 100}),
        'kind': 'adopt',
        'actor': 'admin',
        'plan': {'team_baseline': 40, 'writes': [{'max_budget': 140}]},
    }
    defaults.update(kwargs)
    return await control.reserve_operation(**defaults)


@pytest.mark.asyncio
async def test_intent_is_committed_without_releasing_lock(
    async_engine, async_session_maker, controlled_org
):
    async with budget_control_session(async_engine, controlled_org) as control:
        operation = await reserve(control)
        async with async_session_maker() as reader:
            durable = await reader.get(OrgBudgetOperation, operation.id)
            assert durable.plan == {
                'team_baseline': 40,
                'writes': [{'max_budget': 140}],
            }
            assert not await reader.scalar(
                text('SELECT pg_try_advisory_lock(:key)'),
                {'key': _lock_key(controlled_org)},
            )
        await control.require_executable(operation)
    async with budget_control_session(async_engine, controlled_org):
        pass


@pytest.mark.asyncio
async def test_retry_does_not_resnapshot_or_change_generation(
    async_engine, controlled_org
):
    async with budget_control_session(async_engine, controlled_org) as control:
        first = await reserve(control)
        first_id = first.id
        await control.record_failure(first, 'lost response after write')
    async with budget_control_session(async_engine, controlled_org) as control:
        retry = await reserve(
            control, plan={'team_baseline': 90, 'writes': [{'max_budget': 190}]}
        )
        assert retry.id == first_id
        assert retry.plan['team_baseline'] == 40
        assert retry.generation == 1
        assert retry.last_error == 'lost response after write'
        assert (await control.settings()).control_mode == 'needs_adoption'


@pytest.mark.asyncio
async def test_idempotency_key_rejects_different_request(async_engine, controlled_org):
    async with budget_control_session(async_engine, controlled_org) as control:
        await reserve(control)
        with pytest.raises(BudgetControlConflict, match='different request'):
            await reserve(control, request_hash=budget_request_hash({'remaining': 200}))


@pytest.mark.asyncio
async def test_pending_operation_rejects_different_operation(
    async_engine, controlled_org
):
    async with budget_control_session(async_engine, controlled_org) as control:
        await reserve(control)
        with pytest.raises(BudgetControlConflict, match='pending budget operation'):
            await reserve(control, idempotency_key='adoption-2')


@pytest.mark.asyncio
async def test_failed_transaction_after_intent_cannot_erase_it(
    async_engine, async_session_maker, controlled_org
):
    with pytest.raises(RuntimeError, match='crash'):
        async with budget_control_session(async_engine, controlled_org) as control:
            operation = await reserve(control)
            operation.last_error = 'uncommitted result'
            raise RuntimeError('crash')
    async with async_session_maker() as reader:
        durable = await reader.get(OrgBudgetOperation, operation.id)
        assert durable.status == 'pending'
        assert durable.last_error is None
    async with budget_control_session(async_engine, controlled_org) as control:
        await control.require_executable(await reserve(control))


@pytest.mark.asyncio
async def test_fabricated_or_merely_flushed_intent_cannot_authorize_write(
    async_engine, controlled_org
):
    async with budget_control_session(async_engine, controlled_org) as control:
        settings = await control.settings()
        settings.control_generation = 1
        operation = OrgBudgetOperation(
            id=uuid4(),
            org_id=controlled_org,
            idempotency_key='fake',
            request_hash='0' * 64,
            generation=1,
            kind='adopt',
            actor='admin',
            plan={'writes': []},
            status='pending',
        )
        with pytest.raises(BudgetWriteDenied, match='not durable'):
            await control.require_executable(operation)
        control.session.add(operation)
        await control.session.flush()
        with pytest.raises(BudgetWriteDenied, match='not durable'):
            await control.require_executable(operation)


@pytest.mark.asyncio
async def test_database_rejects_changing_original_targets(async_engine, controlled_org):
    async with budget_control_session(async_engine, controlled_org) as control:
        operation = await reserve(control)
        operation.plan = {'team_baseline': 90}
        with pytest.raises(DBAPIError, match='intent is immutable'):
            await control.session.commit()


@pytest.mark.asyncio
async def test_only_successful_adoption_becomes_managed(async_engine, controlled_org):
    async with budget_control_session(async_engine, controlled_org) as control:
        operation = await reserve(control)
        assert (await control.settings()).control_mode == 'needs_adoption'
        await control.record_failure(operation, 'readback unavailable')
        assert (await control.settings()).control_mode == 'needs_adoption'
        await control.finish_operation(operation)
        settings = await control.settings()
        assert settings.control_mode == 'managed'
        assert settings.control_changed_by == 'admin'
        assert operation.status == 'applied'
        with pytest.raises(BudgetWriteDenied):
            await control.require_executable(operation)
        assert (await reserve(control)).id == operation.id


@pytest.mark.asyncio
async def test_handoff_invalidates_pending_intent_and_preserves_plan(
    async_engine, controlled_org
):
    async with budget_control_session(async_engine, controlled_org) as control:
        operation = await reserve(control)
        await control.hand_off('admin-2')
        assert operation.status == 'abandoned'
        assert operation.plan['team_baseline'] == 40
        settings = await control.settings()
        assert settings.control_mode == 'external'
        assert settings.control_generation == 2
        assert settings.control_changed_by == 'admin-2'
        with pytest.raises(BudgetWriteDenied):
            await control.require_executable(operation)
        with pytest.raises(BudgetWriteDenied, match='not managed'):
            await reserve(control, kind='rollover', idempotency_key='roll-1')
        next_adoption = await reserve(control, idempotency_key='adoption-2')
        assert next_adoption.generation == 3


@pytest.mark.asyncio
async def test_session_lock_is_reentrant_but_cannot_escape_scope(
    async_engine, controlled_org
):
    async with budget_control_session(async_engine, controlled_org) as control:
        assert current_budget_control(controlled_org) is control
        async with budget_control_session(async_engine, controlled_org) as nested:
            assert nested is control
        with pytest.raises(BudgetControlConflict, match='same organization'):
            async with budget_control_session(async_engine, uuid4()):
                pytest.fail('Nested different organization must be rejected')
    with pytest.raises(BudgetWriteDenied):
        control.assert_locked()
    with pytest.raises(BudgetWriteDenied):
        current_budget_control(controlled_org)


@pytest.mark.asyncio
async def test_child_task_cannot_inherit_write_authority(async_engine, controlled_org):
    async with budget_control_session(async_engine, controlled_org):

        async def child():
            with pytest.raises(BudgetWriteDenied, match='not owned by this task'):
                current_budget_control(controlled_org)

        await asyncio.create_task(child())


@pytest.mark.asyncio
async def test_competing_workers_and_cancellation_release_lock(
    async_engine, controlled_org
):
    acquired = asyncio.Event()
    finish = asyncio.Event()

    async def owner():
        async with budget_control_session(async_engine, controlled_org) as control:
            await reserve(control)
            acquired.set()
            await finish.wait()

    task = asyncio.create_task(owner())
    await asyncio.wait_for(acquired.wait(), timeout=10)
    with pytest.raises(BudgetControlConflict, match='already in progress'):
        async with budget_control_session(async_engine, controlled_org):
            pytest.fail('Competing worker acquired the same lock')
    async with budget_control_session(async_engine, uuid4()):
        pass
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with budget_control_session(async_engine, controlled_org) as control:
        assert (await control.pending_operation()).generation == 1


@pytest.mark.asyncio
async def test_database_enforces_one_pending_operation_even_without_lock(
    async_engine, async_session_maker, controlled_org
):
    async with budget_control_session(async_engine, controlled_org) as control:
        await reserve(control)
    async with async_session_maker() as session:
        session.add(
            OrgBudgetOperation(
                org_id=controlled_org,
                idempotency_key='bypass',
                request_hash='0' * 64,
                generation=2,
                kind='adopt',
                actor='admin',
                plan={},
                status='pending',
            )
        )
        with pytest.raises(IntegrityError, match='uq_budget_operation_pending'):
            await session.commit()


@pytest.mark.asyncio
async def test_new_settings_never_implicitly_grant_ownership(
    create_org, async_session_maker
):
    org = create_org()
    async with async_session_maker() as session:
        session.add(OrgBudgetSettings(org_id=org.id, enabled=True, monthly_limit=100))
        await session.commit()
        settings = await session.scalar(select(OrgBudgetSettings))
        assert settings.control_mode == 'external'
        assert settings.control_generation == 0


@pytest.mark.asyncio
async def test_verification_works_with_a_single_connection_pool(
    test_database, controlled_org
):
    engine = create_async_engine(
        test_database.async_url, pool_size=1, max_overflow=0, pool_timeout=0.1
    )
    try:
        async with budget_control_session(engine, controlled_org) as control:
            operation = await reserve(control)
            await control.require_executable(operation)
            await control.finish_operation(operation)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_savepoint_commit_is_not_a_durable_intent_commit(
    async_engine, controlled_org
):
    async with budget_control_session(async_engine, controlled_org) as control:
        operation = await reserve(control)
        operation.last_error = 'not yet committed'
        await control.session.flush()
        async with control.session.begin_nested():
            pass
        with pytest.raises(BudgetWriteDenied, match='not durable'):
            await control.require_executable(operation)


@pytest.mark.asyncio
async def test_database_rejects_reopening_finished_operation(
    async_engine, controlled_org
):
    async with budget_control_session(async_engine, controlled_org) as control:
        operation = await reserve(control)
        await control.finish_operation(operation)
        operation.status = 'pending'
        with pytest.raises(DBAPIError, match='cannot be reopened'):
            await control.session.commit()
