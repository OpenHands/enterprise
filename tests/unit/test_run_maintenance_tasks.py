"""The maintenance task runner: per-run claims (#552) and task processing."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from run_maintenance_tasks import (
    DEFAULT_CLAIM_LEASE_SECONDS,
    claim_lease,
    claim_next_task,
    finish_task,
    main,
    run_tasks,
)
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus

LEASE = timedelta(seconds=DEFAULT_CLAIM_LEASE_SECONDS)


def _naive_utc(delta: timedelta = timedelta()) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + delta


def _add_task(session_maker, **fields) -> int:
    fields.setdefault('status', MaintenanceTaskStatus.PENDING)
    with session_maker() as session:
        task = MaintenanceTask(
            processor_type='test.processor', processor_json='{}', **fields
        )
        session.add(task)
        session.commit()
        return task.id


def _get(session_maker, task_id: int) -> MaintenanceTask:
    with session_maker() as session:
        task = session.get(MaintenanceTask, task_id)
        assert task is not None
        return task


def _set(session_maker, task_id: int, **values) -> None:
    with session_maker() as session:
        session.execute(
            update(MaintenanceTask)
            .where(MaintenanceTask.id == task_id)
            .values(**values)
        )
        session.commit()


def _runner_sessions_with(session_maker, setting: str):
    """Point the runner at the test database with a server setting applied."""
    engine = create_engine(session_maker.kw['bind'].url, poolclass=NullPool)

    @event.listens_for(engine, 'connect')
    def _apply(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute(f'SET {setting}')
        cursor.close()

    return patch('run_maintenance_tasks.session_maker', sessionmaker(bind=engine))


@pytest.fixture(autouse=True)
def runner_sessions(session_maker):
    with patch('run_maintenance_tasks.session_maker', session_maker):
        yield


def test_claim_takes_the_oldest_pending_task(session_maker):
    older = _add_task(session_maker, created_at=_naive_utc(-timedelta(hours=2)))
    _add_task(session_maker, created_at=_naive_utc(-timedelta(hours=1)))
    run_id = uuid.uuid4()

    task = claim_next_task(run_id, LEASE)

    assert task is not None and task.id == older
    stored = _get(session_maker, older)
    assert stored.status == MaintenanceTaskStatus.WORKING
    assert stored.claim_run_id == run_id
    assert stored.claimed_at is not None and stored.claimed_at.tzinfo is not None
    assert stored.started_at is not None and stored.started_at.tzinfo is None


def test_concurrent_claims_take_different_tasks(session_maker):
    older = _add_task(session_maker, created_at=_naive_utc(-timedelta(hours=2)))
    newer = _add_task(session_maker, created_at=_naive_utc(-timedelta(hours=1)))

    with session_maker() as other_run:
        other_run.query(MaintenanceTask).filter(
            MaintenanceTask.id == older
        ).with_for_update().one()
        # A claim that waits on the lock fails here instead of hanging the suite.
        with _runner_sessions_with(session_maker, "lock_timeout = '5s'"):
            task = claim_next_task(uuid.uuid4(), LEASE)
        other_run.rollback()

    assert task is not None and task.id == newer
    assert _get(session_maker, older).status == MaintenanceTaskStatus.PENDING


def test_a_claim_inside_its_lease_is_not_taken(session_maker):
    _add_task(
        session_maker,
        status=MaintenanceTaskStatus.WORKING,
        claim_run_id=uuid.uuid4(),
        claimed_at=datetime.now(timezone.utc) - LEASE + timedelta(minutes=5),
        started_at=_naive_utc(-LEASE + timedelta(minutes=5)),
    )

    assert claim_next_task(uuid.uuid4(), LEASE) is None


def test_a_claim_past_its_lease_is_taken_over(session_maker):
    previous_run = uuid.uuid4()
    task_id = _add_task(
        session_maker,
        status=MaintenanceTaskStatus.WORKING,
        claim_run_id=previous_run,
        claimed_at=datetime.now(timezone.utc) - LEASE - timedelta(minutes=1),
        started_at=_naive_utc(-LEASE - timedelta(minutes=1)),
    )
    run_id = uuid.uuid4()

    task = claim_next_task(run_id, LEASE)

    assert task is not None and task.id == task_id
    assert _get(session_maker, task_id).claim_run_id == run_id


def test_a_taken_over_claim_gets_a_fresh_lease(session_maker):
    previous_run = uuid.uuid4()
    task_id = _add_task(
        session_maker,
        status=MaintenanceTaskStatus.WORKING,
        claim_run_id=previous_run,
        claimed_at=datetime.now(timezone.utc) - LEASE - timedelta(minutes=1),
        started_at=_naive_utc(-LEASE - timedelta(minutes=1)),
    )
    run_id = uuid.uuid4()

    with patch('run_maintenance_tasks.logger') as logger:
        assert claim_next_task(run_id, LEASE) is not None

    logger.warning.assert_called_once_with(
        'maintenance_task.reclaimed',
        extra={
            'task_id': task_id,
            'previous_claim_run_id': str(previous_run),
            'claim_run_id': str(run_id),
        },
    )
    assert claim_next_task(uuid.uuid4(), LEASE) is None


def test_the_started_at_fallback_is_read_as_utc(session_maker):
    """A non-UTC session time zone must not age a claim-less row early."""
    _add_task(
        session_maker,
        status=MaintenanceTaskStatus.WORKING,
        started_at=_naive_utc(-timedelta(minutes=5)),
    )

    with _runner_sessions_with(session_maker, "TIME ZONE 'Asia/Tokyo'"):
        assert claim_next_task(uuid.uuid4(), LEASE) is None


def test_tasks_created_together_are_claimed_by_id(session_maker):
    created_at = _naive_utc(-timedelta(hours=1))
    _add_task(session_maker, id=1002, created_at=created_at)
    _add_task(session_maker, id=1001, created_at=created_at)

    task = claim_next_task(uuid.uuid4(), LEASE)

    assert task is not None and task.id == 1001


def test_a_working_task_without_a_claim_uses_started_at(session_maker):
    """Rows from code without claims are reclaimable once started_at passes the lease."""
    abandoned = _add_task(
        session_maker,
        status=MaintenanceTaskStatus.WORKING,
        started_at=_naive_utc(-LEASE - timedelta(minutes=1)),
    )
    _add_task(
        session_maker,
        status=MaintenanceTaskStatus.WORKING,
        started_at=_naive_utc(-LEASE + timedelta(minutes=5)),
    )

    task = claim_next_task(uuid.uuid4(), LEASE)

    assert task is not None and task.id == abandoned
    assert claim_next_task(uuid.uuid4(), LEASE) is None


def test_a_run_that_lost_its_claim_cannot_write(session_maker):
    task_id = _add_task(session_maker)
    first_run, second_run = uuid.uuid4(), uuid.uuid4()
    assert claim_next_task(first_run, LEASE) is not None
    _set(
        session_maker,
        task_id,
        claimed_at=datetime.now(timezone.utc) - LEASE - timedelta(minutes=1),
    )
    assert claim_next_task(second_run, LEASE) is not None

    with patch('run_maintenance_tasks.logger') as logger:
        assert not finish_task(
            task_id, first_run, {'stale': True}, MaintenanceTaskStatus.COMPLETED
        )
    logger.warning.assert_called_once_with(
        'maintenance_task.stale_write_rejected',
        extra={'task_id': task_id, 'claim_run_id': str(first_run)},
    )
    stored = _get(session_maker, task_id)
    assert stored.status == MaintenanceTaskStatus.WORKING
    assert stored.claim_run_id == second_run

    assert finish_task(
        task_id, second_run, {'done': True}, MaintenanceTaskStatus.COMPLETED
    )
    stored = _get(session_maker, task_id)
    assert stored.status == MaintenanceTaskStatus.COMPLETED
    assert stored.info == {'done': True}


def test_the_lease_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv('MAINTENANCE_TASK_CLAIM_LEASE_SECONDS', raising=False)
    assert claim_lease() == timedelta(seconds=2100)

    monkeypatch.setenv('MAINTENANCE_TASK_CLAIM_LEASE_SECONDS', '2700')
    assert claim_lease() == timedelta(seconds=2700)


async def test_run_tasks_processes_pending_tasks_in_order(session_maker):
    first = _add_task(session_maker, created_at=_naive_utc(-timedelta(hours=2)))
    second = _add_task(session_maker, created_at=_naive_utc(-timedelta(hours=1)))
    seen: list[int] = []

    async def processor(task):
        seen.append(task.id)
        return {'processed': True}

    with patch.object(MaintenanceTask, 'get_processor', return_value=processor):
        assert await run_tasks() == 0

    assert seen == [first, second]
    for task_id in (first, second):
        stored = _get(session_maker, task_id)
        assert stored.status == MaintenanceTaskStatus.COMPLETED
        assert stored.info == {'processed': True}
        assert stored.updated_at.tzinfo is None


async def test_run_tasks_records_a_processor_error(session_maker):
    task_id = _add_task(session_maker)
    processor = AsyncMock(side_effect=ValueError('Test error'))

    with patch.object(MaintenanceTask, 'get_processor', return_value=processor):
        assert await run_tasks() == 1

    stored = _get(session_maker, task_id)
    assert stored.status == MaintenanceTaskStatus.ERROR
    assert stored.info == {'error': 'Test error'}


async def test_run_tasks_does_not_overwrite_a_task_taken_over_mid_run(session_maker):
    task_id = _add_task(session_maker)
    new_owner = uuid.uuid4()

    async def processor(task):
        _set(session_maker, task.id, claim_run_id=new_owner)
        return {'error_count': 1}

    with patch.object(MaintenanceTask, 'get_processor', return_value=processor):
        assert await run_tasks() == 0

    stored = _get(session_maker, task_id)
    assert stored.status == MaintenanceTaskStatus.WORKING
    assert stored.claim_run_id == new_owner
    assert stored.info is None


async def test_run_tasks_waits_for_a_task_delay(session_maker):
    task_id = _add_task(session_maker, delay=1)
    sleep = AsyncMock()

    with (
        patch.object(
            MaintenanceTask, 'get_processor', return_value=AsyncMock(return_value={})
        ),
        patch('asyncio.sleep', sleep),
    ):
        await run_tasks()

    sleep.assert_called_once_with(1)
    assert _get(session_maker, task_id).status == MaintenanceTaskStatus.COMPLETED


async def test_main_reruns_an_abandoned_task_instead_of_failing_it(session_maker):
    abandoned = _add_task(
        session_maker,
        status=MaintenanceTaskStatus.WORKING,
        started_at=_naive_utc(-timedelta(hours=2)),
    )
    pending = _add_task(session_maker)

    with (
        patch.object(
            MaintenanceTask,
            'get_processor',
            return_value=AsyncMock(return_value={'processed': True}),
        ),
        patch(
            'server.maintenance_task_processor.managed_llm_key_ownership_processor.'
            'enqueue_managed_llm_key_ownership_tasks',
            return_value=0,
        ),
    ):
        await main()

    for task_id in (abandoned, pending):
        stored = _get(session_maker, task_id)
        assert stored.status == MaintenanceTaskStatus.COMPLETED
        assert stored.info == {'processed': True}
