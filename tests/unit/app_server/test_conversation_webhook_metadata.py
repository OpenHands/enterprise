"""PostgreSQL regressions for stale, untrusted sandbox metadata snapshots."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openhands.app_server.app_conversation.app_conversation_models import (
    AGENT_PROFILE_ID_TAG_KEY,
    ARCHIVE_WORKSPACE_PATH_TAG_KEY,
    DIRECT_LLM_VALIDATED_TAG,
    AppConversationInfo,
)
from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    SQLAppConversationInfoService,
)
from openhands.app_server.event_callback.webhook_router import merge_conversation_tags
from openhands.app_server.user.specifiy_user_context import ADMIN, SandboxUserContext
from server.utils.saas_app_conversation_info_injector import (
    SaasSQLAppConversationInfoService,
)
from storage.org import Org
from storage.user import User


@pytest.mark.parametrize(
    'service_class', [SQLAppConversationInfoService, SaasSQLAppConversationInfoService]
)
async def test_stale_webhook_cannot_erase_forge_or_restore_app_metadata(
    async_session_maker: async_sessionmaker[AsyncSession],
    service_class: type[SQLAppConversationInfoService],
) -> None:
    user_id, org_id = uuid4(), uuid4()
    async with async_session_maker() as setup:
        setup.add(Org(id=org_id, name='Webhook test org'))
        await setup.flush()
        setup.add(User(id=user_id, current_org_id=org_id))
        await setup.commit()

    async with (
        async_session_maker() as app_session,
        async_session_maker() as webhook_session,
    ):
        app = service_class(db_session=app_session, user_context=ADMIN)
        webhook = service_class(
            db_session=webhook_session,
            user_context=SandboxUserContext(user_id=str(user_id), sandbox_id='owned'),
        )
        info = AppConversationInfo(
            id=uuid4(),
            created_by_user_id=str(user_id),
            sandbox_id='owned',
            title='Initial',
        )
        await app.save_app_conversation_info(info)
        stale = await webhook.get_app_conversation_info(info.id)
        assert stale is not None and stale.tags == {}
        info.tags = {
            DIRECT_LLM_VALIDATED_TAG: '1',
            ARCHIVE_WORKSPACE_PATH_TAG_KEY: '/workspace/project',
            AGENT_PROFILE_ID_TAG_KEY: 'launched-profile',
        }
        await app.save_app_conversation_info(info)
        stale.tags = {
            DIRECT_LLM_VALIDATED_TAG: 'forged',
            ARCHIVE_WORKSPACE_PATH_TAG_KEY: '/outside-owned-workspace',
            'sdkcontext': 'preserved',
        }
        await webhook.save_app_conversation_info(stale, from_sandbox=True)
        current = await app.get_app_conversation_info(info.id)
        assert current is not None
        assert current.tags == {**info.tags, 'sdkcontext': 'preserved'}

        # Re-enabling the gateway invalidates the trusted direct-only marker.
        # A delayed sandbox snapshot must not resurrect its old value.
        current.tags.pop(DIRECT_LLM_VALIDATED_TAG)
        await app.save_app_conversation_info(current)
        await webhook.save_app_conversation_info(stale, from_sandbox=True)
        revoked = await app.get_app_conversation_info(info.id)
        assert revoked is not None
        assert DIRECT_LLM_VALIDATED_TAG not in revoked.tags
        assert revoked.tags[ARCHIVE_WORKSPACE_PATH_TAG_KEY] == '/workspace/project'

        # A conversation first discovered through the SDK is never app-validated.
        forged = info.model_copy(update={'id': uuid4(), 'tags': dict(info.tags)})
        await webhook.save_app_conversation_info(forged, from_sandbox=True)
        assert forged.tags == {}


async def test_initial_webhook_save_waits_for_launch_metadata(
    async_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquired, resume_app, webhook_entered = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    async with (
        async_session_maker() as app_session,
        async_session_maker() as webhook_session,
    ):
        app = SQLAppConversationInfoService(db_session=app_session, user_context=ADMIN)
        webhook = SQLAppConversationInfoService(
            db_session=webhook_session, user_context=ADMIN
        )
        app_execute, webhook_execute = app_session.execute, webhook_session.execute

        def barrier[**P, T](
            execute: Callable[P, Awaitable[T]],
            *,
            entered: asyncio.Event,
            after: bool = False,
        ) -> Callable[P, Awaitable[T]]:
            async def execute_with_barrier(*args: P.args, **kwargs: P.kwargs) -> T:
                is_lock = bool(args) and 'pg_advisory_xact_lock' in str(args[0])
                if is_lock and not after:
                    entered.set()
                result = await execute(*args, **kwargs)
                if is_lock and after:
                    entered.set()
                    await resume_app.wait()
                return result

            return execute_with_barrier

        monkeypatch.setattr(
            app_session, 'execute', barrier(app_execute, entered=acquired, after=True)
        )
        monkeypatch.setattr(
            webhook_session,
            'execute',
            barrier(webhook_execute, entered=webhook_entered),
        )
        info = AppConversationInfo(
            id=uuid4(),
            sandbox_id='owned',
            created_by_user_id=None,
            tags={
                DIRECT_LLM_VALIDATED_TAG: '1',
                ARCHIVE_WORKSPACE_PATH_TAG_KEY: '/workspace/project',
            },
        )
        stale = info.model_copy(update={'tags': {'sdkcontext': 'preserved'}})
        app_save = asyncio.create_task(app.save_app_conversation_info(info))
        await asyncio.wait_for(acquired.wait(), timeout=5)
        webhook_save = asyncio.create_task(
            webhook.save_app_conversation_info(stale, from_sandbox=True)
        )
        await asyncio.wait_for(webhook_entered.wait(), timeout=5)
        resume_app.set()
        await asyncio.wait_for(asyncio.gather(app_save, webhook_save), timeout=5)
        current = await app.get_app_conversation_info(info.id)
        assert current is not None
        assert current.tags == {**info.tags, 'sdkcontext': 'preserved'}


def test_webhook_tags_cannot_replace_reserved_metadata() -> None:
    existing = {ARCHIVE_WORKSPACE_PATH_TAG_KEY: '/workspace/project'}
    incoming = {
        ARCHIVE_WORKSPACE_PATH_TAG_KEY: '/etc',
        DIRECT_LLM_VALIDATED_TAG: '1',
        'sdkcontext': 'value',
    }
    assert merge_conversation_tags(existing, incoming) == {
        **existing,
        'sdkcontext': 'value',
    }
