"""CustomerGatewayStore on PostgreSQL (shared fixtures; migration 219 applied via template)."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from storage.customer_gateway import CustomerGatewayMemberCredential
from storage.customer_gateway_store import CustomerGatewayDenied, CustomerGatewayStore
from storage.org_member import OrgMember
from storage.role import Role

ORG_ID = UUID('d4444444-4444-4444-4444-444444444444')
SUSAN = UUID('d5555555-5555-5555-5555-555555555555')
DAVID = UUID('d6666666-6666-6666-6666-666666666666')
KEY_1, KEY_2, KEY_D = (
    'sk-susan-one-0123456789',
    'sk-susan-two-9876543210',
    'sk-david-0000000000',
)
PROFILE = {
    'base_url': 'https://gw.customer.example/v1',
    'models': {'deepseek': 'deepseek/chat'},
}


@pytest.fixture
def store(async_session_maker, session_maker, create_org, create_user):
    create_org(id=ORG_ID)
    with session_maker() as session:
        role = Role(name=f'member-{uuid4().hex[:8]}', rank=3)
        session.add(role)
        session.flush()
        for uid in (SUSAN, DAVID):
            create_user(id=uid, current_org_id=ORG_ID)
            session.add(
                OrgMember(
                    org_id=ORG_ID,
                    user_id=uid,
                    role_id=role.id,
                    llm_api_key='k',
                    status='active',
                )
            )
        session.commit()
    jwt = JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('test_secret'), active=True)]
    )
    with (
        patch('storage.customer_gateway_store.a_session_maker', async_session_maker),
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt),
    ):
        yield CustomerGatewayStore


async def test_org_gateway_default_off_and_toggle(store):
    assert await store.get_enabled_org_gateway(ORG_ID) is None
    await store.set_org_gateway(ORG_ID, 'litellm', PROFILE, True, 'op')
    assert (await store.get_enabled_org_gateway(ORG_ID)).gateway_id == 'litellm'
    await store.set_org_gateway(ORG_ID, 'litellm', PROFILE, False, 'op')
    assert await store.get_enabled_org_gateway(ORG_ID) is None


async def test_rotation_keeps_identity_and_policy_version(store, async_session_maker):
    assert (
        await store.set_member_credential(
            ORG_ID, SUSAN, KEY_1, ['deepseek', 'sonnet'], 'op'
        )
        == 1
    )
    pv = await store.current_policy_version(ORG_ID, SUSAN)
    assert (
        await store.set_member_credential(
            ORG_ID, SUSAN, KEY_2, ['deepseek', 'sonnet'], 'op'
        )
        == 2
    )
    res = await store.resolve(ORG_ID, SUSAN, pv)
    assert (
        res.upstream_key == KEY_2
        and res.credential_version == 2
        and res.allowed_models == {'deepseek', 'sonnet'}
    )
    async with async_session_maker() as s:
        rows = (
            (await s.execute(select(CustomerGatewayMemberCredential))).scalars().all()
        )
        raw = (
            (
                await s.execute(
                    text('select ciphertext from customer_gateway_member_credential')
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1 and all(KEY_1 not in c and KEY_2 not in c for c in raw)


async def test_members_are_isolated(store):
    await store.set_member_credential(ORG_ID, SUSAN, KEY_1, ['deepseek'], 'op')
    await store.set_member_credential(ORG_ID, DAVID, KEY_D, ['deepseek'], 'op')
    assert (await store.resolve(ORG_ID, DAVID, 1)).upstream_key == KEY_D
    assert (await store.resolve(ORG_ID, SUSAN, 1)).upstream_key == KEY_1


async def test_masked_status_has_no_key_material(store):
    await store.set_member_credential(ORG_ID, SUSAN, KEY_1, ['deepseek'], 'op')
    out = await store.masked_status(ORG_ID)
    assert len(out) == 1 and KEY_1 not in repr(out) and 'ciphertext' not in out[0]


async def test_disable_revokes_outstanding_tokens(store):
    await store.set_member_credential(ORG_ID, SUSAN, KEY_1, ['deepseek'], 'op')
    pv = await store.current_policy_version(ORG_ID, SUSAN)
    new_pv = await store.set_member_enabled(ORG_ID, SUSAN, False, 'op')
    assert new_pv == pv + 1
    with pytest.raises(CustomerGatewayDenied, match='credential_disabled'):
        await store.resolve(ORG_ID, SUSAN, pv)
    await store.set_member_enabled(ORG_ID, SUSAN, True, 'op')
    with pytest.raises(CustomerGatewayDenied, match='token_revoked'):
        await store.resolve(ORG_ID, SUSAN, pv)


async def test_inactive_member_denied_and_unknown_member(store, session_maker):
    await store.set_member_credential(ORG_ID, SUSAN, KEY_1, ['deepseek'], 'op')
    with session_maker() as s:
        s.execute(
            text("update org_member set status='inactive' where user_id=:u"),
            {'u': SUSAN},
        )
        s.commit()
    with pytest.raises(CustomerGatewayDenied, match='membership_inactive'):
        await store.resolve(ORG_ID, SUSAN, 1)
    with pytest.raises(CustomerGatewayDenied, match='no_credential'):
        await store.resolve(ORG_ID, DAVID, 1)
    with pytest.raises(CustomerGatewayDenied):
        await store.set_member_enabled(ORG_ID, DAVID, False, 'op')


async def test_credential_requires_membership(
    store, async_session_maker, create_org, create_user
):
    # Unknown user, and a real user who is not a member of this org: explicit denial.
    with pytest.raises(CustomerGatewayDenied, match='not_a_member'):
        await store.set_member_credential(ORG_ID, uuid4(), KEY_1, ['deepseek'], 'op')
    other_org, outsider = uuid4(), uuid4()
    create_org(id=other_org)
    create_user(id=outsider, current_org_id=other_org)
    with pytest.raises(CustomerGatewayDenied, match='not_a_member'):
        await store.set_member_credential(ORG_ID, outsider, KEY_1, ['deepseek'], 'op')
    # The database guard is unchanged: a direct insert for a non-member still fails.
    async with async_session_maker() as session:
        session.add(
            CustomerGatewayMemberCredential(
                org_id=ORG_ID,
                user_id=outsider,
                ciphertext='x',
                fingerprint='fp',
                allowed_models=[],
                updated_by='op',
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_empty_key_rejected(store):
    with pytest.raises(ValueError):
        await store.set_member_credential(ORG_ID, SUSAN, '  ', ['deepseek'], 'op')


@pytest.mark.parametrize(
    'member_status,allowed',
    [
        ('active', True),
        (None, True),
        ('inactive', False),
        ('invited', False),
        ('disabled', False),
    ],
)
async def test_native_membership_states(store, session_maker, member_status, allowed):
    """NULL is legacy active (OrgMemberStore.add_user_to_org defaults status to None)."""
    await store.set_member_credential(ORG_ID, SUSAN, KEY_1, ['deepseek'], 'op')
    with session_maker() as s:
        s.execute(
            text('update org_member set status=:st where user_id=:u'),
            {'st': member_status, 'u': SUSAN},
        )
        s.commit()
    if allowed:
        assert (await store.resolve(ORG_ID, SUSAN, 1)).upstream_key == KEY_1
    else:
        with pytest.raises(CustomerGatewayDenied, match='membership_inactive'):
            await store.current_policy_version(ORG_ID, SUSAN)


async def test_member_removal_cascades_credential(store, session_maker):
    await store.set_member_credential(ORG_ID, SUSAN, KEY_1, ['deepseek'], 'op')
    with session_maker() as s:
        s.execute(text('delete from org_member where user_id=:u'), {'u': SUSAN})
        s.commit()
    with pytest.raises(CustomerGatewayDenied, match='no_credential'):
        await store.resolve(ORG_ID, SUSAN, 1)


async def test_service_account_cannot_be_provisioned_or_resolved(
    store, session_maker, create_user
):
    await store.set_member_credential(ORG_ID, SUSAN, KEY_1, ['deepseek'], 'op')
    with session_maker() as s:
        s.execute(
            text('update "user" set account_type=\'service\' where id=:u'), {'u': SUSAN}
        )
        s.commit()
    with pytest.raises(CustomerGatewayDenied, match='service_account'):
        await store.resolve(ORG_ID, SUSAN, 1)
    with pytest.raises(CustomerGatewayDenied, match='service_account'):
        await store.current_policy_version(ORG_ID, SUSAN)
    with pytest.raises(CustomerGatewayDenied, match='service_account'):
        await store.set_member_credential(ORG_ID, SUSAN, KEY_2, ['deepseek'], 'op')


async def test_native_api_key_bearer_route_rejects_inference_token(
    store, async_session_maker
):
    """The real SaaS bearer authenticator (PG ApiKeyStore) accepts the member's own API key
    and rejects the same member's inference token."""
    from starlette.requests import Request

    from server.auth.saas_user_auth import saas_user_auth_from_bearer
    from server.customer_gateway.tokens import mint_inference_token
    from storage.api_key_store import ApiKeyStore

    jwt = JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('test_secret'), active=True)]
    )
    inference = mint_inference_token(
        jwt,
        user_id=str(SUSAN),
        org_id=str(ORG_ID),
        sandbox_id='sb1',
        gateway_id='litellm',
        policy_version=1,
        ttl=timedelta(minutes=5),
    )

    def request(token: str) -> Request:
        return Request(
            {
                'type': 'http',
                'method': 'GET',
                'path': '/api/v1/users/me',
                'headers': [(b'authorization', f'Bearer {token}'.encode())],
            }
        )

    with (
        patch('storage.api_key_store.a_session_maker', async_session_maker),
        patch(
            'server.auth.saas_user_auth._require_active_user',
            AsyncMock(return_value=SimpleNamespace(account_type='human')),
        ),
    ):
        api_key = await ApiKeyStore.get_instance().create_api_key(
            str(SUSAN), org_id=ORG_ID
        )
        accepted = await saas_user_auth_from_bearer(request(api_key))
        assert accepted is not None and accepted.user_id == str(SUSAN)
        assert await saas_user_auth_from_bearer(request(inference)) is None


