"""BYOR export shares durable issuance, exact ownership and removal fencing."""

import asyncio
import hashlib
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from storage.budget_control import BudgetControlConflict, BudgetWriteDenied
from storage.litellm_credentials import (
    ensure_byor_credential,
    retire_replaced_credentials,
)
from storage.llm_credential_operation import LlmCredentialOperation
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_member import OrgMember
from storage.org_member_store import OrgMemberStore
from tests.unit.test_managed_member_admission import admission as admission_fixture
from tests.unit.test_managed_member_admission import adoption as adoption_fixture

admission = admission_fixture
adoption = adoption_fixture


@pytest.fixture
async def byor_member(admission):
    org_id, _, user_id, role_id, _, _ = admission
    await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    return admission


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['managed', 'external', 'needs_adoption'])
async def test_export_commits_credential_without_changing_budget_policy(
    byor_member, async_session_maker, mode
):
    org_id, _, user_id, _, proxy, native = byor_member
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        settings.control_mode = mode
        await session.commit()
    before = deepcopy(proxy.state)
    native.calls.clear()
    key = await ensure_byor_credential(org_id, user_id)
    assert proxy.state == before
    assert len(native.keys) == 2
    assert all(
        path not in {'/team/update', '/team/member_update'} for path, _ in native.calls
    )
    async with async_session_maker() as session:
        member = await session.get(OrgMember, {'org_id': org_id, 'user_id': user_id})
        assert member.llm_api_key_for_byor.get_secret_value() == key
        assert member.llm_api_key.get_secret_value() != key
        operation = await session.scalar(
            select(LlmCredentialOperation).where(
                LlmCredentialOperation.key_hash
                == hashlib.sha256(key.encode()).hexdigest()
            )
        )
        assert operation.status == 'issued'
        assert operation.activated_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'policy',
    [
        {'blocked': True},
        {'max_budget': 0},
        {'models': ['only-approved']},
        {'key_alias': 'operator-renamed'},
    ],
)
async def test_export_preserves_restricted_and_renamed_owned_keys(byor_member, policy):
    org_id, _, user_id, _, _, native = byor_member
    key = await ensure_byor_credential(org_id, user_id)
    native.keys[-1].update(policy)
    before = deepcopy(native.keys)
    native.calls.clear()
    assert await ensure_byor_credential(org_id, user_id) == key
    assert native.keys == before
    assert all(body is None for _, body in native.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['missing', 'foreign_user', 'foreign_org'])
async def test_unverifiable_stored_key_is_not_deleted_or_replaced(byor_member, change):
    org_id, _, user_id, _, _, native = byor_member
    key = await ensure_byor_credential(org_id, user_id)
    if change == 'missing':
        native.keys.pop()
    elif change == 'foreign_user':
        native.keys[-1]['user_id'] = str(uuid4())
    else:
        native.keys[-1]['team_id'] = str(uuid4())
    native.calls.clear()
    with pytest.raises(BudgetWriteDenied, match='ownership could not be verified'):
        await ensure_byor_credential(org_id, user_id)
    member = await OrgMemberStore.get_org_member(org_id, user_id)
    assert member.llm_api_key_for_byor.get_secret_value() == key
    assert all(body is None for _, body in native.calls)


@pytest.mark.asyncio
async def test_rotation_preserves_restricted_key_and_database_pointer(byor_member):
    org_id, _, user_id, _, _, native = byor_member
    key = await ensure_byor_credential(org_id, user_id)
    native.keys[-1]['max_budget'] = 0
    native.calls.clear()
    with pytest.raises(BudgetWriteDenied, match='independent restrictions'):
        await ensure_byor_credential(org_id, user_id, rotate=True)
    member = await OrgMemberStore.get_org_member(org_id, user_id)
    assert member.llm_api_key_for_byor.get_secret_value() == key
    assert all(body is None for _, body in native.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'failure', ['after_key', 'before_revoke', 'after_revoke', None]
)
async def test_rotation_recovery_keeps_budget_counters_and_retires_only_old_byor_key(
    byor_member, async_session_maker, failure
):
    org_id, _, user_id, _, proxy, native = byor_member
    old = await ensure_byor_credential(org_id, user_id)
    main_hash = native.keys[0]['token']
    before = deepcopy(proxy.state)
    native.calls.clear()
    native.failure = failure
    if failure == 'after_key':
        with pytest.raises(httpx.ReadTimeout):
            await ensure_byor_credential(org_id, user_id, rotate=True)
        member = await OrgMemberStore.get_org_member(org_id, user_id)
        assert member.llm_api_key_for_byor.get_secret_value() == old
        assert len(native.keys) == 3
    new = await ensure_byor_credential(org_id, user_id, rotate=True)
    assert new != old
    await retire_replaced_credentials(org_id)
    assert {key['token'] for key in native.keys} == {
        main_hash,
        hashlib.sha256(new.encode()).hexdigest(),
    }
    assert len([path for path, _ in native.calls if path == '/key/generate']) == 1
    assert proxy.state == before
    async with async_session_maker() as session:
        operation = await session.scalar(
            select(LlmCredentialOperation).where(
                LlmCredentialOperation.replaces_key_hash
                == hashlib.sha256(old.encode()).hexdigest()
            )
        )
        assert operation.activated_at is not None
        assert operation.retired_at is not None


