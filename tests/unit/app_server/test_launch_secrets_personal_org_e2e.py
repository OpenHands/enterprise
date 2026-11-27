"""End-to-end launch-secret delivery against real PostgreSQL stores (OSS-15493).

Unlike ``test_live_status_app_conversation_service.py`` (which drives
``_launch_custom_secrets`` with a mocked ``user_context``), this exercises the
whole launch secret path with nothing stubbed between the code under test and
the database:

* a real ``SaasSecretsStore`` writes/reads the member's **personal** secret
  (``is_org_shared=False``) through real encryption into real ``custom_secrets``
  rows;
* a real ``OrgSecretsStore`` writes the **org-shared** secret;
* a real ``SaasUserAuth`` resolves ``get_effective_org_id`` from real
  ``org`` / ``org_member`` rows;
* the **org** profile scope that triggers the bug is resolved by the real
  ``locate_agent_profile`` over a real ``org.agent_profiles`` envelope, not a
  hand-set literal;
* the real ``LiveStatusAppConversationService._launch_custom_secrets`` decides
  what the sandbox receives.

The regression (OHE-3935): an active **org-scoped** profile with no
``secret_refs`` list makes the launch drop the member's personal secrets. The
fix exempts a **personal workspace org** (``org.id == member.id``), where the
lone member is the only one the exclusion could ever shield.
"""

from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.app_server.app_conversation.live_status_app_conversation_service import (  # noqa: E501
    LiveStatusAppConversationService,
)
from openhands.app_server.secrets.secrets_models import CustomSecret, Secrets
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.user.auth_user_context import AuthUserContext
from openhands.app_server.user.user_models import UserInfo
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth.saas_user_auth import SaasUserAuth
from storage.agent_profile_resolution import locate_agent_profile
from storage.org import Org
from storage.org_member import OrgMember
from storage.org_secrets_store import OrgSecretsStore
from storage.role import Role
from storage.saas_secrets_store import SaasSecretsStore
from storage.user import User

PERSONAL_SECRET = 'MY_PERSONAL_DATADOG_KEY'
ORG_SECRET = 'TEAM_SHARED_SONARQUBE_TOKEN'
PROFILE_ID = str(uuid4())


async def _async_empty(*args, **kwargs) -> str:
    """Stand in for system-key provisioning, which is orthogonal to the
    custom-secret path under test."""
    return ''


def _org_profile_envelope() -> dict:
    """An org-scoped profile with no ``secret_refs`` list, marked active.

    This is the "No profile restriction" shape from OHE-3935: ``secret_refs``
    defaults to ``None``, which is exactly what flips the launch into
    org-scoped secret delivery.
    """
    return {
        'profiles': {
            PROFILE_ID: {
                'id': PROFILE_ID,
                'name': 'openhands',
                'llm_profile_ref': 'openhands_default',
            }
        },
        'active': PROFILE_ID,
    }


@pytest.fixture
def jwt_service() -> JwtService:
    return JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('fake-test-key'), active=True)]
    )


@pytest.fixture
def point_stores_at_test_db(monkeypatch, async_session_maker, jwt_service):
    for module in (
        'server.auth.saas_user_auth',
        'storage.user_store',
        'storage.org_member_store',
        'storage.org_store',
        'storage.saas_secrets_store',
        'storage.org_secrets_store',
    ):
        monkeypatch.setattr(f'{module}.a_session_maker', async_session_maker)
    # One encryption key for both the test's writes and the launch path's reads:
    # the production read path builds its own store via ``get_jwt_service`` in
    # ``SaasSecretsStore.get_instance``, so the test must encrypt with the same
    # service or the read fails to decrypt.
    monkeypatch.setattr('storage.encrypt_utils.get_jwt_service', lambda: jwt_service)