@pytest.mark.parametrize('column', ['is_disabled', 'deletion_pending'])
async def test_inactive_user_denied_immediately_for_existing_token_and_new_mint(
    store, session_maker, monkeypatch, column
):
    """Matches saas_user_auth._require_active_user: an already-issued inference token stops
    working on its next relay request (no TTL wait), and no new token can be minted."""
    from starlette.requests import Request

    from server.customer_gateway import service

    monkeypatch.setenv('CUSTOMER_GATEWAY_ENABLED', 'true')
    monkeypatch.setenv('CUSTOMER_GATEWAY_RELAY_URL', 'https://app/v1')
    monkeypatch.setenv('CUSTOMER_GATEWAY_TOKEN_URL', 'https://app/token')
    jwt = JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('test_secret'), active=True)]
    )
    await store.set_org_gateway(ORG_ID, 'gw', PROFILE, True, 'op')
    await store.set_member_credential(ORG_ID, SUSAN, KEY_1, ['deepseek'], 'op')
    sandbox = SimpleNamespace(
        id='sb1',
        created_by_user_id=str(SUSAN),
        credential_org_id=ORG_ID,
        status=service.SandboxStatus.RUNNING,
    )
    gw = await store.get_enabled_org_gateway(ORG_ID)

    def request(token: str) -> Request:
        return Request(
            {
                'type': 'http',
                'method': 'POST',
                'path': '/v1/chat/completions',
                'headers': [(b'authorization', f'Bearer {token}'.encode())],
            }
        )

    with (
        patch.object(service, '_jwt', return_value=jwt),
        patch.object(service, 'get_sandbox', AsyncMock(return_value=sandbox)),
    ):
        token = await service.mint_for_sandbox(sandbox, gw)
        _, _, res = await service.authorize_inference(request(token))
        assert res.upstream_key == KEY_1

        with session_maker() as s:
            s.execute(
                text(f'update "user" set {column}=true where id=:u'), {'u': SUSAN}
            )
            s.commit()

        with pytest.raises(CustomerGatewayDenied, match='user_inactive'):
            await service.authorize_inference(request(token))
        with pytest.raises(CustomerGatewayDenied, match='user_inactive'):
            await service.mint_for_sandbox(sandbox, gw)
        with pytest.raises(CustomerGatewayDenied, match='user_inactive'):
            await store.set_member_credential(ORG_ID, SUSAN, KEY_2, ['deepseek'], 'op')
        # David (another member) is unaffected.
        await store.set_member_credential(ORG_ID, DAVID, KEY_D, ['deepseek'], 'op')
        assert (await store.resolve(ORG_ID, DAVID, 1)).upstream_key == KEY_D


async def test_enabled_since_moves_only_on_off_to_on(store, session_maker):
    await store.set_org_gateway(ORG_ID, 'gw', PROFILE, True, 'op')
    first = (await store.get_enabled_org_gateway(ORG_ID)).enabled_since
    assert first is not None
    edited = {**PROFILE, 'models': {**PROFILE['models'], 'sonnet': 'anthropic/sonnet'}}
    await store.set_org_gateway(ORG_ID, 'gw', edited, True, 'op')
    assert (await store.get_enabled_org_gateway(ORG_ID)).enabled_since == first
    await store.set_org_gateway(ORG_ID, 'gw', edited, False, 'op')
    with session_maker() as s:
        assert (
            s.execute(
                text('select enabled_since from customer_gateway_org_config')
            ).scalar_one()
            is None
        )
    await store.set_org_gateway(ORG_ID, 'gw', edited, True, 'op')
    assert (await store.get_enabled_org_gateway(ORG_ID)).enabled_since > first
