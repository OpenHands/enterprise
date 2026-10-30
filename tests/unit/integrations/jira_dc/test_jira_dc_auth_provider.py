"""The Jira DC integration reusing a ``jira_dc`` auth provider's identity and token.

Runs against the real test database; only Keycloak, analytics, Redis and the
Jira token endpoint are mocked.
"""

import json
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from pydantic import SecretStr

from integrations.jira_dc.jira_dc_auth_provider import (
    find_user_id_by_jira_dc_key,
    get_jira_dc_access_token,
    get_jira_dc_secret_hint,
)
from integrations.jira_dc.jira_dc_conversation_secret_enricher import (
    JiraDcConversationSecretEnricher,
)
from integrations.jira_dc.jira_dc_manager import JiraDcManager
from integrations.jira_dc.jira_dc_user_token import get_user_jira_dc_token
from openhands.app_server.app_conversation.app_conversation_models import (
    ConversationTrigger,
)
from openhands.app_server.integrations.service_types import ProviderType
from server.routes.integration.jira_dc import JiraDcLinkCreate, create_workspace_link
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import OAuthProviderDetails
from storage.auth_provider_user import AuthProviderUser
from storage.jira_dc_integration_store import JiraDcIntegrationStore

JIRA_HOST = 'jira.example.com'
JIRA_BASE_URL = f'https://{JIRA_HOST}'


@pytest.fixture
def jira_dc_db(oauth_v2_stores, async_session_maker):
    with patch(
        'storage.jira_dc_integration_store.a_session_maker', async_session_maker
    ):
        yield


@pytest.fixture
def create_jira_dc_provider(oauth_v2_stores, async_session_maker):
    async def _create(
        host: str = JIRA_HOST,
        base_path: str = '',
        named_secret: str | None = 'JIRA_DC_TOKEN',
        provider_type: ProviderType = ProviderType.JIRA_DC,
    ) -> AuthProvider:
        base = f'https://{host}{base_path}'
        provider = AuthProvider(
            title='Login with Jira DC',
            is_idp=True,
            details=OAuthProviderDetails(
                provider_type=provider_type,
                client_id='client',
                client_secret=SecretStr('secret'),
                authorization_url=f'{base}/rest/oauth2/latest/authorize',
                token_url=f'{base}/rest/oauth2/latest/token',
                userinfo_url=f'{base}/rest/api/2/myself',
                scopes=['WRITE'],
                named_secret=named_secret,
                attribute_mapping={'sub': 'key', 'email': 'emailAddress'},
            ),
        )
        async with async_session_maker() as session:
            session.add(provider)
            await session.commit()
            await session.refresh(provider)
        return provider

    return _create


@pytest.fixture
def link_user(store_oauth_token, async_session_maker):
    """Link ``user_id`` to ``provider`` as Jira user ``jira_key``, the way an
    OAuth v2 login through it does."""

    async def _link(
        user_id: UUID,
        provider: AuthProvider,
        jira_key: str = 'jdoe',
        access_token: str = 'auth-provider-token',
        access_token_expires_at: datetime | None = None,
    ) -> None:
        async with async_session_maker() as session:
            session.add(
                AuthProviderUser(
                    oauth_provider_id=provider.id,
                    user_id=user_id,
                    external_subject_id=jira_key,
                )
            )
            await session.commit()
        await store_oauth_token(
            user_id, provider, access_token, access_token_expires_at
        )

    return _link


async def _create_workspace(admin_user_id: str = 'admin') -> int:
    workspace = await JiraDcIntegrationStore().create_workspace(
        name=JIRA_HOST,
        admin_user_id=admin_user_id,
        org_id=None,
        encrypted_webhook_secret='webhook-secret',
        svc_acc_email='svc@example.com',
        encrypted_svc_acc_api_key='svc-key',
    )
    return workspace.id


# ---------------------------------------------------------------------------
# Token, identity and hint lookups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_access_token_comes_from_the_provider_on_the_same_host(
    oauth_v2_login_on, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider())

    assert await get_jira_dc_access_token(str(user.id), JIRA_HOST) == (
        'auth-provider-token'
    )


@pytest.mark.asyncio
async def test_access_token_is_never_taken_from_another_jira_host(
    oauth_v2_login_on, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider(host='other.example.com'))

    assert await get_jira_dc_access_token(str(user.id), JIRA_HOST) is None
    assert await get_jira_dc_access_token(str(user.id), None) is None


@pytest.mark.asyncio
async def test_access_token_is_ignored_with_oauth_v2_login_off(
    oauth_v2_login_off, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider())

    assert await get_jira_dc_access_token(str(user.id), JIRA_HOST) is None


