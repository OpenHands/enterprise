"""Tests for the sandbox router's lifecycle routes over HTTP."""

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.app_server.sandbox.sandbox_router import (
    router as sandbox_router,
)
from openhands.app_server.sandbox.sandbox_router import (
    sandbox_service_dependency,
)
from openhands.app_server.utils.dependencies import check_session_api_key


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
