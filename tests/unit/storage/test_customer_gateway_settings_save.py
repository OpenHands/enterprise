"""Ordinary settings saves in a customer-gateway org leave legacy member keys and the gateway
mapping untouched (PostgreSQL). Raw customer keys change only through the operator store/CLI."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from storage.customer_gateway import CustomerGatewayMemberCredential
from storage.customer_gateway_store import CustomerGatewayStore
from storage.org_member import OrgMember
from storage.saas_settings_store import SaasSettingsStore
from tests.unit.test_saas_settings_store import (  # noqa: F401
    _make_settings,
    org_with_multiple_members_fixture,
)

PROFILE = {
    'base_url': 'https://gw.customer.example/v1',
    'models': {'deepseek': 'deepseek/chat'},
}
BYOR = dict(model='anthropic/claude-sonnet-4', base_url='https://api.anthropic.com/v1')


@pytest.fixture
def gateway_org(org_with_multiple_members_fixture, async_session_maker, monkeypatch):  # noqa: F811
    monkeypatch.setenv('CUSTOMER_GATEWAY_ENABLED', 'true')
    monkeypatch.setenv(
        'CUSTOMER_GATEWAY_RELAY_URL', 'https://app/api/customer-gateway/v1'
    )
    monkeypatch.setenv(
        'CUSTOMER_GATEWAY_TOKEN_URL', 'https://app/api/customer-gateway/token'
    )
    with (
        patch('storage.customer_gateway_store.a_session_maker', async_session_maker),
        patch('storage.saas_settings_store.a_session_maker', async_session_maker),
    ):
        yield org_with_multiple_members_fixture


async def _edit(store, built):
    """Persist a ``_make_settings`` result the way the settings router does."""
    settings, edits = built
    await store.edit(settings, **edits)


async def _snapshot(session_maker, org_id):
    with session_maker() as s:
        members = {
            m.user_id: m._llm_api_key
            for m in s.execute(select(OrgMember).where(OrgMember.org_id == org_id))
            .scalars()
            .all()
        }
        creds = {
            c.user_id: (c.ciphertext, c.credential_version, c.policy_version)
            for c in s.execute(select(CustomerGatewayMemberCredential)).scalars().all()
        }
    return members, creds


async def _seed(fx):
    org_id = fx['org_id']
    await CustomerGatewayStore.set_org_gateway(org_id, 'gw', PROFILE, True, 'op')
    await CustomerGatewayStore.set_member_credential(
        org_id, fx['member1_user_id'], 'sk-member-a-0123456789', ['deepseek'], 'op'
    )
    await CustomerGatewayStore.set_member_credential(
        org_id, fx['member2_user_id'], 'sk-member-b-9876543210', ['deepseek'], 'op'
    )


async def test_on_preference_save_leaves_legacy_keys_and_mapping(
    gateway_org, session_maker
):
    fx = gateway_org
    await _seed(fx)
    before = await _snapshot(session_maker, fx['org_id'])
    store = SaasSettingsStore(str(fx['admin_user_id']))
    await _edit(
        store, _make_settings(**BYOR, api_key='admin-initial-key', language='fr')
    )
    await _edit(store, _make_settings(**BYOR, language='de'))
    assert await _snapshot(session_maker, fx['org_id']) == before


@pytest.mark.parametrize(
    'entered', ['sk-entered-by-admin-000', 'eyJhbGciOiJIUzI1NiJ9.inference.bearer']
)
async def test_on_llm_key_change_refused_and_nothing_written(
    gateway_org, session_maker, entered
):
    fx = gateway_org
    await _seed(fx)
    before = await _snapshot(session_maker, fx['org_id'])
    store = SaasSettingsStore(str(fx['admin_user_id']))
    with pytest.raises(HTTPException) as exc:
        await _edit(store, _make_settings(**BYOR, api_key=entered))
    assert exc.value.status_code == 409
    assert await _snapshot(session_maker, fx['org_id']) == before


async def test_operator_store_is_the_only_raw_key_path(gateway_org, session_maker):
    fx = gateway_org
    await _seed(fx)
    members_before, creds_before = await _snapshot(session_maker, fx['org_id'])
    await CustomerGatewayStore.set_member_credential(
        fx['org_id'],
        fx['member1_user_id'],
        'sk-member-a-rotated-0000',
        ['deepseek'],
        'op',
    )
    members_after, creds_after = await _snapshot(session_maker, fx['org_id'])
    assert members_after == members_before
    a = fx['member1_user_id']
    assert creds_after[a][1] == creds_before[a][1] + 1
    assert creds_after[a][2] == creds_before[a][2]
    assert creds_after[fx['member2_user_id']] == creds_before[fx['member2_user_id']]


async def test_off_save_writes_only_personal_key(
    org_with_multiple_members_fixture,  # noqa: F811
    async_session_maker,
    session_maker,
    monkeypatch,  # noqa: F811
):
    monkeypatch.delenv('CUSTOMER_GATEWAY_ENABLED', raising=False)
    fx = org_with_multiple_members_fixture
    with patch('storage.saas_settings_store.a_session_maker', async_session_maker):
        await _edit(
            SaasSettingsStore(str(fx['admin_user_id'])),
            _make_settings(**BYOR, api_key='shared-key-v9'),
        )
    members, _ = await _snapshot(session_maker, fx['org_id'])
    assert fx['decrypt_value'](members[fx['admin_user_id']]) == 'shared-key-v9'
    for i in (1, 2):
        assert (
            fx['decrypt_value'](members[fx[f'member{i}_user_id']])
            == f'member{i}-initial-key'
        )


MANAGED = dict(model='openhands/claude-sonnet-4-5-20250929', clear_base_url=True)


async def test_on_save_never_reaches_managed_key_provisioning(
    gateway_org, session_maker
):
    fx = gateway_org
    await _seed(fx)
    before = await _snapshot(session_maker, fx['org_id'])
    ensure = AsyncMock(side_effect=AssertionError('managed provisioning while ON'))
    with patch.object(SaasSettingsStore, '_ensure_api_key', ensure):
        await _edit(
            SaasSettingsStore(str(fx['admin_user_id'])),
            _make_settings(**MANAGED, language='fr'),
        )
    ensure.assert_not_called()
    assert await _snapshot(session_maker, fx['org_id']) == before


async def test_off_save_still_provisions_managed_key(
    org_with_multiple_members_fixture,  # noqa: F811
    async_session_maker,
    monkeypatch,
):
    monkeypatch.delenv('CUSTOMER_GATEWAY_ENABLED', raising=False)
    fx = org_with_multiple_members_fixture
    ensure = AsyncMock()
    with (
        patch('storage.saas_settings_store.a_session_maker', async_session_maker),
        patch.object(SaasSettingsStore, '_ensure_api_key', ensure),
    ):
        await _edit(
            SaasSettingsStore(str(fx['admin_user_id'])),
            _make_settings(**MANAGED),
        )
    ensure.assert_called_once()
