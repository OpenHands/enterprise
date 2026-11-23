import asyncio
import os
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import httpx
import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from google.api_core.exceptions import ServiceUnavailable

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
)
from openhands.app_server.event.aws_event_service import AwsEventServiceInjector
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


@pytest.mark.asyncio
async def test_s3_watermark_distinguishes_a_missing_bucket_from_an_empty_archive(
    monkeypatch,
):
    pytest.importorskip('openhands.agent_server.persistence.trajectory_batches')
    info = AppConversationInfo(
        id=uuid4(), sandbox_id='sandbox', created_by_user_id='test-user'
    )
    event = AwsEventServiceInjector(
        bucket_name='tenant-minio', endpoint_url='http://configured-minio:9000'
    )
    monkeypatch.setattr(
        trajectory_router, 'get_global_config', lambda: SimpleNamespace(event=event)
    )
    s3 = Mock()
    s3_client = Mock(return_value=s3)
    monkeypatch.setattr(trajectory_router, '_get_shared_s3_client', s3_client)
    gcs_client = Mock(side_effect=AssertionError('S3 configuration used GCS'))
    monkeypatch.setattr(trajectory_router, '_get_shared_storage_client', gcs_client)
    app = FastAPI()
    app.include_router(trajectory_router.router)
    app.dependency_overrides[trajectory_router.enrolled_conversation] = lambda: info
    root = f'/webhooks/trajectories/{info.id}/{uuid4()}/journal'

    def missing(code):
        return ClientError(
            {'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': 404}},
            'GetObject',
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://test'
    ) as client:
        s3.get_object.side_effect = missing('NoSuchBucket')
        unavailable = await client.get(root)
        assert unavailable.status_code == 503
        assert unavailable.headers['Retry-After'] == '2'
        s3.get_object.side_effect = missing('NoSuchKey')
        assert (await client.get(root)).json() == {'acked_through': 0}
        assert s3.get_object.call_args.kwargs['Bucket'] == 'tenant-minio'
    s3_client.assert_called_with('http://configured-minio:9000')
    gcs_client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('versioned', [False, True])
async def test_sdk_upload_and_verified_read_through_minio(
    tmp_path, monkeypatch, versioned
):
    endpoint = os.environ.get('TEST_TRAJECTORY_S3_ENDPOINT')
    if not endpoint:
        pytest.skip('Set TEST_TRAJECTORY_S3_ENDPOINT to use a disposable MinIO server')
    archive = pytest.importorskip(
        'openhands.agent_server.persistence.trajectory_archive'
    )
    publisher = pytest.importorskip(
        'openhands.agent_server.persistence.trajectory_webhook'
    )
    journal_type = pytest.importorskip('openhands.sdk.observability.trajectory_journal')
    info = AppConversationInfo(
        id=uuid4(), sandbox_id='sandbox', created_by_user_id='test-user'
    )
    bucket = f'trajectory-test-{uuid4().hex}'
    event = AwsEventServiceInjector(bucket_name=bucket, endpoint_url=endpoint)
    monkeypatch.setattr(
        trajectory_router, 'get_global_config', lambda: SimpleNamespace(event=event)
    )
    monkeypatch.setattr(
        trajectory_router,
        '_get_shared_storage_client',
        Mock(side_effect=AssertionError('S3 configuration used GCS')),
    )
    app = FastAPI()
    app.include_router(trajectory_router.router)
    app.dependency_overrides[trajectory_router.enrolled_conversation] = lambda: info
    journal = journal_type.Journal(tmp_path / 'spool', str(info.id))
    capture = journal.instance_id
    stream = f'/webhooks/trajectories/{info.id}/{capture}/journal'
    run = uuid4().hex

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://test'
    ) as client:
        assert (await client.get(stream)).status_code == 503
        s3 = trajectory_router._get_shared_s3_client(endpoint)
        s3.create_bucket(Bucket=bucket)
        if versioned:
            s3.put_bucket_versioning(
                Bucket=bucket, VersioningConfiguration={'Status': 'Enabled'}
            )
        assert (await client.get(stream)).json() == {'acked_through': 0}
        admission = await client.post(
            f'/webhooks/trajectories/{info.id}/{capture}/admissions/{run}',
            json={'origin': 'evaluation'},
        )
        assert admission.status_code == 204
        journal.append('run_start', {}, run_id=run)
        assert await publisher._drain(client, stream, journal) == (1, True)
        first, _ = publisher.batch_after(journal, 0)
        duplicate = await client.post(stream, params={'first': 1}, content=first)
        assert duplicate.json() == {'acked_through': 1}
        journal.append('run_end', {'status': 'finished'}, run_id=run)
        assert await publisher._drain(client, stream, journal) == (2, True)
        assert await publisher._drain(client, stream, journal) == (2, True)

    store = trajectory_router._store(info, UUID(capture), 'journal')
    head = store.head(f'shadow/v1/{info.id}/head.json')
    assert head is not None
    seal = archive.read_seal(store, archive.ObjectRef.model_validate_json(head.data))
    assert seal.runs == {run: 'finished'}
    assert seal.segments[0].object.generation.startswith('version:') == versioned
    assert [item.record.sequence for item in archive.read_segments(store, seal)] == [
        1,
        2,
    ]

    store.advance('cas-test.json', None, b'first')
    previous = store.head('cas-test.json')
    store.advance('cas-test.json', previous, b'second')
    with pytest.raises(OSError, match='head changed'):
        store.advance('cas-test.json', previous, b'stale')
    assert store.head('cas-test.json').data == b'second'
