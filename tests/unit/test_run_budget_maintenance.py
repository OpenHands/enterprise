"""Regression tests for the budget maintenance CronJob entrypoint."""

import ast
import pathlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from unittest.mock import patch

import pytest

from run_budget_maintenance import enqueue_budget_tasks
from server.maintenance_task_processor.org_budget_maintenance_processor import (
    OrgBudgetMaintenanceProcessor,
)
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus
from storage.org_budget_operation import OrgBudgetOperation
from storage.org_budget_settings import OrgBudgetSettings


@pytest.fixture
def budget_queue(session_maker, create_org):
    def add_org(*, mode='managed', enabled=True, pending=False):
        org_id = create_org().id
        with session_maker() as session:
            session.add(
                OrgBudgetSettings(
                    org_id=org_id,
                    control_mode=mode,
                    enabled=enabled,
                    control_generation=int(pending),
                )
            )
            if pending:
                session.add(
                    OrgBudgetOperation(
                        org_id=org_id,
                        idempotency_key='adopt',
                        request_hash='test',
                        kind='adopt',
                        generation=1,
                        actor='admin',
                        plan={},
                        status='pending',
                    )
                )
            session.commit()
        return str(org_id)

    with patch('run_budget_maintenance.session_maker', session_maker):
        yield add_org


def add_task(
    session_maker,
    org_ids,
    *,
    state=MaintenanceTaskStatus.PENDING,
    age_minutes=0,
    no_start=False,
):
    timestamp = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=age_minutes)
    with session_maker() as session:
        task = MaintenanceTask(
            status=state,
            started_at=None if no_start else timestamp,
            created_at=timestamp,
            updated_at=timestamp,
            delay=0,
        )
        task.set_processor(OrgBudgetMaintenanceProcessor(org_ids=org_ids))
        session.add(task)
        session.commit()
        return task.id


def pending_org_ids(session_maker):
    with session_maker() as session:
        return [
            org_id
            for task in session.query(MaintenanceTask)
            .filter(MaintenanceTask.status == MaintenanceTaskStatus.PENDING)
            .all()
            for org_id in task.get_processor().org_ids
        ]


@pytest.mark.parametrize(
    'state', [MaintenanceTaskStatus.PENDING, MaintenanceTaskStatus.WORKING]
)
def test_one_queued_org_does_not_block_another(session_maker, budget_queue, state):
    first, second = budget_queue(), budget_queue()
    add_task(session_maker, [first], state=state)
    assert enqueue_budget_tasks() == 1
    assert pending_org_ids(session_maker).count(second) == 1
    assert enqueue_budget_tasks() == 0


@pytest.mark.parametrize('no_start', [False, True])
def test_stale_working_task_is_replaced_in_same_enqueue(
    session_maker, budget_queue, no_start
):
    org_id = budget_queue()
    old_id = add_task(
        session_maker,
        [org_id],
        state=MaintenanceTaskStatus.WORKING,
        age_minutes=120,
        no_start=no_start,
    )
    assert enqueue_budget_tasks() == 1
    with session_maker() as session:
        assert (
            session.get(MaintenanceTask, old_id).status == MaintenanceTaskStatus.ERROR
        )
    assert pending_org_ids(session_maker) == [org_id]


def test_recent_working_task_without_start_is_not_reclaimed(
    session_maker, budget_queue
):
    org_id = budget_queue()
    add_task(
        session_maker,
        [org_id],
        state=MaintenanceTaskStatus.WORKING,
        age_minutes=10,
        no_start=True,
    )
    assert enqueue_budget_tasks() == 0
    assert pending_org_ids(session_maker) == []


def test_old_pending_task_is_reused_after_cronjob_unsuspension(
    session_maker, budget_queue
):
    org_id = budget_queue()
    task_id = add_task(session_maker, [org_id], age_minutes=1440)
    assert enqueue_budget_tasks() == 0
    with session_maker() as session:
        assert (
            session.get(MaintenanceTask, task_id).status
            == MaintenanceTaskStatus.PENDING
        )
    assert pending_org_ids(session_maker) == [org_id]


@pytest.mark.parametrize(
    'processor_json', ['not-json', '{"org_ids":null}', '{"org_ids":[42]}']
)
def test_malformed_task_does_not_block_valid_organizations(
    session_maker, budget_queue, processor_json
):
    org_id = budget_queue()
    bad_id = add_task(session_maker, [])
    with session_maker() as session:
        task = session.get(MaintenanceTask, bad_id)
        task.processor_json = processor_json
        session.commit()
    assert enqueue_budget_tasks() == 1
    with session_maker() as session:
        valid = (
            session.query(MaintenanceTask).filter(MaintenanceTask.id != bad_id).one()
        )
        assert valid.get_processor().org_ids == [org_id]


def test_only_managed_enabled_or_pending_operations_are_enqueued(
    session_maker, budget_queue
):
    managed = budget_queue()
    pending_external = budget_queue(mode='external', enabled=False, pending=True)
    pending_legacy = budget_queue(mode='needs_adoption', enabled=False, pending=True)
    budget_queue(mode='external')
    budget_queue(mode='needs_adoption')
    budget_queue(enabled=False)
    assert enqueue_budget_tasks(batch_size=2) == 3
    assert set(pending_org_ids(session_maker)) == {
        managed,
        pending_external,
        pending_legacy,
    }
    with session_maker() as session:
        tasks = session.query(MaintenanceTask).all()
        assert sorted(len(task.get_processor().org_ids) for task in tasks) == [1, 2]
        assert all(task.delay == 0 for task in tasks)


def test_simultaneous_enqueue_does_not_duplicate_orgs(session_maker, budget_queue):
    org_ids = {budget_queue() for _ in range(3)}
    barrier = Barrier(2)

    def enqueue():
        barrier.wait(timeout=5)
        return enqueue_budget_tasks(batch_size=2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(enqueue) for _ in range(2)]
        assert sorted(future.result(timeout=10) for future in futures) == [0, 3]
    queued = pending_org_ids(session_maker)
    assert len(queued) == 3 and set(queued) == org_ids


def test_stale_cleanup_commits_even_when_no_org_is_eligible(
    session_maker, budget_queue
):
    org_id = budget_queue(mode='external')
    old_id = add_task(
        session_maker, [org_id], state=MaintenanceTaskStatus.WORKING, age_minutes=120
    )
    assert enqueue_budget_tasks() == 0
    with session_maker() as session:
        assert (
            session.get(MaintenanceTask, old_id).status == MaintenanceTaskStatus.ERROR
        )


@pytest.mark.parametrize('batch_size', [0, -1])
def test_invalid_batch_size_does_not_touch_queue(budget_queue, batch_size):
    with pytest.raises(ValueError, match='positive'):
        enqueue_budget_tasks(batch_size=batch_size)


def _budget_maintenance_tree() -> ast.Module:
    source = pathlib.Path(__file__).parent.parent.parent / 'run_budget_maintenance.py'
    return ast.parse(source.read_text())


def test_run_budget_maintenance_sets_immediate_task_delay() -> None:
    tree = _budget_maintenance_tree()
    maintenance_task_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == 'MaintenanceTask'
    ]

    assert maintenance_task_calls, 'run_budget_maintenance.py should enqueue tasks'
    assert any(
        keyword.arg == 'delay'
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == 0
        for call in maintenance_task_calls
        for keyword in call.keywords
    ), (
        'Budget maintenance tasks must set delay=0 for deployed DB schemas without '
        'a delay default'
    )
