"""Durable activation, queueing and crash recovery of exact-key retirement."""

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

import run_maintenance_tasks
from server.maintenance_task_processor.credential_retirement_processor import (
    enqueue_credential_retirement_tasks,
)
from storage.llm_credential_operation import LlmCredentialOperation
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus


@pytest.fixture
def retirement_records(create_org, session_maker):
    def create(*, org_id=None, activated=True, retired=False):
        org_id = org_id or create_org().id
        old_key, new_key = f'sk-{uuid4().hex}', f'sk-{uuid4().hex}'
        with session_maker() as session:
            operation = LlmCredentialOperation(
                org_id=org_id,
                user_id=str(uuid4()),
                request_hash=uuid4().hex,
                key_hash=hashlib.sha256(new_key.encode()).hexdigest(),
                replaces_key_hash=hashlib.sha256(old_key.encode()).hexdigest(),
                payload={'key': new_key, '_retire_key': old_key},
                status='issued',
                activated_at=datetime.now(UTC) if activated else None,
                retired_at=datetime.now(UTC) if retired else None,
            )
            session.add(operation)
            session.commit()
            session.refresh(operation)
            session.expunge(operation)
            return operation

    with patch(
        'server.maintenance_task_processor.credential_retirement_processor.session_maker',
        session_maker,
    ):
        yield create


def test_enqueue_requires_activation_and_deduplicates_per_org(
    retirement_records, session_maker
):
    first = retirement_records()
    retirement_records(org_id=first.org_id)
    retirement_records(activated=False)
    retirement_records(retired=True)
    assert enqueue_credential_retirement_tasks() == 1
    second = retirement_records()
    assert enqueue_credential_retirement_tasks() == 1
    assert enqueue_credential_retirement_tasks() == 0
    with session_maker() as session:
        tasks = session.scalars(select(MaintenanceTask)).all()
        assert {task.get_processor().org_id for task in tasks} == {
            first.org_id,
            second.org_id,
        }


@pytest.mark.parametrize('field', ['activated_at', 'retired_at'])
def test_committed_credential_receipts_are_immutable(
    retirement_records, session_maker, field
):
    record = retirement_records(retired=True)
    with session_maker() as session:
        operation = session.get(LlmCredentialOperation, record.id)
        setattr(operation, field, None)
        with pytest.raises(DBAPIError, match='receipts are immutable'):
            session.commit()


def test_concurrent_retirement_enqueue_is_deduplicated(
    retirement_records, session_maker
):
    retirement_records()
    retirement_records()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: enqueue_credential_retirement_tasks(), range(2))
        )
    assert sum(results) == 2
    with session_maker() as session:
        assert len(session.scalars(select(MaintenanceTask)).all()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['response_loss', 'cancellation'])
async def test_real_worker_recovers_retirement_after_remote_effect(
    retirement_records, session_maker, async_session_maker, failure
):
    operation = retirement_records()
    reached_delete = asyncio.Event()
    deleted = False
    writes = []

    async def handle(request):
        nonlocal deleted
        if request.url.path == '/key/info':
            return (
                httpx.Response(404)
                if deleted
                else httpx.Response(
                    200,
                    json={
                        'info': {
                            'team_id': str(operation.org_id),
                            'user_id': operation.user_id,
                        }
                    },
                )
            )
        assert request.url.path == '/key/delete'
        writes.append(request)
        deleted = True
        reached_delete.set()
        if failure == 'response_loss':
            raise httpx.ReadTimeout('Lost response', request=request)
        await asyncio.Event().wait()

    original_client = httpx.AsyncClient
    with (
        patch('storage.database.a_session_maker', async_session_maker),
        patch('run_maintenance_tasks.session_maker', session_maker),
        patch('storage.lite_llm_manager.LITE_LLM_API_KEY', 'test-master'),
        patch('storage.lite_llm_manager.LITE_LLM_API_URL', 'http://litellm.test'),
        patch(
            'storage.lite_llm_manager.httpx.AsyncClient',
            new=lambda **kwargs: original_client(
                transport=httpx.MockTransport(handle), **kwargs
            ),
        ),
    ):
        assert enqueue_credential_retirement_tasks() == 1
        worker = asyncio.create_task(run_maintenance_tasks.run_tasks())
        await asyncio.wait_for(reached_delete.wait(), timeout=5)
        if failure == 'cancellation':
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
            with session_maker() as session:
                task = session.scalar(select(MaintenanceTask))
                assert task.status == MaintenanceTaskStatus.WORKING
                task.started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
                    hours=2
                )
                session.commit()
        else:
            assert await worker == 1
        with session_maker() as session:
            assert session.get(LlmCredentialOperation, operation.id).retired_at is None
        assert enqueue_credential_retirement_tasks() == 1
        assert await run_maintenance_tasks.run_tasks() == 0
        assert enqueue_credential_retirement_tasks() == 0
    assert len(writes) == 1
    with session_maker() as session:
        assert session.get(LlmCredentialOperation, operation.id).retired_at is not None
        tasks = session.scalars(
            select(MaintenanceTask).order_by(MaintenanceTask.id)
        ).all()
        assert [task.status for task in tasks] == [
            MaintenanceTaskStatus.ERROR,
            MaintenanceTaskStatus.COMPLETED,
        ]
