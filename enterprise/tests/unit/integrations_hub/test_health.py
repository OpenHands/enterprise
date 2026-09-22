from __future__ import annotations

import asyncio
import threading
import time

import httpx
import psycopg
from fastapi.testclient import TestClient

from integrations_hub import main as main_module
from integrations_hub.main import app
from integrations_hub.models import IntegrationSpec, ToolSpec
from integrations_hub.repository import Repository, repository


class ConfigEnvFixtureProtocol:
    def __call__(self, key: str, value: str | None) -> None: ...


def test_live_endpoint_does_not_depend_on_database(monkeypatch) -> None:
    monkeypatch.setattr(
        repository,
        "database_ready",
        lambda: (_ for _ in ()).throw(AssertionError("live must not check the DB")),
    )

    response = TestClient(app).get("/api/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "backend": "fastapi"}


def test_live_endpoint_stays_responsive_during_slow_tool_call(monkeypatch) -> None:
    """A slow connector must occupy a worker thread, not the ASGI event loop."""
    owner = "health-check@example.com"
    api_key = "cla_health_check_key"
    started = threading.Event()
    release = threading.Event()
    repository.save_key(owner, "agent", api_key)
    repository.save_integration(
        owner,
        IntegrationSpec(
            key="slow",
            name="Slow integration",
            kind="api",
            provider="mock",
            authStrategy="none",
            tools={"call": ToolSpec(name="call", executionMode="mock")},
        ),
    )

    def slow_invoke(*_args, **_kwargs):
        started.set()
        assert release.wait(timeout=5)
        return {"ok": True}

    monkeypatch.setattr(main_module, "invoke_enabled_tool", slow_invoke)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            slow_request = asyncio.create_task(
                client.post(
                    "/api/context/slow/call",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"scopes": [], "payload": {}},
                )
            )
            try:
                assert await asyncio.to_thread(started.wait, 1)
                started_at = time.monotonic()
                response = await client.get("/api/live")
                assert time.monotonic() - started_at < 0.2
                assert response.status_code == 200
            finally:
                release.set()
            assert (await slow_request).status_code == 200

    asyncio.run(exercise())


def test_ready_endpoint_requires_database(monkeypatch) -> None:
    monkeypatch.setattr(repository, "database_ready", lambda: False)

    response = TestClient(app).get("/api/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "Database is not ready."
    assert response.headers["x-request-id"]
    assert response.headers["x-error-id"]


def test_ready_endpoint_returns_success_when_database_is_available(monkeypatch) -> None:
    monkeypatch.setattr(repository, "database_ready", lambda: True)

    response = TestClient(app).get("/api/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "backend": "fastapi"}


def test_database_ready_checks_the_configured_database(
    config_env: ConfigEnvFixtureProtocol, monkeypatch
) -> None:
    config_env("INTHUB_POSTGRES_URL", "postgres://test")
    repo = Repository()
    queries: list[str] = []

    def fake_db_rows(query: str, _params: tuple[object, ...] = ()):
        queries.append(query)
        return [{"ready": 1}]

    monkeypatch.setattr(repo, "db_rows", fake_db_rows)

    assert repo.database_ready()
    assert queries == ["SELECT 1 AS ready"]


def test_database_ready_returns_false_without_database(
    config_env: ConfigEnvFixtureProtocol, monkeypatch
) -> None:
    config_env("INTHUB_POSTGRES_URL", None)
    repo = Repository()
    monkeypatch.setattr(
        repo,
        "db_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("missing database must not be queried")
        ),
    )

    assert not repo.database_ready()


def test_database_ready_returns_false_when_database_query_fails(
    config_env: ConfigEnvFixtureProtocol, monkeypatch
) -> None:
    config_env("INTHUB_POSTGRES_URL", "postgres://test")
    repo = Repository()
    monkeypatch.setattr(
        repo,
        "db_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            psycopg.OperationalError("database is unavailable")
        ),
    )

    assert not repo.database_ready()
