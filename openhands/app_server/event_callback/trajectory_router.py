import asyncio
import json
import os
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from google.api_core.exceptions import GoogleAPICallError
from pydantic import BaseModel

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
)
from openhands.app_server.event.google_cloud_event_service import (
    _get_shared_storage_client,
)
from openhands.app_server.event_callback.webhook_router import (
    sandbox_conversation,
    valid_sandbox,
)
from openhands.app_server.sandbox.sandbox_models import SandboxRecord
from openhands.app_server.trajectory_archive import GoogleCloudArchive

router = APIRouter(prefix='/webhooks/trajectories')


async def enrolled_conversation(
    conversation_id: UUID,
    sandbox: Annotated[SandboxRecord, Depends(valid_sandbox)],
) -> AppConversationInfo:
    enrolled = os.environ.get('OPENHANDS_TRAJECTORY_CLOUD_IDS', '').split(',')
    if str(conversation_id) not in enrolled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Trajectory capture is disabled')
    info = await sandbox_conversation(conversation_id, sandbox, allow_missing=False)
    if not info.created_by_user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Trajectory owner is missing')
    return info


Enrolled = Annotated[AppConversationInfo, Depends(enrolled_conversation)]
Stream = Literal['journal', 'witness']


def _store(
    info: AppConversationInfo, capture_id: UUID, stream: str
) -> GoogleCloudArchive:
    bucket = os.environ.get('FILE_STORE_PATH')
    if not bucket:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, 'Trajectory bucket is unavailable'
        )
    return GoogleCloudArchive(
        _get_shared_storage_client().bucket(bucket),
        f'users/{info.created_by_user_id}/trajectory-pilot/{info.id.hex}/{capture_id.hex}/{stream}',
    )


@router.get('/{conversation_id}/{capture_id}/{stream}')
async def get_watermark(
    capture_id: UUID, stream: Stream, info: Enrolled
) -> dict[str, int]:
    from openhands.agent_server.persistence.trajectory_batches import archive_through

    try:
        through = await asyncio.to_thread(
            archive_through, _store(info, capture_id, stream), str(info.id)
        )
    except (GoogleAPICallError, ValueError, OSError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, 'Trajectory archive is unavailable'
        ) from exc
    return {'acked_through': through}


@router.post('/{conversation_id}/{capture_id}/{stream}')
async def post_batch(
    request: Request,
    capture_id: UUID,
    stream: Stream,
    info: Enrolled,
    first: Annotated[int, Query(ge=1)],
) -> dict[str, int]:
    from openhands.agent_server.persistence.trajectory_archive import ArchiveGap
    from openhands.agent_server.persistence.trajectory_batches import (
        MAX_BATCH_BYTES,
        commit_batch,
    )

    body = bytearray()
    async for part in request.stream():
        if len(body) + len(part) > MAX_BATCH_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                'Trajectory batch is too large',
            )
        body.extend(part)
    try:
        through = await asyncio.to_thread(
            commit_batch,
            _store(info, capture_id, stream),
            str(info.id),
            first,
            bytes(body),
        )
    except ArchiveGap as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, 'Trajectory range conflicts'
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, 'Invalid trajectory batch'
        ) from exc
    except (GoogleAPICallError, OSError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, 'Unable to store trajectory batch'
        ) from exc
    return {'acked_through': through}


class RunAdmission(BaseModel):
    origin: Literal['interactive', 'automation', 'evaluation', 'unknown']


@router.post('/{conversation_id}/{capture_id}/admissions/{run_id}', status_code=204)
async def register_run(
    capture_id: UUID, run_id: UUID, info: Enrolled, body: RunAdmission
) -> Response:
    data = json.dumps(
        {'conversation_id': str(info.id), 'run_id': run_id.hex, 'origin': body.origin},
        sort_keys=True,
        separators=(',', ':'),
    ).encode()
    store = _store(info, capture_id, 'admissions')
    try:
        await asyncio.to_thread(store.put, f'{run_id.hex}.json', data)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, 'Trajectory admission conflicts'
        ) from exc
    except (GoogleAPICallError, OSError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, 'Unable to record trajectory admission'
        ) from exc
    return Response(status_code=204)
