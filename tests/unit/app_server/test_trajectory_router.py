from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
)
from openhands.app_server.event_callback import trajectory_router


@pytest.mark.asyncio
async def test_sdk_drains_into_cloud_with_existing_auth_scope(tmp_path, monkeypatch):
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
        response = await client.post(
            f'{root}/admissions/{run}', json={'origin': 'evaluation'}
        )
        assert response.status_code == 204
        assert admission.head(f'{run}.json') is not None
        journal.append('run_start', {}, run_id=run)
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
