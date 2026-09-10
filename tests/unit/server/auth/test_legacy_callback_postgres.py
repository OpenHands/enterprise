"""Exercise a settings-only legacy account through the real Keycloak callback."""

import asyncio
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException, Request
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.auth import mode
from server.auth.keycloak.token_manager import KeycloakUserInfo
from server.auth.local.accounts import create_local_account
from server.auth.mode import initialize_authentication
from server.routes.auth import keycloak_callback
from storage.api_key import ApiKey
from storage.org import Org
from storage.org_member import OrgMember
from storage.slack_user import SlackUser
from storage.user import User
from storage.user_settings import UserSettings
from storage.user_store import UserStore
from tests.unit.server.auth.test_local_auth_postgres import (
    database_name as database_name,
)
from tests.unit.server.auth.test_local_auth_postgres import (
    migrate,
)
from tests.unit.server.auth.test_local_auth_postgres import (
    migration_logs as migration_logs,
)
from tests.unit.server.auth.test_local_auth_postgres import (
    postgres_template as postgres_template,
)

PG_PORT = os.environ.get('OH_AUTH_TEST_POSTGRES_PORT')
pytestmark = pytest.mark.skipif(not PG_PORT, reason='Requires isolated PostgreSQL 17.')


async def test_settings_only_user_preserves_graph_and_credentials_at_callback(
    database_name,  # noqa: F811
    migration_logs,  # noqa: F811
    monkeypatch,  # noqa: F811
):
    migrate(database_name, 'head', migration_logs)
    engine = create_async_engine(
        f'postgresql+asyncpg://postgres@127.0.0.1:{PG_PORT}/{database_name}'
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid4()
    legacy_model = 'provider/legacy-custom-model'
    from openhands.app_server.services.jwt_service import JwtService
    from openhands.app_server.utils.encryption_key import EncryptionKey
    from storage.encrypt_utils import encrypt_legacy_value

    jwt_service = JwtService(
        keys=[
            EncryptionKey(
                kid='test', key=SecretStr('legacy-callback-test-secret'), active=True
            )
        ]
    )
    monkeypatch.setattr('storage.encrypt_utils.get_jwt_service', lambda: jwt_service)
    for module in (
        'storage.user_store',
        'storage.role_store',
        'storage.org_store',
        'storage.org_member_store',
        'server.auth.identities',
        'server.auth.user_management',
        'server.auth.keycloak.account_management',
    ):
        monkeypatch.setattr(f'{module}.a_session_maker', factory)
    monkeypatch.setattr(
        UserStore, '_acquire_user_creation_lock', AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        UserStore, '_release_user_creation_lock', AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        'storage.lite_llm_manager.LiteLlmManager.migrate_entries', AsyncMock()
    )
    monkeypatch.setattr('integrations.stripe_service.migrate_customer', AsyncMock())
    monkeypatch.setattr(
        'server.auth.user_management.EnterpriseUserManagementService.ensure_llm_provisioned',
        AsyncMock(),
    )
    monkeypatch.setattr('server.routes.auth.set_response_cookie', MagicMock())
    monkeypatch.setattr('server.routes.auth.schedule_gitlab_repo_sync', MagicMock())
    monkeypatch.setattr('server.routes.auth.RECAPTCHA_SITE_KEY', '')
    monkeypatch.setattr(
        'server.routes.auth.get_web_url', lambda request: 'https://app.example.com'
    )
    monkeypatch.setattr(
        'server.routes.auth._should_redirect_to_onboarding',
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        'server.auth.admission.OrgInvitationService.accept_pending_invitations_for_user',
        AsyncMock(return_value=[]),
    )
    seen_new = []

    async def defaults(user, *, is_new_user):
        seen_new.append(is_new_user)
        return user

    monkeypatch.setattr(
        'server.auth.admission.DefaultOrgBootstrapService.apply_for_user', defaults
    )

    async with factory() as session, session.begin():
        legacy = UserSettings(
            keycloak_user_id=str(user_id),
            email='legacy@example.com',
            email_verified=True,
            already_migrated=False,
            user_version=0,
            accepted_tos=datetime.now(UTC).replace(tzinfo=None),
            llm_api_key=encrypt_legacy_value('preserved-llm-secret'),
            agent_settings={
                'llm': {
                    'model': legacy_model,
                    'base_url': 'https://provider.example/v1',
                }
            },
            conversation_settings={},
            language='es',
            user_consents_to_analytics=False,
        )
        session.add(legacy)
        key = ApiKey(
            user_id=str(user_id), key='preserved-api-key-hash', name='Existing SDK key'
        )
        session.add(key)
        await session.flush()
        api_key_id = key.id
        settings_id = legacy.id
    monkeypatch.setattr(mode, '_auth_mode', None)
    assert (
        await initialize_authentication(
            session_factory=factory,
            environ={
                'KEYCLOAK_SERVER_URL': 'https://unreachable.example',
                'KEYCLOAK_REALM_NAME': 'enterprise',
                'KEYCLOAK_CLIENT_ID': 'openhands',
            },
        )
        is mode.AuthMode.KEYCLOAK
    )
    assert await UserStore.get_user_by_id(str(user_id)) is None
    manager = MagicMock()
    manager.get_keycloak_tokens = AsyncMock(return_value=('access', 'refresh'))
    manager.get_user_info = AsyncMock(
        return_value=KeycloakUserInfo(
            sub=str(user_id), email='legacy@example.com', email_verified=True
        )
    )
    monkeypatch.setattr('server.routes.auth.token_manager', manager)
    create_user = AsyncMock(
        side_effect=AssertionError('Legacy account was classified as a new signup')
    )
    monkeypatch.setattr(UserStore, 'create_user', create_user)
    authorizer = MagicMock(
        authorize_user=AsyncMock(return_value=MagicMock(success=True))
    )
    req = Request(
        {
            'type': 'http',
            'method': 'GET',
            'path': '/oauth/keycloak/callback',
            'scheme': 'https',
            'server': ('app.example.com', 443),
            'headers': [],
            'query_string': b'',
        }
    )
    try:
        response = await keycloak_callback(
            req,
            BackgroundTasks(),
            code='code',
            state='https://app.example.com/settings/user',
            user_authorizer=authorizer,
        )
        assert response.status_code == 302
        create_user.assert_not_awaited()
        assert seen_new == [False]
        async with factory() as session:
            user = await session.get(User, user_id)
            org = await session.get(Org, user_id)
            member = await session.scalar(
                select(OrgMember).where(
                    OrgMember.user_id == user_id, OrgMember.org_id == user_id
                )
            )
            key = await session.get(ApiKey, api_key_id)
            assert user.id == user.current_org_id == org.id == member.org_id == user_id
            assert user.email == 'legacy@example.com'
            assert user.language == 'es'
            assert key.key == 'preserved-api-key-hash'
            assert key.user_id == str(user_id)
            assert key.org_id == user_id
            assert (await session.get(UserSettings, settings_id)).already_migrated
            assert member.agent_settings_diff['llm']['model'] == legacy_model
            assert (
                member.agent_settings_diff['llm']['base_url']
                == 'https://provider.example/v1'
            )
            assert member.llm_api_key.get_secret_value() == 'preserved-llm-secret'
    finally:
        await engine.dispose()


async def test_concurrent_local_slack_links_cannot_claim_the_same_actor(
    database_name,  # noqa: F811
    migration_logs,  # noqa: F811
    monkeypatch,  # noqa: F811
):
    from server.routes.integration import slack

    migrate(database_name, 'head', migration_logs)
    engine = create_async_engine(
        f'postgresql+asyncpg://postgres@127.0.0.1:{PG_PORT}/{database_name}'
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.LOCAL)
    monkeypatch.setattr(slack, 'a_session_maker', factory)
    monkeypatch.setattr(slack.slack_team_store, 'create_team', AsyncMock())
    monkeypatch.setattr(
        slack,
        'AsyncWebClient',
        lambda **kwargs: MagicMock(
            users_info=AsyncMock(
                return_value={'user': {'profile': {'display_name': 'Shared actor'}}}
            )
        ),
    )
    try:
        async with factory() as session, session.begin():
            users = [
                await create_local_account(
                    session,
                    f'slack{number}@example.com',
                    SecretStr('PostgreSQL Slack test password'),
                )
                for number in (1, 2)
            ]
        results = await asyncio.gather(
            *[
                slack._complete_slack_link(
                    user,
                    {
                        'slack_user_id': 'same-actor',
                        'team_id': 'T1',
                        'bot_access_token': 'test-bot-secret',
                    },
                    BackgroundTasks(),
                )
                for user in users
            ],
            return_exceptions=True,
        )
        assert sorted(result.status_code for result in results) == [200, 409]
        assert sum(isinstance(result, HTTPException) for result in results) == 1
        async with factory() as session:
            rows = (await session.scalars(select(SlackUser))).all()
            assert len(rows) == 1
            winner = next(
                user
                for user, result in zip(users, results, strict=False)
                if not isinstance(result, BaseException)
            )
            assert rows[0].keycloak_user_id == str(winner.id)
    finally:
        await engine.dispose()
