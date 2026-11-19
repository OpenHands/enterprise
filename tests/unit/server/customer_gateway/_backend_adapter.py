"""Adapter: real CloudSandboxBackend.start_conversation (automation-reference)
-> real HTTP request body it builds and sends -> real
LiveStatusAppConversationService.start_app_conversation() (the same real
service instance the other tests in this module build) -> real
customer_gateway_hooks.bind_start_request.

Bounded scope: this file wires the real backend method and
its real request/response serialization to the existing real service
fixture. It does NOT stand up the full FastAPI app/router/DI stack, and the
GET start-task/GET-conversation follow-up calls return minimal fixture
metadata (only the fields CloudSandboxBackend.start_conversation itself
reads: ``id``, ``status``, ``detail``, ``app_conversation_id``, and for the
conversation GET: ``launched_agent_profile`` / ``additional_repositories``
presence) rather than exercising AppConversation's full UI serialization.
Labeled explicitly: HTTP transport and the GET read-back are mocked I/O; the
POST handler itself genuinely parses the backend's real JSON body into a
real ``AppConversationStartRequest`` and drives the real service.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

# ``openhands`` is a namespace package: once anything (e.g. openhands.sdk)
# has imported it, appending to sys.path no longer extends its __path__, so
# the automation source checkout's ``openhands/automation`` submodule must
# be added to the already-created namespace package's search path directly.
import openhands  # noqa: E402

# AUTOMATION_SOURCE_DIR: path to an importable checkout of the automation
# source tree (e.g. a tagged clone of OpenHands/automation matching the
# deployed image, or a reference tree provided for this session). Must
# contain an ``openhands/automation`` package. No default: the caller must
# set it explicitly so this suite never silently imports an unintended tree.
#
# This adapter (and the three cross-repo modules that import it:
# test_automation_backend_real_launch.py, test_automation_run_lifecycle.py,
# test_automation_dispatcher_caller_linkage.py) is the ONLY place that
# requires AUTOMATION_SOURCE_DIR. Skip cleanly -- rather than raising -- when
# it is unset, so `uv run pytest ./tests/unit` (per AGENTS.md) still collects
# and runs the rest of the suite from a plain `uv sync --all-groups` checkout.
# CI provisions AUTOMATION_SOURCE_DIR explicitly and keeps these three
# modules (and their provenance enforcement) running there.
AUTOMATION_SOURCE_DIR = os.environ.get('AUTOMATION_SOURCE_DIR', '')
if not AUTOMATION_SOURCE_DIR:
    pytest.skip(
        'AUTOMATION_SOURCE_DIR is not set. Point it at a checkout containing '
        'openhands/automation to run test_automation_backend_real_launch.py, '
        'test_automation_run_lifecycle.py and '
        'test_automation_dispatcher_caller_linkage.py.',
        allow_module_level=True,
    )
_AUTOMATION_SOURCE_OPENHANDS_PATH = os.path.join(AUTOMATION_SOURCE_DIR, 'openhands')
if _AUTOMATION_SOURCE_OPENHANDS_PATH not in openhands.__path__:
    openhands.__path__.append(_AUTOMATION_SOURCE_OPENHANDS_PATH)

# AUTOMATION_CAPTURE_DIR: OPTIONAL path to a separately captured, exact
# deployed openhands-automation==1.32.1 source snapshot, used only as an
# EXTRA, redundant provenance cross-check (assert_real_cloud_backend_
# provenance below) -- never imported. Not required to run or trust this
# suite: automation_source_provenance.json (shipped alongside this file,
# sanitized -- revision + sha256 hashes only, no source code, no private
# capture) already supplies the independently-verifiable expected hashes,
# so a reviewer with nothing but a public clone of
# https://github.com/OpenHands/automation at the pinned revision can run
# and trust this guard with AUTOMATION_SOURCE_DIR alone.
AUTOMATION_CAPTURE_DIR = os.environ.get('AUTOMATION_CAPTURE_DIR', '')

from openhands.app_server.app_conversation.app_conversation_models import (  # noqa: E402,E501
    AppConversationStartRequest,
)
from openhands.app_server.app_conversation.live_status_app_conversation_service import (  # noqa: E402,E501
    LiveStatusAppConversationService,
)

APP_CONVERSATIONS_PATH = '/api/v1/app-conversations'


def make_sandbox_agent_server_echo_response(mock_httpx_client) -> None:
    """Make the mocked SANDBOX agent-server POST (``mock_httpx_client.post``,
    already stubbed by ``_build_real_service``) return a response that
    actually satisfies ``ConversationInfo.model_validate`` -- a real server
    echoes the same ``agent``/``workspace`` it was sent back in its response
    (see live_status_app_conversation_service.py's own comment: "the
    response echoes the same agent back through the AgentBase
    discriminator") -- rather than the bare ``{"id": ...}`` fixture other
    tests use (sufficient for their call-shape/denial assertions, but not
    for proving the actual conversation reaches READY here)."""
    from unittest.mock import AsyncMock, Mock

    async def _post(url, json=None, **kwargs):  # noqa: A002
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {
            'id': str(uuid4()),
            'agent': json.get('agent'),
            'workspace': json.get('workspace'),
        }
        return response

    mock_httpx_client.post = AsyncMock(side_effect=_post)


def make_transport(
    svc: LiveStatusAppConversationService,
    conversation_get_response: dict,
) -> httpx.MockTransport:
    """An httpx transport whose POST handler parses the REAL JSON body the
    backend sent into a REAL AppConversationStartRequest and drains the REAL
    service's start_app_conversation() async generator -- the actual
    serialization boundary and the actual binder call. GET responses are a
    labeled I/O fixture returning only the fields the backend reads."""

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == 'POST' and path == APP_CONVERSATIONS_PATH:
            import json

            body = json.loads(request.content)
            start_request = AppConversationStartRequest(
                sandbox_id=body['sandbox_id'],
                trigger=body['trigger'],
                tags=body.get('tags') or {},
                agent_profile_id=body.get('agent_profile_id'),
                llm_profile=body.get('llm_profile'),
                selected_repository=body.get('selected_repository'),
                git_provider=body.get('git_provider'),
                selected_branch=body.get('selected_branch'),
            )
            result = None
            async for task in svc.start_app_conversation(start_request):
                result = task
            return httpx.Response(200, json=result.model_dump(mode='json'))
        if request.method == 'GET' and path == APP_CONVERSATIONS_PATH:
            return httpx.Response(200, json=[conversation_get_response])
        raise AssertionError(f'Unexpected request: {request.method} {request.url}')

    return httpx.MockTransport(handler)


def make_automation_run(owner_user_id: str, org_id: UUID, sandbox_id: str):
    """A real openhands.automation.models.Automation/AutomationRun pair (the
    actual ORM classes from automation-reference), enough for
    CloudSandboxBackend's org header and ConversationStart construction.
    API-key minting (automation-service's own Cloud credential, unrelated to
    the gateway owner-identity binding under test) is stubbed in
    make_cloud_backend below."""
    from openhands.automation.models import (
        Automation,
        AutomationRun,
        AutomationRunAsKind,
        AutomationRunStatus,
    )

    automation = Automation(
        id=uuid4(),
        user_id=UUID(owner_user_id),
        run_as_kind=AutomationRunAsKind.USER,
        created_by_user_id=UUID(owner_user_id),
        org_id=org_id,
        name='test-automation',
        trigger={'type': 'cron', 'schedule': '0 * * * *'},
        tarball_path='s3://bucket/tarball.tar.gz',
        entrypoint='python3 main.py',
    )
    run = AutomationRun(
        id=uuid4(),
        automation_id=automation.id,
        status=AutomationRunStatus.RUNNING,
        sandbox_id=sandbox_id,
    )
    run.automation = automation
    return run


_PROVENANCE_JSON_PATH = os.path.join(
    os.path.dirname(__file__), 'automation_source_provenance.json'
)


def _sha256(path: str) -> str:
    import hashlib

    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def assert_real_cloud_backend_provenance() -> None:
    """Guard against silently testing the wrong (or a mirrored/mocked)
    ``cloud.py``: assert the imported ``CloudSandboxBackend`` module was
    actually loaded from the AUTOMATION_SOURCE_DIR checkout, and that its
    file content matches the known-good sha256 for the pinned official
    revision.

    The expected hash and revision come from ``automation_source_
    provenance.json`` (shipped alongside this file; sanitized -- hashes and
    revision only, no source code) rather than from a private capture, so
    this guard runs and is trustworthy for anyone who has only cloned
    https://github.com/OpenHands/automation at that revision into
    AUTOMATION_SOURCE_DIR. If AUTOMATION_CAPTURE_DIR (a separately captured
    exact deployed-image source snapshot) is ALSO set, it is cross-checked
    too, as a redundant extra assertion -- never a substitute for the
    provenance-JSON check above, and never equal to AUTOMATION_SOURCE_DIR
    itself (that would just compare the loaded file to itself)."""
    import inspect
    import json

    from openhands.automation.backends import cloud as cloud_module

    loaded_path = inspect.getfile(cloud_module)
    assert loaded_path.startswith(AUTOMATION_SOURCE_DIR), (
        f'CloudSandboxBackend was loaded from {loaded_path!r}, not the '
        f'AUTOMATION_SOURCE_DIR checkout {AUTOMATION_SOURCE_DIR!r}'
    )
    loaded_hash = _sha256(loaded_path)

    with open(_PROVENANCE_JSON_PATH) as f:
        provenance = json.load(f)
    expected_hash = provenance['files']['openhands/automation/backends/cloud.py']
    assert loaded_hash == expected_hash, (
        'The imported cloud.py does not match the known-good sha256 for '
        f'official revision {provenance["revision"]} of '
        f'{provenance["official_repo"]} (loaded={loaded_hash}, '
        f'expected={expected_hash}); refusing to treat this as the real '
        'backend.'
    )

    if AUTOMATION_CAPTURE_DIR:
        capture_dir_real = os.path.realpath(AUTOMATION_CAPTURE_DIR)
        source_dir_real = os.path.realpath(AUTOMATION_SOURCE_DIR)
        assert capture_dir_real != source_dir_real, (
            'AUTOMATION_CAPTURE_DIR must not be set equal to '
            'AUTOMATION_SOURCE_DIR -- that would compare the loaded file '
            'to itself instead of to an independent capture. Unset '
            'AUTOMATION_CAPTURE_DIR if you have no separate capture; the '
            'provenance-JSON check above is sufficient on its own.'
        )
        snapshot_path = os.path.join(
            AUTOMATION_CAPTURE_DIR, 'openhands/automation/backends/cloud.py'
        )
        snapshot_hash = _sha256(snapshot_path)
        assert loaded_hash == snapshot_hash, (
            "The imported cloud.py does not match AUTOMATION_CAPTURE_DIR's "
            f'captured snapshot (loaded={loaded_hash}, '
            f'snapshot={snapshot_hash}).'
        )


def make_cloud_backend(api_url: str, run, fake_api_key: str = 'fake-automation-key'):
    """The real CloudSandboxBackend with only its own API-key minting
    stubbed (see make_automation_run's docstring). Verifies provenance
    (see assert_real_cloud_backend_provenance) before returning."""
    assert_real_cloud_backend_provenance()
    from openhands.automation.backends.cloud import CloudSandboxBackend

    backend = CloudSandboxBackend(api_url=api_url, run=run)
    backend._ensure_api_key = AsyncMock(return_value=fake_api_key)
    return backend


def make_conversation_start(**kwargs):
    from openhands.automation.backends.base import ConversationStart

    return ConversationStart(**kwargs)


def make_execution_context(sandbox_id: str, api_url: str, api_key: str):
    from openhands.automation.backends.base import ExecutionContext

    return ExecutionContext(
        agent_url='http://agent-server:8000',
        session_key='session-key',
        sandbox_id=sandbox_id,
        api_url=api_url,
        api_key=api_key,
    )