async def _seed(
    *,
    session_maker,
    jwt_service,
    create_org,
    create_user,
    personal_org: bool,
):
    """Seed a member whose active org profile is org-scoped, a personal secret
    and an org-shared secret. ``personal_org`` makes ``org.id == user.id``."""
    user_id = uuid4()
    if personal_org:
        # Personal workspace org: the org row shares the member's id.
        org = create_org(id=user_id, agent_profiles=_org_profile_envelope())
    else:
        org = create_org(agent_profiles=_org_profile_envelope())
    user = create_user(id=user_id, current_org_id=org.id)

    with session_maker() as session:
        role = Role(name=f'member-{uuid4().hex[:8]}', rank=10)
        session.add(role)
        session.flush()
        session.add(
            OrgMember(
                org_id=org.id, user_id=user.id, role_id=role.id, llm_api_key='fake'
            )
        )
        session.commit()

    # Real personal secret (is_org_shared=False), written through real encryption.
    personal_store = SaasSecretsStore(str(user.id), jwt_service)
    await personal_store.store(
        Secrets(
            custom_secrets={
                PERSONAL_SECRET: CustomSecret(
                    secret=SecretStr('personal-value'),
                    description='my own key',
                )
            }
        )
    )
    # Real org-shared secret (is_org_shared=True).
    await OrgSecretsStore(org.id, jwt_service).create_shared(
        name=ORG_SECRET,
        value='shared-value',
        description=None,
        created_by_user_id=str(user.id),
    )
    return org, user


def _service_for(user, jwt_service) -> LiveStatusAppConversationService:
    """A real service wired to a real auth-backed user context.

    ``object.__new__`` matches how this file's sibling tests build the service
    when only ``user_context`` matters (``_launch_custom_secrets`` touches
    nothing else). The context itself is fully real: ``get_secrets`` and
    ``get_effective_org_id`` reach Postgres.
    """
    auth = SaasUserAuth(refresh_token=SecretStr('fake-refresh'), user_id=str(user.id))
    service = object.__new__(LiveStatusAppConversationService)
    object.__setattr__(service, 'user_context', AuthUserContext(user_auth=auth))
    return service


def _assert_profile_resolves_org_scope(org: Org, user: User):
    """The seeded active profile must resolve in **org** scope the real way,
    so the test's ``active_agent_profile_scope='org'`` is the value real
    resolution would produce, not an arbitrary literal."""
    member = OrgMember(org_id=org.id, user_id=user.id, role_id=1, llm_api_key='fake')
    located = locate_agent_profile(org, member, PROFILE_ID)
    assert located is not None, 'seeded profile must resolve'
    assert located[1] == 'org', 'seeded profile must resolve in org scope'


@pytest.mark.asyncio
async def test_personal_workspace_org_launch_keeps_personal_secret(
    point_stores_at_test_db,
    session_maker,
    async_session_maker,
    create_org,
    create_user,
    jwt_service,
    monkeypatch,
):
    # System key provisioning is orthogonal to the custom-secret path under test.
    monkeypatch.setattr(
        SaasUserAuth, '_get_openhands_api_key', _async_empty, raising=False
    )
    org, user = await _seed(
        session_maker=session_maker,
        jwt_service=jwt_service,
        create_org=create_org,
        create_user=create_user,
        personal_org=True,
    )
    _assert_profile_resolves_org_scope(org, user)
    service = _service_for(user, jwt_service)
    user_info = UserInfo(id=str(user.id), active_agent_profile_scope='org')

    secrets = await service._launch_custom_secrets(user_info)

    assert PERSONAL_SECRET in secrets, (
        'a personal-workspace-org member must still receive their own personal '
        'secret at launch'
    )
    assert ORG_SECRET in secrets


@pytest.mark.asyncio
async def test_multi_member_org_launch_still_excludes_personal_secret(
    point_stores_at_test_db,
    session_maker,
    async_session_maker,
    create_org,
    create_user,
    jwt_service,
    monkeypatch,
):
    monkeypatch.setattr(
        SaasUserAuth, '_get_openhands_api_key', _async_empty, raising=False
    )
    org, user = await _seed(
        session_maker=session_maker,
        jwt_service=jwt_service,
        create_org=create_org,
        create_user=create_user,
        personal_org=False,
    )
    _assert_profile_resolves_org_scope(org, user)
    service = _service_for(user, jwt_service)
    user_info = UserInfo(id=str(user.id), active_agent_profile_scope='org')

    secrets = await service._launch_custom_secrets(user_info)

    assert PERSONAL_SECRET not in secrets, (
        'a real multi-member org with an org-scoped profile must keep the '
        'exclusion: personal secrets are not delivered'
    )
    assert ORG_SECRET in secrets
