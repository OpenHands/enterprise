"""Organization-bound sandbox lookups against real PostgreSQL secret stores."""

from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from openhands.app_server.sandbox.sandbox_models import SandboxInfo, SandboxStatus
from openhands.app_server.sandbox.sandbox_router import get_secret_value
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.shared import server_config
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth.saas_user_auth import SaasUserAuth
from storage.org_member import OrgMember
from storage.org_secrets_store import OrgSecretsStore
from storage.role import Role
from storage.saas_secrets_store import SaasSecretsStore
from storage.user import User


@pytest.fixture
async def org_lookup(
    monkeypatch, async_session_maker, session_maker, create_org, create_user
):
    org_a, org_b = create_org(), create_org()
    user = create_user(current_org_id=org_a.id)
    with session_maker() as session:
        role = Role(name='test-member', rank=10)
        session.add(role)
        session.flush()
        for org in (org_a, org_b):
            session.add(
                OrgMember(
                    org_id=org.id, user_id=user.id, role_id=role.id, llm_api_key='fake'
                )
            )
        session.commit()

    for module in (
        'server.auth.saas_user_auth',
        'storage.user_store',
        'storage.org_member_store',
        'storage.org_store',
        'storage.saas_secrets_store',
        'storage.org_secrets_store',
    ):
        monkeypatch.setattr(f'{module}.a_session_maker', async_session_maker)
    monkeypatch.setattr(
        server_config, 'user_auth_class', 'server.auth.saas_user_auth.SaasUserAuth'
    )
    jwt = JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('fake-test-key'), active=True)]
    )

    async def secrets_store(user_id, effective_org_id=None):
        return SaasSecretsStore(user_id, jwt, effective_org_id=effective_org_id)

    monkeypatch.setattr(SaasSecretsStore, 'get_instance', secrets_store)
    # System key provisioning is separate from the custom-secret lookup under test.
    monkeypatch.setattr(
        SaasUserAuth, '_get_openhands_api_key', AsyncMock(return_value='')
    )
    for org, value in ((org_a, 'fake-a'), (org_b, 'fake-b')):
        await OrgSecretsStore(org.id, jwt).create_shared(
            name='DB_SYNC_CANARY',
            value=value,
            description=None,
            created_by_user_id=str(user.id),
        )
    sandbox = SandboxInfo(
        id='org-bound-sandbox',
        created_by_user_id=str(user.id),
        sandbox_spec_id='test',
        status=SandboxStatus.RUNNING,
        session_api_key='fake-session',
        credential_org_id=org_a.id,
    )
    return sandbox, org_a, org_b, user


@pytest.mark.asyncio
async def test_owner_switch_does_not_retarget_sandbox_secrets(
    org_lookup, session_maker
):
    sandbox, org_a, org_b, user = org_lookup
    values = []
    for org in (org_a, org_b, org_a):
        with session_maker() as session:
            stored_user = session.get(User, user.id)
            stored_user.current_org_id = org.id
            session.commit()
        response = await get_secret_value('DB_SYNC_CANARY', sandbox)
        values.append(response.body)
    assert values == [b'fake-a', b'fake-a', b'fake-a']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'change',
    [
        'removed',
        'inactive',
        'disabled',
        'suspended',
        'different-owner',
        'missing-owner',
        'unbound',
        'nonmember-org',
    ],
)
@pytest.mark.parametrize('lookup', ['value', 'names'])
async def test_lookup_requires_live_origin_membership(
    org_lookup,
    session_maker,
    create_org,
    create_user,
    change,
    lookup,
    caplog,
    monkeypatch,
):
    from fastapi import HTTPException

    from openhands.app_server.sandbox.sandbox_router import list_secret_names
    from server.auth.saas_user_auth import logger
    from storage.org import Org

    monkeypatch.setattr(logger, 'propagate', True)
    sandbox, org_a, org_b, user = org_lookup
    assert (await get_secret_value('DB_SYNC_CANARY', sandbox)).body == b'fake-a'
    with session_maker() as session:
        if change == 'removed':
            session.delete(session.get(OrgMember, (org_a.id, user.id)))
        elif change == 'inactive':
            session.get(OrgMember, (org_a.id, user.id)).status = 'inactive'
        elif change == 'disabled':
            session.get(User, user.id).is_disabled = True
        elif change == 'suspended':
            session.get(Org, org_a.id).status = 'suspended'
        elif change == 'different-owner':
            sandbox.created_by_user_id = str(create_user(current_org_id=org_b.id).id)
        elif change == 'missing-owner':
            from uuid import uuid4

            sandbox.created_by_user_id = str(uuid4())
        elif change == 'unbound':
            sandbox.credential_org_id = None
        elif change == 'nonmember-org':
            sandbox.credential_org_id = create_org().id
        session.commit()
    with pytest.raises(HTTPException) as exc:
        if lookup == 'value':
            await get_secret_value('DB_SYNC_CANARY', sandbox)
        else:
            await list_secret_names(sandbox)
    assert exc.value.status_code == 403
    assert 'fake-a' not in str(exc.value.detail)
    assert 'fake-b' not in str(exc.value.detail)
    reasons = {
        'removed': 'not_a_member',
        'inactive': 'inactive',
        'disabled': 'disabled',
        'suspended': 'suspended',
        'different-owner': 'not_a_member',
        'missing-owner': 'unknown_owner',
        'unbound': 'unbound',
        'nonmember-org': 'not_a_member',
    }
    records = [
        r for r in caplog.records if r.message == 'sandbox_credential_access_denied'
    ]
    assert records[-1].reason == reasons[change]
    assert records[-1].sandbox_id == sandbox.id
    assert 'fake-a' not in caplog.text
    assert 'fake-b' not in caplog.text