@pytest.mark.asyncio
async def test_access_token_is_none_when_the_refresh_fails(
    oauth_v2_login_on, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(
        user.id,
        await create_jira_dc_provider(),
        access_token_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    with patch(
        'server.auth.oauth_v2_refresh.refresh_oauth_token',
        AsyncMock(side_effect=ValueError('refresh rejected')),
    ):
        assert await get_jira_dc_access_token(str(user.id), JIRA_HOST) is None


@pytest.mark.asyncio
async def test_jira_key_resolves_to_the_linked_user_on_that_host(
    oauth_v2_login_on, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider(), jira_key='jdoe')

    assert await find_user_id_by_jira_dc_key('jdoe', JIRA_HOST) == str(user.id)
    assert await find_user_id_by_jira_dc_key('jdoe', 'other.example.com') is None
    assert await find_user_id_by_jira_dc_key('someone-else', JIRA_HOST) is None


@pytest.mark.asyncio
async def test_jira_key_linked_to_two_users_matches_neither(
    oauth_v2_login_on, create_user, create_jira_dc_provider, link_user
):
    await link_user(create_user().id, await create_jira_dc_provider(), 'jdoe')
    await link_user(create_user().id, await create_jira_dc_provider(), 'jdoe')

    assert await find_user_id_by_jira_dc_key('jdoe', JIRA_HOST) is None


@pytest.mark.asyncio
async def test_jira_key_ignores_providers_of_other_types(
    oauth_v2_login_on, create_user, create_jira_dc_provider, link_user
):
    provider = await create_jira_dc_provider(provider_type=ProviderType.JIRA)
    await link_user(create_user().id, provider, 'jdoe')

    assert await find_user_id_by_jira_dc_key('jdoe', JIRA_HOST) is None


@pytest.mark.asyncio
async def test_secret_hint_carries_the_base_url_and_named_secret(
    oauth_v2_login_on, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    provider = await create_jira_dc_provider(base_path='/jira', named_secret='MY_JIRA')
    await link_user(user.id, provider)

    hint = await get_jira_dc_secret_hint(str(user.id))

    assert hint is not None
    assert hint.base_url == f'{JIRA_BASE_URL}/jira'
    assert hint.secret_name == 'MY_JIRA'


@pytest.mark.asyncio
async def test_no_secret_hint_when_the_provider_exposes_no_secret(
    oauth_v2_login_on, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider(named_secret=None))

    assert await get_jira_dc_secret_hint(str(user.id)) is None


# ---------------------------------------------------------------------------
# #1 Token resolution and workspace linking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_token_prefers_the_auth_provider_token(
    oauth_v2_login_on, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider())
    integration_store = MagicMock()
    integration_store.get_user_oauth_tokens = AsyncMock()

    with patch(
        'integrations.jira_dc.jira_dc_user_token.JIRA_DC_BASE_URL', JIRA_BASE_URL
    ):
        token = await get_user_jira_dc_token(
            keycloak_user_id=str(user.id),
            workspace_id=1,
            token_manager=MagicMock(),
            store=integration_store,
        )

    assert token.access_token.get_secret_value() == 'auth-provider-token'
    integration_store.get_user_oauth_tokens.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_token_falls_back_to_the_integration_token(
    oauth_v2_login_on, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider(host='other.example.com'))
    integration_store = MagicMock()
    integration_store.get_user_oauth_tokens = AsyncMock(
        return_value=('enc-access', None, 0, 0)
    )
    token_manager = MagicMock()
    token_manager.decrypt_text.return_value = 'integration-token'

    with patch(
        'integrations.jira_dc.jira_dc_user_token.JIRA_DC_BASE_URL', JIRA_BASE_URL
    ):
        token = await get_user_jira_dc_token(
            keycloak_user_id=str(user.id),
            workspace_id=1,
            token_manager=token_manager,
            store=integration_store,
        )

    assert token.access_token.get_secret_value() == 'integration-token'


@contextmanager
def _link_request(user_id: UUID):
    """Yield the Redis mock that the integration's own OAuth flow writes its
    state to."""
    user_auth = MagicMock()
    user_auth.get_user_id = AsyncMock(return_value=str(user_id))
    user_auth.get_user_email = AsyncMock(return_value='jdoe@example.com')
    redis_client = MagicMock()
    redis_client.setex.return_value = True
    module = 'server.routes.integration.jira_dc'
    with ExitStack() as stack:
        stack.enter_context(patch(f'{module}.JIRA_DC_ENABLE_OAUTH', True))
        stack.enter_context(patch(f'{module}.JIRA_DC_BASE_URL', JIRA_BASE_URL))
        stack.enter_context(
            patch(f'{module}.get_user_auth', AsyncMock(return_value=user_auth))
        )
        stack.enter_context(patch(f'{module}.redis_client', redis_client))
        stack.enter_context(patch(f'{module}.get_analytics_service', return_value=None))
        yield redis_client


@pytest.mark.asyncio
async def test_workspace_link_skips_the_jira_consent_for_an_auth_provider_user(
    oauth_v2_login_on, jira_dc_db, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider(), jira_key='jdoe')
    workspace_id = await _create_workspace()
    with _link_request(user.id) as redis_client:
        response = await create_workspace_link(
            MagicMock(), JiraDcLinkCreate(workspace_name=JIRA_HOST)
        )

    assert json.loads(response.body) == {
        'success': True,
        'redirect': False,
        'authorizationUrl': '',
    }
    redis_client.setex.assert_not_called()
    link = await JiraDcIntegrationStore().get_active_user_by_keycloak_id_and_workspace(
        str(user.id), workspace_id
    )
    assert link is not None
    assert link.jira_dc_user_id == 'jdoe'


@pytest.mark.asyncio
async def test_workspace_link_uses_jira_consent_without_a_matching_provider(
    oauth_v2_login_on, jira_dc_db, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider(host='other.example.com'))
    await _create_workspace()
    with _link_request(user.id) as redis_client:
        response = await create_workspace_link(
            MagicMock(), JiraDcLinkCreate(workspace_name=JIRA_HOST)
        )

    assert json.loads(response.body)['redirect'] is True
    redis_client.setex.assert_called_once()


# ---------------------------------------------------------------------------
# #2 Webhook user matching
# ---------------------------------------------------------------------------


def _manager() -> JiraDcManager:
    manager = JiraDcManager(MagicMock())
    manager.integration_store = JiraDcIntegrationStore()
    return manager


@pytest.mark.asyncio
async def test_webhook_links_an_auth_provider_user_who_never_linked(
    oauth_v2_login_on, jira_dc_db, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider(), jira_key='jdoe')
    workspace_id = await _create_workspace()
    user_auth = MagicMock()

    with (
        patch('integrations.jira_dc.jira_dc_manager.JIRA_DC_ENABLE_OAUTH', True),
        patch(
            'integrations.jira_dc.jira_dc_manager.get_user_auth_from_keycloak_id',
            AsyncMock(return_value=user_auth),
        ),
    ):
        jira_dc_user, resolved_auth = await _manager().authenticate_user(
            'jdoe@example.com', 'jdoe', workspace_id
        )

    assert jira_dc_user is not None
    assert jira_dc_user.keycloak_user_id == str(user.id)
    assert jira_dc_user.jira_dc_user_id == 'jdoe'
    assert jira_dc_user.status == 'active'
    assert resolved_auth is user_auth


@pytest.mark.asyncio
async def test_webhook_does_not_relink_a_user_who_unlinked(
    oauth_v2_login_on, jira_dc_db, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider(), jira_key='jdoe')
    workspace_id = await _create_workspace()
    await JiraDcIntegrationStore().create_workspace_link(
        keycloak_user_id=str(user.id),
        jira_dc_user_id='unavailable',
        jira_dc_workspace_id=workspace_id,
        status='inactive',
    )

    with patch('integrations.jira_dc.jira_dc_manager.JIRA_DC_ENABLE_OAUTH', True):
        result = await _manager().authenticate_user(
            'jdoe@example.com', 'jdoe', workspace_id
        )

    assert result == (None, None)


@pytest.mark.asyncio
async def test_webhook_does_not_link_with_oauth_v2_login_off(
    oauth_v2_login_off, jira_dc_db, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider(), jira_key='jdoe')
    workspace_id = await _create_workspace()

    with patch('integrations.jira_dc.jira_dc_manager.JIRA_DC_ENABLE_OAUTH', True):
        result = await _manager().authenticate_user(
            'jdoe@example.com', 'jdoe', workspace_id
        )

    assert result == (None, None)
    assert (
        await JiraDcIntegrationStore().get_user_by_keycloak_id_and_workspace(
            str(user.id), workspace_id
        )
        is None
    )


# ---------------------------------------------------------------------------
# #3 Prompt hint for the generic named secret
# ---------------------------------------------------------------------------


async def _enrich(user_id: UUID):
    user_context = SimpleNamespace(
        get_user_id=AsyncMock(return_value=str(user_id)),
        user_auth=SimpleNamespace(get_effective_org_id=AsyncMock(return_value=None)),
    )
    return await JiraDcConversationSecretEnricher().enrich(
        user_context=user_context,
        user=MagicMock(),
        trigger=ConversationTrigger.GUI,
        system_message_suffix='Existing instructions.',
        web_url='https://openhands.example.com',
        jwt_service=MagicMock(),
        access_token_hard_timeout=timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_enricher_describes_the_auth_provider_token_without_a_workspace_link(
    oauth_v2_login_on, jira_dc_db, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider())

    with patch(
        'integrations.jira_dc.jira_dc_conversation_secret_enricher.JIRA_DC_ENABLE_OAUTH',
        True,
    ):
        enrichment = await _enrich(user.id)

    assert enrichment.secrets == {}
    suffix = enrichment.system_message_suffix or ''
    assert suffix.startswith('Existing instructions.')
    assert f'Base URL: {JIRA_BASE_URL}' in suffix
    assert 'Bearer $JIRA_DC_TOKEN' in suffix


@pytest.mark.asyncio
async def test_enricher_email_mode_describes_the_auth_provider_token(
    oauth_v2_login_on, jira_dc_db, create_user, create_jira_dc_provider, link_user
):
    user = create_user()
    await link_user(user.id, await create_jira_dc_provider())
    await _create_workspace()

    with patch(
        'integrations.jira_dc.jira_dc_conversation_secret_enricher.JIRA_DC_ENABLE_OAUTH',
        False,
    ):
        enrichment = await _enrich(user.id)

    suffix = enrichment.system_message_suffix or ''
    assert 'Bearer $JIRA_DC_TOKEN' in suffix
    assert 'email-linking mode' not in suffix
