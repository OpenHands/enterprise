from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from server.maintenance_task_processor.managed_llm_key_ownership_processor import (
    ManagedLlmKeyOwnershipProcessor,
    ManagedLlmKeyOwnershipTarget,
    enqueue_managed_llm_key_ownership_tasks,
)
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User


async def _create_member(
    session,
    *,
    org_id,
    user_id,
    role_id,
    key: str,
    has_custom_key: bool = False,
    agent_settings_diff: dict | None = None,
) -> None:
    session.add(User(id=user_id, current_org_id=org_id))
    session.add(
        OrgMember(
            org_id=org_id,
            user_id=user_id,
            role_id=role_id,
            llm_api_key=key,
            has_custom_llm_api_key=has_custom_key,
            agent_settings_diff=agent_settings_diff,
            managed_llm_key_ownership_version=0,
        )
    )


def _task() -> MaintenanceTask:
    return MaintenanceTask(
        status=MaintenanceTaskStatus.WORKING,
        processor_type='',
        processor_json='{}',
        delay=0,
    )


def test_enqueues_only_unreconciled_managed_key_rows(session_maker):
    org_id = uuid4()
    stale_user_id = uuid4()
    current_user_id = uuid4()
    with session_maker() as session:
        role = Role(name=f'key-enqueue-{uuid4()}', rank=1)
        session.add(role)
        session.flush()
        session.add(Org(id=org_id, name=f'key-enqueue-{org_id}'))
        session.add_all(
            [
                User(id=stale_user_id, current_org_id=org_id),
                User(id=current_user_id, current_org_id=org_id),
            ]
        )
        session.flush()
        session.add_all(
            [
                OrgMember(
                    org_id=org_id,
                    user_id=stale_user_id,
                    role_id=role.id,
                    llm_api_key='legacy-key',
                    managed_llm_key_ownership_version=0,
                ),
                OrgMember(
                    org_id=org_id,
                    user_id=current_user_id,
                    role_id=role.id,
                    llm_api_key='current-key',
                    managed_llm_key_ownership_version=1,
                ),
            ]
        )
        session.commit()

    with patch(
        'server.maintenance_task_processor.managed_llm_key_ownership_processor.session_maker',
        session_maker,
    ):
        assert enqueue_managed_llm_key_ownership_tasks(batch_size=1) == 1
        # A pending task prevents duplicate work until the first run finishes.
        assert enqueue_managed_llm_key_ownership_tasks(batch_size=1) == 0

    with session_maker() as session:
        tasks = session.query(MaintenanceTask).all()
        assert len(tasks) == 1
        processor = tasks[0].get_processor()
    assert len(processor.targets) == 1
    assert processor.targets[0].org_id == str(org_id)
    assert processor.targets[0].user_id == str(stale_user_id)