@pytest.mark.asyncio
async def test_membership_in_other_org_is_not_required(org_lookup, session_maker):
    sandbox, org_a, org_b, user = org_lookup
    with session_maker() as session:
        session.delete(session.get(OrgMember, (org_b.id, user.id)))
        session.get(User, user.id).current_org_id = org_b.id
        session.commit()
    assert (await get_secret_value('DB_SYNC_CANARY', sandbox)).body == b'fake-a'


@pytest.mark.asyncio
async def test_bound_context_survives_sandbox_reload(org_lookup, async_session_maker):
    from unittest.mock import AsyncMock

    from openhands.app_server.sandbox.remote_sandbox_service import RemoteSandboxService
    from openhands.app_server.sandbox.sandbox_store import REMOTE_BACKEND, StoredSandbox

    sandbox, org_a, _, user = org_lookup
    async with async_session_maker() as session:
        session.add(
            StoredSandbox(
                id=sandbox.id,
                backend=REMOTE_BACKEND,
                created_by_user_id=str(user.id),
                credential_org_id=org_a.id,
                sandbox_spec_id='test',
            )
        )
        await session.commit()
    async with async_session_maker() as session:
        stored = await session.get(StoredSandbox, sandbox.id)
        assert stored.credential_org_id == org_a.id
        # Provider state cannot override the app-owned organization binding.
        service = RemoteSandboxService.__new__(RemoteSandboxService)
        service.user_context = AsyncMock()
        service.user_context.get_user_id.return_value = str(user.id)
        info = service._to_sandbox_info(
            stored, {'status': 'running', 'session_api_key': 'fake-session'}
        )
        assert info.credential_org_id == org_a.id
        assert (await get_secret_value('DB_SYNC_CANARY', info)).body == b'fake-a'


@pytest.mark.asyncio
@pytest.mark.parametrize('context_kind', ['api-key', 'header', 'resolver'])
async def test_launch_captures_effective_org_instead_of_current_org(
    org_lookup, session_maker, context_kind
):
    from integrations.resolver_context import ResolverUserContext
    from openhands.app_server.sandbox.sandbox_store import get_credential_org_id
    from openhands.app_server.user.auth_user_context import AuthUserContext

    _, org_a, org_b, user = org_lookup
    with session_maker() as session:
        session.get(User, user.id).current_org_id = org_b.id
        session.commit()
    auth = await SaasUserAuth.get_for_user(str(user.id))
    if context_kind == 'api-key':
        auth.api_key_org_id = org_a.id
        context = AuthUserContext(user_auth=auth)
    elif context_kind == 'header':
        auth._x_org_id_header = str(org_a.id)
        context = AuthUserContext(user_auth=auth)
    else:
        context = ResolverUserContext(auth, resolver_org_id=org_a.id)
    assert await get_credential_org_id(context) == org_a.id


