import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from google.api_core.exceptions import ServiceUnavailable

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
)
from openhands.app_server.event_callback import trajectory_router


@pytest.mark.asyncio
@pytest.mark.parametrize('failed_write', ['put', 'advance'])
async def test_sdk_drains_into_cloud_with_existing_auth_scope(
    tmp_path, monkeypatch, failed_write
):
    archive = pytest.importorskip(
        'openhands.agent_server.persistence.trajectory_archive'
    )
    publisher = pytest.importorskip(
        'openhands.agent_server.persistence.trajectory_webhook'
    )
    journal_type = pytest.importorskip('openhands.sdk.observability.trajectory_journal')
    conversation = uuid4()
    journal = journal_type.Journal(tmp_path / 'spool', str(conversation))
    run = uuid4().hex
    store = archive.DirectoryStore(tmp_path / 'gcs')
    admission = archive.DirectoryStore(tmp_path / 'admissions')
    app = FastAPI()
    app.include_router(trajectory_router.router)
    sandbox = SimpleNamespace(id='sandbox', created_by_user_id='test-user')
    app.dependency_overrides[trajectory_router.valid_sandbox] = lambda: sandbox

    async def load_conversation(conversation_id, sandbox_record, *, allow_missing):
        assert sandbox_record is sandbox
        assert not allow_missing
        return AppConversationInfo(
            id=conversation_id, sandbox_id=sandbox.id, created_by_user_id='test-user'
        )

    monkeypatch.setattr(trajectory_router, 'sandbox_conversation', load_conversation)
    monkeypatch.setattr(
        trajectory_router,
        '_store',
        lambda _info, _capture, stream: admission if stream == 'admissions' else store,
    )
    slots = asyncio.Semaphore(1)
    monkeypatch.setattr(trajectory_router, '_archive_slots', slots)
    root = f'/webhooks/trajectories/{conversation}/{journal.instance_id}'

    class LoseFirstReply(httpx.ASGITransport):
        lost = False

        async def handle_async_request(self, request):
            response = await super().handle_async_request(request)
            if (
                not self.lost
                and request.method == 'POST'
                and response.status_code == 200
            ):
                self.lost = True
                raise httpx.ReadError('acknowledgment was lost')
            return response

    async with httpx.AsyncClient(
        transport=LoseFirstReply(app=app), base_url='http://test'
    ) as client:
        monkeypatch.delenv('OPENHANDS_TRAJECTORY_CLOUD_IDS', raising=False)
        assert (await client.get(f'{root}/journal')).status_code == 404
        monkeypatch.setenv('OPENHANDS_TRAJECTORY_CLOUD_IDS', str(conversation))
        async with slots:
            busy = await client.get(f'{root}/journal')
        assert busy.status_code == 503
        assert busy.headers['Retry-After'] == '2'
        response = await client.post(
            f'{root}/admissions/{run}', json={'origin': 'evaluation'}
        )
        assert response.status_code == 204
        assert admission.head(f'{run}.json') is not None
        journal.append('run_start', {}, run_id=run)

        def fail_write(*_args, **_kwargs):
            raise ServiceUnavailable('simulated GCS outage')

        with monkeypatch.context() as fault:
            fault.setattr(store, failed_write, fail_write)
            with pytest.raises(httpx.HTTPStatusError) as failure:
                await publisher._drain(client, f'{root}/journal', journal)
        assert failure.value.response.status_code == 503
        assert failure.value.response.headers['Retry-After'] == '2'
        assert (await client.get(f'{root}/journal')).json() == {'acked_through': 0}
        with pytest.raises(httpx.ReadError):
            await publisher._drain(client, f'{root}/journal', journal)
        await publisher._drain(client, f'{root}/journal', journal)
        journal.append('run_end', {'status': 'finished'}, run_id=run)
        await publisher._drain(client, f'{root}/journal', journal)
        assert (await client.get(f'{root}/journal')).json() == {'acked_through': 2}
        response = await client.post(f'{root}/journal?first=4', content=b'{}\n')
        assert response.status_code == 409

    head = store.head(f'shadow/v1/{conversation}/head.json')
    assert head is not None
    seal = archive.read_seal(store, archive.ObjectRef.model_validate_json(head.data))
    assert seal.runs == {run: 'finished'}