@pytest.mark.asyncio
async def test_processor_repairs_only_wrong_owned_managed_keys(async_session_maker):
    org_id = uuid4()
    wrong_user_id = uuid4()
    owned_user_id = uuid4()
    custom_user_id = uuid4()
    acp_user_id = uuid4()
    async with async_session_maker() as session:
        role = Role(name=f'key-repair-{uuid4()}', rank=1)
        session.add(role)
        await session.flush()
        session.add(
            Org(
                id=org_id,
                name=f'key-repair-{org_id}',
                agent_settings={
                    'agent_kind': 'openhands',
                    'llm': {'model': 'openhands/test-model'},
                },
            )
        )
        await session.flush()
        await _create_member(
            session,
            org_id=org_id,
            user_id=wrong_user_id,
            role_id=role.id,
            key='shared-admin-key',
        )
        await _create_member(
            session,
            org_id=org_id,
            user_id=owned_user_id,
            role_id=role.id,
            key='owned-member-key',
        )
        await _create_member(
            session,
            org_id=org_id,
            user_id=custom_user_id,
            role_id=role.id,
            key='customer-key',
            has_custom_key=True,
        )
        await _create_member(
            session,
            org_id=org_id,
            user_id=acp_user_id,
            role_id=role.id,
            key='unused-in-acp-mode',
            agent_settings_diff={
                'agent_kind': 'acp',
                'acp_server': 'codex',
            },
        )
        await session.commit()

    processor = ManagedLlmKeyOwnershipProcessor(
        targets=[
            ManagedLlmKeyOwnershipTarget(
                org_id=str(org_id), user_id=str(wrong_user_id)
            ),
            ManagedLlmKeyOwnershipTarget(
                org_id=str(org_id), user_id=str(owned_user_id)
            ),
            ManagedLlmKeyOwnershipTarget(
                org_id=str(org_id), user_id=str(custom_user_id)
            ),
            ManagedLlmKeyOwnershipTarget(org_id=str(org_id), user_id=str(acp_user_id)),
        ]
    )
    verify = AsyncMock(side_effect=[False, True, True])
    delete_alias = AsyncMock()
    generate = AsyncMock(return_value='replacement-member-key')
    with (
        patch(
            'server.maintenance_task_processor.managed_llm_key_ownership_processor.a_session_maker',
            async_session_maker,
        ),
        patch(
            'server.maintenance_task_processor.managed_llm_key_ownership_processor.LiteLlmManager.verify_existing_key_strict',
            verify,
        ),
        patch(
            'server.maintenance_task_processor.managed_llm_key_ownership_processor.LiteLlmManager.delete_key_by_alias_strict',
            delete_alias,
        ),
        patch(
            'server.maintenance_task_processor.managed_llm_key_ownership_processor.LiteLlmManager.generate_key',
            generate,
        ),
    ):
        result = await processor(_task())

    assert result == {
        'verified': 1,
        'repaired': 1,
        'skipped': 2,
        'error_count': 0,
        'errors': [],
    }
    delete_alias.assert_awaited_once()
    assert str(wrong_user_id) in delete_alias.await_args.kwargs['key_alias']
    generate.assert_awaited_once_with(
        str(wrong_user_id),
        str(org_id),
        delete_alias.await_args.kwargs['key_alias'],
        {'type': 'openhands'},
    )

    async with async_session_maker() as session:
        members = {
            row.user_id: row
            for row in (
                await session.scalars(
                    select(OrgMember).where(OrgMember.org_id == org_id)
                )
            ).all()
        }
    assert members[wrong_user_id].llm_api_key.get_secret_value() == (
        'replacement-member-key'
    )
    assert members[owned_user_id].llm_api_key.get_secret_value() == 'owned-member-key'
    assert members[custom_user_id].llm_api_key.get_secret_value() == 'customer-key'
    assert members[acp_user_id].llm_api_key.get_secret_value() == 'unused-in-acp-mode'
    assert all(
        member.managed_llm_key_ownership_version == 1 for member in members.values()
    )


@pytest.mark.asyncio
async def test_processor_retries_when_litellm_ownership_is_unavailable(
    async_session_maker,
):
    org_id = uuid4()
    user_id = uuid4()
    async with async_session_maker() as session:
        role = Role(name=f'key-retry-{uuid4()}', rank=1)
        session.add(role)
        await session.flush()
        session.add(
            Org(
                id=org_id,
                name=f'key-retry-{org_id}',
                agent_settings={
                    'agent_kind': 'openhands',
                    'llm': {'model': 'openhands/test-model'},
                },
            )
        )
        await session.flush()
        await _create_member(
            session,
            org_id=org_id,
            user_id=user_id,
            role_id=role.id,
            key='possibly-shared-key',
        )
        await session.commit()

    processor = ManagedLlmKeyOwnershipProcessor(
        targets=[ManagedLlmKeyOwnershipTarget(org_id=str(org_id), user_id=str(user_id))]
    )
    with (
        patch(
            'server.maintenance_task_processor.managed_llm_key_ownership_processor.a_session_maker',
            async_session_maker,
        ),
        patch(
            'server.maintenance_task_processor.managed_llm_key_ownership_processor.LiteLlmManager.verify_existing_key_strict',
            AsyncMock(side_effect=RuntimeError('LiteLLM unavailable')),
        ),
        patch(
            'server.maintenance_task_processor.managed_llm_key_ownership_processor.LiteLlmManager.delete_key_by_alias_strict',
            AsyncMock(),
        ) as delete_alias,
        patch(
            'server.maintenance_task_processor.managed_llm_key_ownership_processor.LiteLlmManager.generate_key',
            AsyncMock(),
        ) as generate,
    ):
        result = await processor(_task())

    assert result['error_count'] == 1
    assert result['repaired'] == 0
    delete_alias.assert_not_awaited()
    generate.assert_not_awaited()
    async with async_session_maker() as session:
        member = await session.get(
            OrgMember,
            {'org_id': org_id, 'user_id': user_id},
        )
    assert member is not None
    assert member.managed_llm_key_ownership_version == 0
    assert member.llm_api_key.get_secret_value() == 'possibly-shared-key'
