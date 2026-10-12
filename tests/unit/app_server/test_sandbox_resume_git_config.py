from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    ExposedUrl,
    SandboxInfo,
    SandboxStatus,
)
from openhands.app_server.sandbox.sandbox_router import (
    _restore_git_user_settings_after_resume,
    resume_sandbox,
)
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo


@pytest.mark.asyncio
async def test_resume_schedules_git_identity_restore():
    events = []
    user_context = AsyncMock()
    user_context.get_user_info.return_value = MagicMock(
        git_user_name='Resume Test User',
        git_user_email='resume-test@example.com',
    )
    sandbox_service = AsyncMock()
    sandbox_service.resume_sandbox.return_value = True
    db_session = AsyncMock()

    async def commit():
        events.append('commit')

    def create_task(coroutine):
        events.append('schedule')
        coroutine.close()
        return MagicMock()

    db_session.commit.side_effect = commit
    with (
        patch(
            'openhands.app_server.sandbox.sandbox_router._restore_git_user_settings_after_resume',
            new=AsyncMock(),
        ) as restore,
        patch(
            'openhands.app_server.sandbox.sandbox_router.asyncio.create_task',
            side_effect=create_task,
        ) as schedule,
    ):
        await resume_sandbox(
            'sandbox-1',
            user_context,
            sandbox_service,
            db_session,
        )

    assert events == ['commit', 'schedule']
    db_session.commit.assert_awaited_once_with()
    restore.assert_called_once_with(
        'sandbox-1', 'Resume Test User', 'resume-test@example.com'
    )
    schedule.assert_called_once()


@pytest.mark.asyncio
async def test_resume_without_saved_git_identity_does_not_schedule_restore():
    user_context = AsyncMock()
    user_context.get_user_info.return_value = MagicMock(
        git_user_name=None,
        git_user_email=None,
    )
    sandbox_service = AsyncMock()
    sandbox_service.resume_sandbox.return_value = True
    db_session = AsyncMock()

    with patch(
        'openhands.app_server.sandbox.sandbox_router.asyncio.create_task'
    ) as schedule:
        await resume_sandbox(
            'sandbox-1',
            user_context,
            sandbox_service,
            db_session,
        )

    db_session.commit.assert_awaited_once_with()
    schedule.assert_not_called()


@pytest.mark.asyncio
async def test_resume_missing_sandbox_is_404_without_commit_or_restore():
    sandbox_service = AsyncMock()
    sandbox_service.resume_sandbox.return_value = False
    db_session = AsyncMock()

    with (
        patch(
            'openhands.app_server.sandbox.sandbox_router.asyncio.create_task'
        ) as schedule,
        pytest.raises(HTTPException) as exc_info,
    ):
        await resume_sandbox('sandbox-1', AsyncMock(), sandbox_service, db_session)

    assert exc_info.value.status_code == 404
    db_session.commit.assert_not_awaited()
    schedule.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('status_code', 'detail'),
    [
        (
            409,
            {
                'code': 'runtime_not_resumable',
                'current_status': 'paused',
                'message': 'Runtime could not resume because status was paused',
            },
        ),
        (502, 'Runtime API resume failed for sandbox sandbox-1'),
    ],
)
async def test_resume_preserves_service_error_semantics(status_code, detail):
    sandbox_service = AsyncMock()
    sandbox_service.resume_sandbox.side_effect = SandboxError(
        status_code=status_code, detail=detail
    )
    db_session = AsyncMock()

    with (
        patch(
            'openhands.app_server.sandbox.sandbox_router.asyncio.create_task'
        ) as schedule,
        pytest.raises(SandboxError) as exc_info,
    ):
        await resume_sandbox('sandbox-1', AsyncMock(), sandbox_service, db_session)

    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == detail
    db_session.commit.assert_not_awaited()
    schedule.assert_not_called()


@pytest.mark.asyncio
async def test_restore_git_identity_uses_resumed_sandbox():
    sandbox = SandboxInfo(
        id='sandbox-1',
        created_by_user_id='user-1',
        sandbox_spec_id='spec-1',
        status=SandboxStatus.RUNNING,
        session_api_key='new-session-key',
        exposed_urls=[
            ExposedUrl(name=AGENT_SERVER, url='https://agent.example', port=60000)
        ],
    )
    sandbox_service = AsyncMock()
    sandbox_service.wait_for_sandbox_running.return_value = sandbox
    sandbox_service._get_agent_server_url = MagicMock(
        return_value='https://agent.example'
    )
    sandbox_spec_service = AsyncMock()
    sandbox_spec_service.get_sandbox_spec.return_value = SandboxSpecInfo(
        id='spec-1',
        command=None,
        working_dir='/workspace/project',
    )
    workspace = MagicMock()
    sandbox_service_context = AsyncMock()
    sandbox_service_context.__aenter__.return_value = sandbox_service
    sandbox_spec_service_context = AsyncMock()
    sandbox_spec_service_context.__aenter__.return_value = sandbox_spec_service
    httpx_client = AsyncMock()
    httpx_client_context = AsyncMock()
    httpx_client_context.__aenter__.return_value = httpx_client

    with (
        patch(
            'openhands.app_server.sandbox.sandbox_router.get_sandbox_service',
            return_value=sandbox_service_context,
        ) as get_sandbox_service,
        patch(
            'openhands.app_server.sandbox.sandbox_router.get_sandbox_spec_service',
            return_value=sandbox_spec_service_context,
        ) as get_sandbox_spec_service,
        patch(
            'openhands.app_server.sandbox.sandbox_router.get_httpx_client',
            return_value=httpx_client_context,
        ) as get_httpx_client,
        patch(
            'openhands.app_server.sandbox.sandbox_router.AsyncRemoteWorkspace',
            return_value=workspace,
        ) as workspace_type,
        patch(
            'openhands.app_server.sandbox.sandbox_router.configure_git_user_settings',
            new=AsyncMock(),
        ) as configure,
    ):
        await _restore_git_user_settings_after_resume(
            'sandbox-1',
            'Resume Test User',
            'resume-test@example.com',
        )

    state = get_sandbox_service.call_args.args[0]
    get_sandbox_spec_service.assert_called_once_with(state)
    get_httpx_client.assert_called_once_with(state)
    sandbox_service.wait_for_sandbox_running.assert_awaited_once_with(
        'sandbox-1', httpx_client=httpx_client
    )
    workspace_type.assert_called_once_with(
        host='https://agent.example',
        api_key='new-session-key',
        working_dir='/workspace/project',
    )
    configure.assert_awaited_once_with(
        workspace,
        'Resume Test User',
        'resume-test@example.com',
    )