@pytest.mark.asyncio
async def test_failed_member_commit_recovers_same_replacement_without_revoking_old(
    byor_member,
):
    org_id, _, user_id, _, _, native = byor_member
    old = await ensure_byor_credential(org_id, user_id)
    native.calls.clear()

    def fail_commit(session):
        if any(
            isinstance(row, OrgMember)
            and row.llm_api_key_for_byor is not None
            and row.llm_api_key_for_byor.get_secret_value() != old
            for row in session.identity_map.values()
        ):
            raise RuntimeError('member commit failed')

    event.listen(Session, 'before_commit', fail_commit)
    try:
        with pytest.raises(RuntimeError, match='member commit failed'):
            await ensure_byor_credential(org_id, user_id, rotate=True)
    finally:
        event.remove(Session, 'before_commit', fail_commit)
    member = await OrgMemberStore.get_org_member(org_id, user_id)
    assert member.llm_api_key_for_byor.get_secret_value() == old
    assert all(path != '/key/delete' for path, _ in native.calls)
    new = await ensure_byor_credential(org_id, user_id, rotate=True)
    assert new != old
    assert len([path for path, _ in native.calls if path == '/key/generate']) == 1


@pytest.mark.asyncio
async def test_export_requires_current_membership_and_cannot_reinsert_removed_member(
    byor_member,
):
    org_id, _, user_id, _, _, native = byor_member
    await ensure_byor_credential(org_id, user_id)
    await OrgMemberStore.remove_user_from_org(org_id, user_id)
    native.calls.clear()
    with pytest.raises(BudgetWriteDenied, match='membership no longer exists'):
        await ensure_byor_credential(org_id, user_id)
    assert native.calls == []
    assert native.keys == []
    assert await OrgMemberStore.get_org_member(org_id, user_id) is None


@pytest.mark.asyncio
async def test_export_holds_membership_fence_until_pointer_commit(byor_member):
    org_id, _, user_id, _, _, native = byor_member
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause():
        entered.set()
        await asyncio.wait_for(release.wait(), 15)

    native.before_key = pause
    task = asyncio.create_task(ensure_byor_credential(org_id, user_id))
    try:
        await asyncio.wait_for(entered.wait(), 15)
        with pytest.raises(BudgetControlConflict):
            await OrgMemberStore.remove_user_from_org(org_id, user_id)
    finally:
        release.set()
        await task
    await OrgMemberStore.remove_user_from_org(org_id, user_id)
    assert native.keys == []


@pytest.mark.asyncio
async def test_pending_budget_operation_allows_read_only_export_but_not_rotation(
    byor_member,
):
    org_id, _, user_id, _, _, native = byor_member
    key = await ensure_byor_credential(org_id, user_id)
    with patch(
        'storage.budget_control.BudgetControlSession.pending_operation',
        return_value=object(),
    ):
        assert await ensure_byor_credential(org_id, user_id) == key
        with pytest.raises(BudgetControlConflict, match='pending budget operation'):
            await ensure_byor_credential(org_id, user_id, rotate=True)
    assert len(native.keys) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'pattern', ['BYOR Key - {user_id} - {org_id}', 'custom/{org_id}/{user_id}']
)
async def test_export_uses_configured_alias_and_byor_metadata(byor_member, pattern):
    org_id, _, user_id, _, _, native = byor_member
    native.calls.clear()
    with patch('server.constants.BYOR_KEY_ALIAS_PATTERN', pattern):
        await ensure_byor_credential(org_id, user_id)
    payload = next(body for path, body in native.calls if path == '/key/generate')
    assert payload['metadata'] == {'type': 'byor'}
    assert payload['key_alias'] == pattern.format(org_id=org_id, user_id=user_id)