def conversation_service(context, sandbox, strategy):
    from types import SimpleNamespace

    from openhands.app_server.app_conversation.live_status_app_conversation_service import (
        LiveStatusAppConversationService,
    )

    service = LiveStatusAppConversationService.__new__(LiveStatusAppConversationService)
    service.user_context = context
    service.app_mode = 'saas'
    service.max_num_conversations_per_sandbox = 20
    service.sandbox_service = SimpleNamespace(
        search_sandboxes=AsyncMock(
            return_value=SimpleNamespace(items=[sandbox], next_page_id=None)
        ),
        get_sandbox=AsyncMock(return_value=sandbox),
        resume_sandbox=AsyncMock(),
    )
    service._get_sandbox_grouping_strategy = AsyncMock(return_value=strategy)
    service._get_conversation_counts_by_sandbox = AsyncMock(return_value={})
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'strategy',
    ['ADD_TO_ANY', 'GROUP_BY_NEWEST', 'LEAST_RECENTLY_USED', 'FEWEST_CONVERSATIONS'],
)
@pytest.mark.parametrize('binding', ['same-org', 'other-org', 'unbound'])
async def test_grouping_reuses_only_caller_org(org_lookup, strategy, binding):
    from openhands.app_server.settings.settings_models import SandboxGroupingStrategy
    from openhands.app_server.user.auth_user_context import AuthUserContext

    sandbox, org_a, org_b, user = org_lookup
    auth = await SaasUserAuth.get_for_user(str(user.id))
    auth.api_key_org_id = org_b.id
    sandbox.credential_org_id = {
        'same-org': org_b.id,
        'other-org': org_a.id,
        'unbound': None,
    }[binding]
    service = conversation_service(
        AuthUserContext(user_auth=auth), sandbox, SandboxGroupingStrategy[strategy]
    )
    result = await service._find_running_sandbox_for_user()
    assert result == (sandbox if binding == 'same-org' else None)


@pytest.mark.asyncio
@pytest.mark.parametrize('binding', ['same-org', 'other-org', 'unbound'])
@pytest.mark.parametrize('paused', [False, True])
@pytest.mark.parametrize('inherited', [False, True])
async def test_explicit_sandbox_requires_caller_org(
    org_lookup, binding, paused, inherited
):
    from types import SimpleNamespace

    from openhands.app_server.errors import SandboxError
    from openhands.app_server.user.auth_user_context import AuthUserContext

    sandbox, org_a, org_b, user = org_lookup
    auth = await SaasUserAuth.get_for_user(str(user.id))
    auth.api_key_org_id = org_b.id
    sandbox.credential_org_id = {
        'same-org': org_b.id,
        'other-org': org_a.id,
        'unbound': None,
    }[binding]
    if paused:
        sandbox.status = SandboxStatus.PAUSED
    service = conversation_service(AuthUserContext(user_auth=auth), sandbox, None)
    from openhands.app_server.app_conversation.app_conversation_models import (
        AppConversationStartRequest,
    )

    request = AppConversationStartRequest(sandbox_id=None if inherited else sandbox.id)
    if inherited:
        parent = SimpleNamespace(
            sandbox_id=sandbox.id,
            selected_repository=None,
            selected_branch=None,
            git_provider=None,
            llm_model=None,
        )
        service._inherit_configuration_from_parent(request, parent)
        assert request.sandbox_id == sandbox.id
    task = SimpleNamespace(request=request, sandbox_id=None)
    stream = service._wait_for_sandbox_start(task)
    if binding == 'same-org':
        assert await anext(stream) is task
        assert task.sandbox_id == sandbox.id
        await stream.aclose()
    else:
        with pytest.raises(SandboxError, match='requested organization'):
            await anext(stream)
        assert task.sandbox_id is None
        service.sandbox_service.resume_sandbox.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('app_mode', ['saas', 'openhands'])
async def test_unscoped_context_cannot_reuse_legacy_enterprise_sandbox(
    org_lookup, app_mode
):
    from types import SimpleNamespace

    from openhands.app_server.errors import SandboxError
    from openhands.app_server.settings.settings_models import SandboxGroupingStrategy

    sandbox, _, _, user = org_lookup
    sandbox.credential_org_id = None
    if app_mode == 'openhands':
        from openhands.app_server.user.auth_user_context import AuthUserContext
        from openhands.app_server.user_auth.default_user_auth import DefaultUserAuth

        context = AuthUserContext(user_auth=DefaultUserAuth())
        sandbox.created_by_user_id = await context.get_user_id()
    else:
        context = SimpleNamespace(get_user_id=AsyncMock(return_value=str(user.id)))
    service = conversation_service(context, sandbox, SandboxGroupingStrategy.ADD_TO_ANY)
    service.app_mode = app_mode
    result = await service._find_running_sandbox_for_user()
    assert result == (sandbox if app_mode == 'openhands' else None)
    task = SimpleNamespace(
        request=SimpleNamespace(sandbox_id=sandbox.id, conversation_id=None),
        sandbox_id=None,
    )
    stream = service._wait_for_sandbox_start(task)
    if app_mode == 'saas':
        with pytest.raises(SandboxError, match='requested organization'):
            await anext(stream)
        assert task.sandbox_id is None
    else:
        assert await anext(stream) is task
        await stream.aclose()
