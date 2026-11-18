"""Tests for the sandbox router's lifecycle routes over HTTP."""

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.app_server.sandbox.sandbox_router import (
    router as sandbox_router,
)
from openhands.app_server.sandbox.sandbox_router import (
    sandbox_service_dependency,
    user_context_dependency,
)
from openhands.app_server.utils.dependencies import check_session_api_key
from server.licensing.limits import FirstSuperAdminOnlyError


def _build_app(sandbox_service: AsyncMock) -> FastAPI:
    """Mount the real sandbox router with the auth and service layers mocked."""
    app = FastAPI()
    app.dependency_overrides[check_session_api_key] = lambda: None
    app.dependency_overrides[sandbox_service_dependency.dependency] = (
        lambda: sandbox_service
    )
    app.include_router(sandbox_router, prefix='/api/v1')
    return app


class TestDeleteSandbox:
    def test_deletes_the_sandbox_named_in_the_path(self):
        """DELETE /api/v1/sandboxes/{sandbox_id} needs no ?sandbox_id= query.

        Regression test for OHE-3687: the route path and the handler argument
        must share a name, otherwise FastAPI demands the id as a query
        parameter and answers 422.
        """
        sandbox_service = AsyncMock()
        sandbox_service.delete_sandbox = AsyncMock(return_value=True)
        client = TestClient(_build_app(sandbox_service))

        response = client.delete('/api/v1/sandboxes/sb-123')

        assert response.status_code == 200
        sandbox_service.delete_sandbox.assert_awaited_once_with('sb-123')


class TestStartSandbox:
    def test_refuses_a_sandbox_the_license_does_not_allow(self):
        """POST /api/v1/sandboxes answers 402 and starts no sandbox.

        A client that starts a sandbox starts its conversations on the sandbox
        itself, so the license's limit on conversations applies here too. The
        automation service reads ``error`` to mark the run skipped.
        """
        sandbox_service = AsyncMock()
        user_context = AsyncMock()
        user_context.get_user_id.return_value = 'user-123'
        app = _build_app(sandbox_service)
        app.dependency_overrides[user_context_dependency.dependency] = (
            lambda: user_context
        )
        check = AsyncMock(side_effect=FirstSuperAdminOnlyError())

        with patch('server.licensing.limits.check_can_start_conversation', check):
            response = TestClient(app).post('/api/v1/sandboxes')

        assert response.status_code == 402
        assert response.json() == {
            'detail': {
                'error': 'LICENSE_LIMIT_REACHED',
                'message': (
                    'Without a valid license, only the first Super Admin can '
                    'start conversations.'
                ),
            }
        }
        check.assert_awaited_once_with('user-123')
        sandbox_service.start_sandbox.assert_not_awaited()
