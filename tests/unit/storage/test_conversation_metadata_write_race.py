"""The start path and the lifecycle webhook racing to write one conversation.

Both writers are the real ``SaasSQLAppConversationInfoService`` on separate
PostgreSQL sessions, called the way production calls them: the start path
saves with ``save_app_conversation_info`` and a ``ResolverUserContext``; the
``on_conversation_update`` webhook inserts its stub with
``create_app_conversation_info`` as ``ADMIN``. A pause inside one writer's
``commit()`` pins each interleaving. Regression tests for
https://github.com/OpenHands/enterprise/issues/26.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from integrations.resolver_context import ResolverUserContext
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
    ConversationTrigger,
)
from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    StoredConversationMetadata,
)
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.sandbox.sandbox_store import StoredSandbox
from openhands.app_server.user.specifiy_user_context import ADMIN
from server.utils.saas_app_conversation_info_injector import (
    SaasSQLAppConversationInfoService,
)
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas

START_TITLE = 'GitHub Issue #7: fix the bug'


class FakeSaasUserAuth:
    """Stands in for the Keycloak-backed ``SaasUserAuth``."""

    def __init__(self, user_id: str, org_id: uuid.UUID):
        self._user_id = user_id
        self._org_id = org_id

    async def get_user_id(self) -> str:
        return self._user_id

    async def get_effective_org_id(self) -> uuid.UUID:
        return self._org_id


@pytest.fixture
def owner(create_org, create_user, session_maker):
    resolver_org = create_org()
    user = create_user()
    with session_maker() as session:
        session.add(
            StoredSandbox(
                id='sb-1',
                created_by_user_id=str(user.id),
                credential_org_id=resolver_org.id,
                sandbox_spec_id='spec',
            )
        )
        session.commit()
    return str(user.id), resolver_org.id


def _start_info(cid: uuid.UUID, user_id: str) -> AppConversationInfo:
    return AppConversationInfo(
        id=cid,
        title=START_TITLE,
        sandbox_id='sb-1',
        created_by_user_id=user_id,
        selected_repository='acme/widgets',
        selected_branch='main',
        git_provider=ProviderType.GITHUB,
        trigger=ConversationTrigger.RESOLVER,
        llm_model='m',
    )


def _webhook_stub(cid: uuid.UUID, user_id: str) -> AppConversationInfo:
    """What ``on_conversation_update`` builds when it finds no row yet."""
    return AppConversationInfo(
        id=cid,
        title=f'Conversation {cid.hex}',
        sandbox_id='sb-1',
        created_by_user_id=user_id,
        trigger=ConversationTrigger.REMOTE_API_KEY,
        llm_model='m',
    )


def _writers(start_session, webhook_session, user_id, resolver_org):
    start = SaasSQLAppConversationInfoService(
        db_session=start_session,
        user_context=ResolverUserContext(
            FakeSaasUserAuth(user_id, resolver_org), resolver_org_id=resolver_org
        ),
    )
    webhook = SaasSQLAppConversationInfoService(
        db_session=webhook_session, user_context=ADMIN
    )
    return start, webhook


def _pause_commit(
    session,
    reached: asyncio.Event,
    release: asyncio.Event,
    *,
    nth: int = 1,
    flush_first: bool = False,
):
    """Hold the session's ``nth`` commit until ``release`` is set."""
    real_commit = session.commit
    calls = {'n': 0}

    async def commit():
        calls['n'] += 1
        if calls['n'] == nth:
            if flush_first:
                await session.flush()
            reached.set()
            await release.wait()
        await real_commit()

    session.commit = commit


def _pause_after_first_merge(session, reached: asyncio.Event, release: asyncio.Event):
    """Hold the writer after its primary-key lookup and before its INSERT."""
    real_merge = session.merge
    calls = {'n': 0}

    async def merge(instance, **kwargs):
        merged = await real_merge(instance, **kwargs)
        calls['n'] += 1
        if calls['n'] == 1:
            reached.set()
            await release.wait()
        return merged

    session.merge = merge


async def _wait_for_lock_waiter(async_session_maker):
    async with async_session_maker() as session:
        for _ in range(250):
            waiting = await session.scalar(
                text(
                    'select count(*) from pg_stat_activity '
                    "where wait_event_type = 'Lock' "
                    'and datname = current_database()'
                )
            )
            if waiting:
                return
            # pg_stat_activity is a snapshot taken once per transaction.
            await session.rollback()
            await asyncio.sleep(0.02)
    raise AssertionError('no writer blocked on the row lock')


async def _final_state(async_session_maker, cid):
    async with async_session_maker() as session:
        row = await session.get(StoredConversationMetadata, str(cid))
        saas = await session.get(StoredConversationMetadataSaas, str(cid))
        return (
            row.title,
            row.selected_repository,
            row.trigger,
            saas.org_id if saas else None,
        )


def _expected(resolver_org):
    return (START_TITLE, 'acme/widgets', 'resolver', resolver_org)


@pytest.mark.asyncio
async def test_webhook_commits_between_start_lookup_and_insert(
    owner, async_session_maker
):
    user_id, resolver_org = owner
    cid = uuid.uuid4()
    async with async_session_maker() as ss, async_session_maker() as ws:
        start, webhook = _writers(ss, ws, user_id, resolver_org)
        reached, release = asyncio.Event(), asyncio.Event()
        _pause_after_first_merge(ss, reached, release)

        start_task = asyncio.create_task(
            start.save_app_conversation_info(_start_info(cid, user_id))
        )
        await reached.wait()
        assert await webhook.create_app_conversation_info(_webhook_stub(cid, user_id))
        release.set()
        await start_task

    assert await _final_state(async_session_maker, cid) == _expected(resolver_org)


@pytest.mark.asyncio
async def test_webhook_insert_in_flight_when_start_looks_up(owner, async_session_maker):
    user_id, resolver_org = owner
    cid = uuid.uuid4()
    async with async_session_maker() as ss, async_session_maker() as ws:
        start, webhook = _writers(ss, ws, user_id, resolver_org)
        reached, release = asyncio.Event(), asyncio.Event()
        _pause_commit(ws, reached, release, flush_first=True)

        webhook_task = asyncio.create_task(
            webhook.create_app_conversation_info(_webhook_stub(cid, user_id))
        )
        await reached.wait()
        start_task = asyncio.create_task(
            start.save_app_conversation_info(_start_info(cid, user_id))
        )
        await _wait_for_lock_waiter(async_session_maker)
        release.set()
        assert await webhook_task
        await start_task

    assert await _final_state(async_session_maker, cid) == _expected(resolver_org)


@pytest.mark.asyncio
async def test_webhook_saas_row_in_flight_when_start_saves_its_own(
    owner, async_session_maker
):
    user_id, resolver_org = owner
    cid = uuid.uuid4()
    async with async_session_maker() as ss, async_session_maker() as ws:
        start, webhook = _writers(ss, ws, user_id, resolver_org)
        reached, release = asyncio.Event(), asyncio.Event()
        # Commit 1 is the webhook's base row; commit 2 is its SaaS row.
        _pause_commit(ws, reached, release, nth=2, flush_first=True)

        webhook_task = asyncio.create_task(
            webhook.create_app_conversation_info(_webhook_stub(cid, user_id))
        )
        await reached.wait()
        start_task = asyncio.create_task(
            start.save_app_conversation_info(_start_info(cid, user_id))
        )
        await _wait_for_lock_waiter(async_session_maker)
        release.set()
        assert await webhook_task
        await start_task

    assert await _final_state(async_session_maker, cid) == _expected(resolver_org)


@pytest.mark.asyncio
async def test_start_insert_in_flight_when_webhook_writes(owner, async_session_maker):
    user_id, resolver_org = owner
    cid = uuid.uuid4()
    async with async_session_maker() as ss, async_session_maker() as ws:
        start, webhook = _writers(ss, ws, user_id, resolver_org)
        reached, release = asyncio.Event(), asyncio.Event()
        _pause_commit(ss, reached, release, flush_first=True)

        start_task = asyncio.create_task(
            start.save_app_conversation_info(_start_info(cid, user_id))
        )
        await reached.wait()
        webhook_task = asyncio.create_task(
            webhook.create_app_conversation_info(_webhook_stub(cid, user_id))
        )
        await _wait_for_lock_waiter(async_session_maker)
        release.set()
        await start_task
        assert await webhook_task is False

    assert await _final_state(async_session_maker, cid) == _expected(resolver_org)
