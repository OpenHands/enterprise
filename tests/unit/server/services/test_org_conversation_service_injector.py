import sys
import types
from contextlib import asynccontextmanager

import pytest
from starlette.datastructures import State

from server.services.org_conversation_service import (
    OrgConversationServiceInjector,
)


@pytest.mark.asyncio
async def test_org_conversation_service_injector_sets_sandbox_service():
    dummy_db_session = object()

    class DummySandboxService:
        closed = False

    dummy_sandbox_service = DummySandboxService()

    @asynccontextmanager
    async def fake_db_session(state, request=None):
        yield dummy_db_session

    @asynccontextmanager
    async def fake_sandbox_service(state, request=None):
        try:
            yield dummy_sandbox_service
        finally:
            dummy_sandbox_service.closed = True

    sentinel = types.ModuleType('openhands.app_server.config')
    sentinel.get_db_session = fake_db_session
    sentinel.get_sandbox_service = fake_sandbox_service

    original = sys.modules.get('openhands.app_server.config')
    sys.modules['openhands.app_server.config'] = sentinel
    try:
        injector = OrgConversationServiceInjector()
        state = State()
        generator = injector.inject(state)
        service = await generator.__anext__()
        assert service.db_session is dummy_db_session
        assert service.sandbox_service is dummy_sandbox_service
        assert dummy_sandbox_service.closed is False
        await generator.aclose()
        assert dummy_sandbox_service.closed is True
    finally:
        if original is None:
            sys.modules.pop('openhands.app_server.config', None)
        else:
            sys.modules['openhands.app_server.config'] = original


@asynccontextmanager
async def _patched_app_server_config(fake_db_session, fake_sandbox_service):
    """Swap the app-server config module for fakes while the injector runs."""
    sentinel = types.ModuleType('openhands.app_server.config')
    sentinel.get_db_session = fake_db_session
    sentinel.get_sandbox_service = fake_sandbox_service

    original = sys.modules.get('openhands.app_server.config')
    sys.modules['openhands.app_server.config'] = sentinel
    try:
        yield
    finally:
        if original is None:
            sys.modules.pop('openhands.app_server.config', None)
        else:
            sys.modules['openhands.app_server.config'] = original


async def _inject_with_captured_sandbox_state(state):
    """Run the injector and return the state handed to the sandbox service."""
    dummy_db_session = object()
    captured = {}

    @asynccontextmanager
    async def fake_db_session(state, request=None):
        yield dummy_db_session

    @asynccontextmanager
    async def fake_sandbox_service(state, request=None):
        captured['state'] = state
        yield object()

    async with _patched_app_server_config(fake_db_session, fake_sandbox_service):
        generator = OrgConversationServiceInjector().inject(state)
        await generator.__anext__()
        await generator.aclose()

    return captured['state'], dummy_db_session


@pytest.mark.asyncio
async def test_org_conversation_service_injector_looks_sandboxes_up_as_admin():
    # Org admins must see other members' sandboxes, so the sandbox service
    # runs instance-wide while reusing the request's database session.
    from openhands.app_server.services.db_session_injector import DB_SESSION_ATTR
    from openhands.app_server.user.specifiy_user_context import (
        ADMIN,
        USER_CONTEXT_ATTR,
    )

    sandbox_state, db_session = await _inject_with_captured_sandbox_state(State())

    assert getattr(sandbox_state, USER_CONTEXT_ATTR) is ADMIN
    assert getattr(sandbox_state, DB_SESSION_ATTR) is db_session


@pytest.mark.asyncio
async def test_org_conversation_service_injector_keeps_request_state_caller_scoped():
    from openhands.app_server.user.specifiy_user_context import USER_CONTEXT_ATTR

    request_state = State()

    sandbox_state, _ = await _inject_with_captured_sandbox_state(request_state)

    assert sandbox_state is not request_state
    assert not hasattr(request_state, USER_CONTEXT_ATTR)
