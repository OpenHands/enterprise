from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.sandbox_router import (
    router as sandbox_router,
)
from openhands.app_server.sandbox.sandbox_router import (
    sandbox_service_dependency,
)
from openhands.app_server.utils.dependencies import check_session_api_key


@pytest.fixture
def sandbox_service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client(sandbox_service: AsyncMock) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[check_session_api_key] = lambda: None
    app.dependency_overrides[sandbox_service_dependency.dependency] = (
        lambda: sandbox_service
    )
    app.include_router(sandbox_router, prefix='/api/v1')
    return TestClient(app)


def test_resume_sandbox_success(client: TestClient, sandbox_service: AsyncMock):
    sandbox_service.resume_sandbox.return_value = True

    response = client.post('/api/v1/sandboxes/sandbox-1/resume')

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {'success': True}


def test_resume_sandbox_missing(client: TestClient, sandbox_service: AsyncMock):
    sandbox_service.resume_sandbox.return_value = False

    response = client.post('/api/v1/sandboxes/sandbox-1/resume')

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.parametrize(
    ('error_status', 'detail'),
    [
        (
            status.HTTP_409_CONFLICT,
            {
                'code': 'runtime_not_resumable',
                'current_status': 'terminating',
            },
        ),
        (status.HTTP_502_BAD_GATEWAY, 'runtime_resume_failed'),
    ],
)
def test_resume_sandbox_preserves_structured_errors(
    client: TestClient,
    sandbox_service: AsyncMock,
    error_status: int,
    detail: str | dict[str, str],
):
    sandbox_service.resume_sandbox.side_effect = SandboxError(
        status_code=error_status, detail=detail
    )

    response = client.post('/api/v1/sandboxes/sandbox-1/resume')

    assert response.status_code == error_status
    assert response.json() == {'detail': detail}


def test_resume_sandbox_documents_response_semantics(client: TestClient):
    responses = client.get('/openapi.json').json()['paths'][
        '/api/v1/sandboxes/{sandbox_id}/resume'
    ]['post']['responses']

    assert {'200', '404', '409', '502'} <= responses.keys()
