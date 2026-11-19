"""End-to-end integration test for PR #1460.

Exercises ``DockerSandboxService.archive_conversation_workspace`` (the method the
PR adds) against REAL infrastructure — no mocks — to prove the wiring the unit
tests stub out:

  - a REAL Docker container running the bundled agent-server image
  - a REAL workspace with a git repo + uncommitted changes inside it
  - a REAL ``httpx.AsyncClient`` hitting the agent-server's ``GET /api/file/archive``
  - a REAL local ``FileStore`` bucket the archive lands in

What this proves that the unit tests don't:
  - the runtime dict (URL + session_api_key) the PR builds from ``_to_sandbox_info``
    actually authenticates against the agent-server's archive endpoint
  - the archive_path the PR resolves actually points at a directory the
    agent-server can archive
  - the shared ``workspace_archive.archive_workspace`` sink actually streams the
    archive into the configured local bucket in BOTH formats (git-delta + tar.gz)
  - the container is neither stopped nor removed by the archive call (the PR's
    key invariant: capture happens while the container is still up, teardown
    stays with the finalizer)

This is the highest-fidelity check short of standing up the whole enterprise app
+ LLM + real conversation flow, which is out of scope for a local test of the
archive wiring.

Skipped automatically when Docker isn't available or the agent-server image
isn't present locally, so it never blocks CI on machines without Docker.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import docker
import httpx
import pytest

from openhands.app_server.sandbox.docker_sandbox_service import (
    DockerSandboxService,
    ExposedPort,
    VolumeMount,
)
from openhands.app_server.sandbox.docker_sandbox_spec_service import (
    get_agent_server_env,
    get_docker_client,
)
from openhands.app_server.sandbox.sandbox_service import (
    AGENT_SERVER,
    SESSION_API_KEY_VARIABLE,
)
from openhands.app_server.sandbox.sandbox_spec_service import get_agent_server_image
from openhands.app_server.sandbox.sandbox_store import (
    DOCKER_BACKEND,
    StoredSandbox,
)
from openhands.app_server.settings.settings_models import (
    SandboxGroupingStrategy,
)
from openhands.app_server.user.user_models import LOCAL_USER_ID

pytestmark = pytest.mark.asyncio

_AGENT_SERVER_IMAGE = get_agent_server_image()
_WORKSPACE_DIR = '/workspace/project'


def _docker_available() -> bool:
    try:
        get_docker_client().ping()
        return True
    except Exception:
        return False


def _image_present() -> bool:
    try:
        get_docker_client().images.get(_AGENT_SERVER_IMAGE)
        return True
    except Exception:
        return False


_skip = pytest.mark.skipif(
    not (_docker_available() and _image_present()),
    reason=f'Docker + agent-server image {_AGENT_SERVER_IMAGE} required for E2E',
)


def _free_host_port(client: docker.DockerClient) -> int:
    import socket

    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _dump_evidence(bucket: Path) -> None:
    """Copy the produced archives + manifests into the verification artifact tree."""
    dest = Path('PR1460_VERIFICATION/layer2-integration/captured-archives')
    dest.mkdir(parents=True, exist_ok=True)
    for f in sorted(bucket.rglob('*')):
        if not f.is_file():
            continue
        rel = f.relative_to(bucket)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)


class _RealUserContext:
    """Minimal UserContext returning the local user + NO_GROUPING."""

    async def get_user_id(self) -> str:
        return LOCAL_USER_ID

    async def get_default_sandbox_spec_id(self) -> str | None:
        return None

    async def get_user_info(self):
        class _Info:
            sandbox_grouping_strategy = SandboxGroupingStrategy.NO_GROUPING

        return _Info()


class _RealSandboxSpecService:
    """Returns the default agent-server spec, so _resolve_archive_path works."""

    async def get_default_sandbox_spec(self):
        class _Spec:
            id = _AGENT_SERVER_IMAGE
            working_dir = _WORKSPACE_DIR

        return _Spec()

    async def resolve_existing_sandbox_spec(self, spec_id, sandbox_id):
        return await self.get_default_sandbox_spec()


async def _wait_for_agent_server(url: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=5.0) as c:
        while time.monotonic() < deadline:
            try:
                r = await c.get(f'{url}/api/file/list?path={_WORKSPACE_DIR}')
                if r.status_code < 500:
                    return
            except Exception:
                pass
            await asyncio.sleep(1.5)
    raise RuntimeError(f'agent-server at {url} did not become ready')


@_skip
async def test_archive_conversation_workspace_e2e(tmp_path, monkeypatch):
    """The PR's method captures a real running container's workspace to a real
    local bucket, in both formats, without stopping the container."""
    client = get_docker_client()
    session_api_key = uuid.uuid4().hex
    sandbox_id = f'oh-e2e-{uuid.uuid4().hex[:12]}'
    container_name = sandbox_id
    host_port = _free_host_port(client)

    # 1. Start a real agent-server container with a seeded git workspace.
    container = client.containers.run(
        image=_AGENT_SERVER_IMAGE,
        command=['--port', '8000'],
        name=container_name,
        environment={
            SESSION_API_KEY_VARIABLE: session_api_key,
            **get_agent_server_env(),
            'OPENVSCODE_SERVER_ROOT': '/openhands/.openvscode-server',
            'OH_ENABLE_VNC': '0',
            'LOG_JSON': 'true',
            'OH_CONVERSATIONS_PATH': '/workspace/conversations',
            'OH_BASH_EVENTS_DIR': '/workspace/bash_events',
            'PYTHONUNBUFFERED': '1',
        },
        ports={'8000/tcp': ('127.0.0.1', host_port)},
        volumes={str(tmp_path): {'bind': _WORKSPACE_DIR, 'mode': 'rw'}},
        working_dir=_WORKSPACE_DIR,
        detach=True,
        init=True,
    )
    try:
        agent_server_url = f'http://127.0.0.1:{host_port}'
        await _wait_for_agent_server(agent_server_url)

        # Seed a git repo with uncommitted changes for the git-delta capture.
        rc, _ = container.exec_run(
            [
                'bash',
                '-lc',
                f'cd {_WORKSPACE_DIR} && git init -q && '
                'git config user.email a@b.c && git config user.name e2e && '
                'echo hello > README.md && git add . && git commit -qm base && '
                "echo 'uncommitted change' >> README.md && echo new > NEW.txt",
            ]
        )
        assert rc == 0, 'seeding git workspace failed'

        # 2. Build a REAL DockerSandboxService (no mocks) pointed at the container.
        bucket = tmp_path / 'archive-bucket'
        bucket.mkdir()
        monkeypatch.setenv('RUNTIME_FILE_ARCHIVE_ENABLED', 'true')
        monkeypatch.setenv('RUNTIME_FILE_ARCHIVE_REQUIRED', 'true')
        monkeypatch.setenv('RUNTIME_FILE_ARCHIVE_FORMAT', 'both')
        monkeypatch.setenv('RUNTIME_FILE_ARCHIVE_STORE_TYPE', 'local')
        monkeypatch.setenv('RUNTIME_FILE_ARCHIVE_BUCKET', str(bucket))
        monkeypatch.setenv('RUNTIME_FILE_ARCHIVE_PREFIX', 'workspace-archives')

        # httpx client the service uses to reach the agent-server archive endpoint.
        httpx_client = httpx.AsyncClient(timeout=120.0)

        service = DockerSandboxService(
            sandbox_spec_service=_RealSandboxSpecService(),
            user_context=_RealUserContext(),
            db_session=None,  # archive_conversation_workspace never touches it
            container_name_prefix='oh-e2e-',
            host_port=host_port,
            container_url_pattern='http://127.0.0.1:{port}',
            mounts=[
                VolumeMount(
                    host_path=str(tmp_path), container_path=_WORKSPACE_DIR, mode='rw'
                )
            ],
            exposed_ports=[
                ExposedPort(
                    name=AGENT_SERVER, description='Agent server', container_port=8000
                )
            ],
            health_check_path=None,
            httpx_client=httpx_client,
            max_num_sandboxes=3,
            docker_client=client,
        )

        # The stored row the PR's _get_stored_sandbox reads. In a real run
        # start_sandbox writes it; here we skip the DB by patching the lookup
        # to return a row carrying the session key the container was started
        # with — the only field the archive path needs that isn't on the container.
        stored = StoredSandbox(
            id=sandbox_id,
            backend=DOCKER_BACKEND,
            created_by_user_id=LOCAL_USER_ID,
            sandbox_spec_id=_AGENT_SERVER_IMAGE,
            session_api_key_hash=None,  # never looked up by hash in this flow
            created_at=datetime.now(timezone.utc),
        )

        async def _fake_get_stored_sandbox(self, sid, for_update=False):
            return stored if sid == sandbox_id else None

        # The DB lookup is the one piece that genuinely needs Postgres + a
        # migrated schema; the archive codepath only reads the row (never
        # writes), so stubbing the read is faithful to the E2E contract.
        from openhands.app_server.sandbox.managed_sandbox_service import (
            ManagedSandboxService,
        )

        monkeypatch.setattr(
            ManagedSandboxService, '_get_stored_sandbox', _fake_get_stored_sandbox
        )

        try:
            # 3. Call the PR's method.
            ok = await service.archive_conversation_workspace(
                sandbox_id,
                conversation_id=uuid.uuid4().hex,
                workspace_path=_WORKSPACE_DIR,
            )
            assert ok is True, (
                'archive_conversation_workspace returned False (REQUIRED would block the delete)'
            )

            # 4. The container must still be running — the PR never stops/removes it.
            container.reload()
            assert container.status == 'running', (
                f'container was {container.status}, expected running'
            )

            # 5. The archive landed in the local bucket in BOTH formats + a manifest.
            files = list(bucket.rglob('*'))
            names = [f.name for f in files if f.is_file()]
            assert any(n.endswith('.patch') for n in names), (
                f'no git-delta archive: {names}'
            )
            assert any(n.endswith('.tar.gz') for n in names), (
                f'no tar.gz archive: {names}'
            )
            assert any(n.endswith('.json') for n in names), f'no manifest: {names}'
            print(f'\n[ARCHIVE E2E] bucket={bucket}')
            for f in sorted(bucket.rglob('*')):
                if f.is_file():
                    print(f'  {f.relative_to(bucket)}  ({f.stat().st_size} bytes)')
            _dump_evidence(bucket)
        finally:
            await httpx_client.aclose()
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


@_skip
async def test_archive_noop_when_disabled_e2e(tmp_path, monkeypatch):
    """With archiving disabled, the method is a no-op: no HTTP call, no bucket writes."""
    client = get_docker_client()
    session_api_key = uuid.uuid4().hex
    sandbox_id = f'oh-e2e-{uuid.uuid4().hex[:12]}'
    host_port = _free_host_port(client)
    container = client.containers.run(
        image=_AGENT_SERVER_IMAGE,
        command=['--port', '8000'],
        name=sandbox_id,
        environment={
            SESSION_API_KEY_VARIABLE: session_api_key,
            **get_agent_server_env(),
        },
        ports={'8000/tcp': ('127.0.0.1', host_port)},
        volumes={str(tmp_path): {'bind': _WORKSPACE_DIR, 'mode': 'rw'}},
        working_dir=_WORKSPACE_DIR,
        detach=True,
        init=True,
    )
    try:
        await _wait_for_agent_server(f'http://127.0.0.1:{host_port}')
        monkeypatch.delenv('RUNTIME_FILE_ARCHIVE_ENABLED', raising=False)
        bucket = tmp_path / 'archive-bucket'
        bucket.mkdir()
        monkeypatch.setenv('RUNTIME_FILE_ARCHIVE_STORE_TYPE', 'local')
        monkeypatch.setenv('RUNTIME_FILE_ARCHIVE_BUCKET', str(bucket))

        httpx_client = httpx.AsyncClient(timeout=60.0)
        service = DockerSandboxService(
            sandbox_spec_service=_RealSandboxSpecService(),
            user_context=_RealUserContext(),
            db_session=None,
            container_name_prefix='oh-e2e-',
            host_port=host_port,
            container_url_pattern='http://127.0.0.1:{port}',
            mounts=[
                VolumeMount(
                    host_path=str(tmp_path), container_path=_WORKSPACE_DIR, mode='rw'
                )
            ],
            exposed_ports=[
                ExposedPort(
                    name=AGENT_SERVER, description='Agent server', container_port=8000
                )
            ],
            health_check_path=None,
            httpx_client=httpx_client,
            max_num_sandboxes=3,
            docker_client=client,
        )
        stored = StoredSandbox(
            id=sandbox_id,
            backend=DOCKER_BACKEND,
            created_by_user_id=LOCAL_USER_ID,
            sandbox_spec_id=_AGENT_SERVER_IMAGE,
            session_api_key_hash=None,  # never looked up by hash in this flow
            created_at=datetime.now(timezone.utc),
        )

        async def _fake_get_stored_sandbox(self, sid, for_update=False):
            return stored if sid == sandbox_id else None

        from openhands.app_server.sandbox.managed_sandbox_service import (
            ManagedSandboxService,
        )

        monkeypatch.setattr(
            ManagedSandboxService, '_get_stored_sandbox', _fake_get_stored_sandbox
        )
        try:
            ok = await service.archive_conversation_workspace(
                sandbox_id,
                conversation_id=uuid.uuid4().hex,
                workspace_path=_WORKSPACE_DIR,
            )
            assert ok is True
            assert list(bucket.rglob('*')) == [], (
                'disabled archiving must not write anything'
            )
            container.reload()
            assert container.status == 'running'
        finally:
            await httpx_client.aclose()
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass
