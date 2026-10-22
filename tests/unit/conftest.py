import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, QueuePool

from openhands.app_server.app_conversation.sql_app_conversation_start_task_service import (
    StoredAppConversationStartTask,  # noqa: F401
)
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth.token_manager import KeycloakUserInfo, TokenManager
from server.constants import ORG_SETTINGS_VERSION
from server.verified_models.verified_model_service import (
    StoredVerifiedModel,  # noqa: F401
)

# Imported for their side effect: SQLAlchemy can only configure mappers once
# every model a relationship refers to has been imported.
from storage.api_key import ApiKey  # noqa: F401
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import OAuthProviderDetails
from storage.auth_provider_user import AuthProviderUser  # noqa: F401
from storage.billing_session import BillingSession
from storage.conversation_work import ConversationWork
from storage.daily_sandbox_usage import DailySandboxUsage  # noqa: F401
from storage.device_code import DeviceCode  # noqa: F401
from storage.feedback import Feedback
from storage.github_app_installation import GithubAppInstallation
from storage.oauth_token import OAuthToken  # noqa: F401
from storage.oauth_token_store import OAuthTokenStore
from storage.org import Org
from storage.org_budget_settings import OrgBudgetSettings  # noqa: F401
from storage.org_budget_threshold import OrgBudgetThreshold  # noqa: F401
from storage.org_git_claim import OrgGitClaim  # noqa: F401
from storage.org_invitation import OrgInvitation  # noqa: F401
from storage.org_member import OrgMember
from storage.org_user_budget_override import OrgUserBudgetOverride  # noqa: F401
from storage.quota_increase_request import QuotaIncreaseRequest  # noqa: F401
from storage.role import Role
from storage.slack_conversation import SlackConversation  # noqa: F401
from storage.stored_conversation_metadata import StoredConversationMetadata
from storage.stored_conversation_metadata_saas import (
    StoredConversationMetadataSaas,
)
from storage.stored_offline_token import StoredOfflineToken
from storage.stripe_customer import StripeCustomer
from storage.user import User
from storage.user_settings import UserSettings  # noqa: F401
from tests import postgres_testdb


@pytest.fixture(autouse=True)
def allow_short_context_windows():
    old = os.environ.get('ALLOW_SHORT_CONTEXT_WINDOWS')
    os.environ['ALLOW_SHORT_CONTEXT_WINDOWS'] = 'true'
    try:
        yield
    finally:
        if old is None:
            os.environ.pop('ALLOW_SHORT_CONTEXT_WINDOWS', None)
        else:
            os.environ['ALLOW_SHORT_CONTEXT_WINDOWS'] = old


@pytest.fixture
def create_keycloak_user_info():
    """Fixture that returns a factory function to create KeycloakUserInfo models.

    Usage:
        def test_example(create_keycloak_user_info):
            user_info = create_keycloak_user_info(sub='user123', email='test@example.com')
    """

    def _create(**kwargs) -> KeycloakUserInfo:
        defaults = {
            'sub': 'test_user_id',
            'preferred_username': 'test_user',
        }
        defaults.update(kwargs)
        return KeycloakUserInfo(**defaults)

    return _create


@pytest.fixture(scope='session')
def postgres_server(pytestconfig: pytest.Config) -> postgres_testdb.PostgresServer:
    """The postgres this run shares, started by the root ``conftest.py``."""
    return postgres_testdb.server_from(pytestconfig)


@pytest.fixture(scope='session')
def postgres_template(postgres_server: postgres_testdb.PostgresServer) -> str:
    """Name of the migrated database that per-test databases are cloned from."""
    return postgres_testdb.TEMPLATE_DB


@pytest.fixture
def test_database(
    postgres_server: postgres_testdb.PostgresServer, postgres_template: str
) -> Iterator[postgres_testdb.TestDatabase]:
    """A database of this test's own, at ``alembic upgrade head``.

    Cloning the template is fast enough (~50ms) that every test can have a
    genuinely empty schema instead of sharing one behind a rollback.
    """
    name = postgres_testdb.create_test_database(postgres_server, postgres_template)
    try:
        yield postgres_testdb.TestDatabase(server=postgres_server, name=name)
    finally:
        postgres_testdb.drop_test_database(postgres_server, name)


@pytest.fixture
def engine(test_database: postgres_testdb.TestDatabase) -> Iterator[Engine]:
    """Sync engine on this test's database, on the driver production uses.

    A small ``QueuePool`` (rather than ``NullPool``) lets the handful of
    ``session_maker()`` calls a single test makes -- e.g. ``create_org``,
    ``create_user``, then the test body -- reuse one connection instead of
    opening a fresh one each time. The database is already test-scoped and
    dropped in ``test_database``'s teardown, which runs after ``dispose()``
    below closes out this pool, so there's no idle-connection risk to the
    ``DROP DATABASE ... WITH (FORCE)`` that follows.
    """
    engine = create_engine(
        test_database.sync_url, poolclass=QueuePool, pool_size=2, max_overflow=0
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session_maker(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine)


@pytest.fixture
async def async_engine(
    test_database: postgres_testdb.TestDatabase,
) -> AsyncIterator[AsyncEngine]:
    """Async engine on the same database as ``engine``."""
    async_engine = create_async_engine(test_database.async_url, poolclass=NullPool)
    try:
        yield async_engine
    finally:
        await async_engine.dispose()


@pytest.fixture
async def async_session_maker(async_engine: AsyncEngine) -> async_sessionmaker:
    """Async session maker bound to the async engine."""
    return async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest.fixture
def app_db_session(
    test_database: postgres_testdb.TestDatabase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point the app server's global ``db_session`` injector at this test's database.

    Route tests that let FastAPI resolve ``get_db_session`` reach the injector on
    the global config rather than a fixture, so it has to be redirected here.
    """
    from openhands.app_server.config import get_global_config
    from openhands.app_server.services.db_session_injector import DbSessionInjector

    server = test_database.server
    monkeypatch.setattr(
        get_global_config(),
        'db_session',
        DbSessionInjector(
            persistence_dir=tmp_path,
            host=server.host,
            port=server.port,
            name=test_database.name,
            user=server.user,
            password=SecretStr(server.password),
        ),
    )


@pytest.fixture
def create_org(session_maker: sessionmaker) -> Callable[..., Org]:
    """Factory for ``org`` rows, so foreign keys pointing at one resolve.

    Anything org-scoped a test stores needs the org row to exist first.
    """

    def _create(**kwargs) -> Org:
        kwargs.setdefault('id', uuid.uuid4())
        kwargs.setdefault('name', f'test-org-{uuid.uuid4().hex[:12]}')
        kwargs.setdefault('org_version', ORG_SETTINGS_VERSION)
        org = Org(**kwargs)
        with session_maker() as session:
            session.add(org)
            session.commit()
            session.refresh(org)
            session.expunge(org)
        return org

    return _create


@pytest.fixture
def create_user(
    session_maker: sessionmaker, create_org: Callable[..., Org]
) -> Callable[..., User]:
    """Factory for ``user`` rows, defaulting ``current_org_id`` to a new org."""

    def _create(**kwargs) -> User:
        kwargs.setdefault('id', uuid.uuid4())
        if 'current_org_id' not in kwargs:
            kwargs['current_org_id'] = create_org().id
        user = User(**kwargs)
        with session_maker() as session:
            session.add(user)
            session.commit()
            session.refresh(user)
            session.expunge(user)
        return user

    return _create


@pytest.fixture
def oauth_v2_stores(async_session_maker: async_sessionmaker) -> Iterator[None]:
    """Point the stores behind background git-token reads at the test database."""
    jwt_svc = JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('test_secret'), active=True)]
    )
    with (
        patch('storage.database.a_session_maker', async_session_maker),
        patch('storage.user_store.a_session_maker', async_session_maker),
        patch('storage.auth_provider_store.a_session_maker', async_session_maker),
        patch('storage.auth_provider_user_store.a_session_maker', async_session_maker),
        patch('storage.oauth_token_store.a_session_maker', async_session_maker),
        patch('storage.offline_token_store.a_session_maker', async_session_maker),
        patch('server.auth.saas_user_auth.a_session_maker', async_session_maker),
        patch('storage.auth_token_store.a_session_maker', async_session_maker),
        patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc),
    ):
        yield


@pytest.fixture
def create_git_provider(
    oauth_v2_stores, async_session_maker: async_sessionmaker
) -> Callable[..., Awaitable[AuthProvider]]:
    """Factory for OAuth2 ``auth_providers`` rows, git providers by default."""

    async def _create(
        provider_type: ProviderType = ProviderType.GITLAB, is_idp: bool = False
    ) -> AuthProvider:
        host = f'https://{provider_type.value}.example.com'
        provider = AuthProvider(
            title=provider_type.value,
            is_idp=is_idp,
            details=OAuthProviderDetails(
                provider_type=provider_type,
                client_id='client',
                client_secret=SecretStr('secret'),
                authorization_url=f'{host}/oauth/authorize',
                token_url=f'{host}/oauth/token',
                userinfo_url=f'{host}/user',
            ),
        )
        async with async_session_maker() as session:
            session.add(provider)
            await session.commit()
            await session.refresh(provider)
        return provider

    return _create


@pytest.fixture
def store_oauth_token(oauth_v2_stores) -> Callable[..., Awaitable[None]]:
    async def _store(
        user_id: UUID,
        provider: AuthProvider,
        access_token: str,
        access_token_expires_at: datetime | None = None,
    ) -> None:
        await OAuthTokenStore(
            user_id=user_id, oauth_provider_id=provider.id
        ).store_tokens(
            access_token=access_token,
            refresh_token='refresh',
            access_token_expires_at=access_token_expires_at,
            refresh_token_expires_at=None,
        )

    return _store


@pytest.fixture
def oauth_v2_login_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ENABLE_OAUTH_V2_LOGIN', '1')


@pytest.fixture
def oauth_v2_login_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('ENABLE_OAUTH_V2_LOGIN', raising=False)


def _fail_keycloak(*args, **kwargs):
    raise AssertionError('Keycloak must not be used')


@pytest.fixture
def no_keycloak() -> Iterator[None]:
    """Fail on any Keycloak call. Offline-token reads from the database stay
    allowed, since an install without Keycloak simply has none."""
    with (
        patch('server.auth.token_manager.get_keycloak_admin', _fail_keycloak),
        patch('server.auth.token_manager.get_keycloak_openid', _fail_keycloak),
        patch.object(TokenManager, 'get_idp_token', _fail_keycloak),
        patch.object(TokenManager, 'get_idp_token_from_offline_token', _fail_keycloak),
    ):
        yield


def add_minimal_fixtures(session_maker):
    with session_maker() as session:
        role = Role(name='admin', rank=1)
        session.add(role)
        # Flush before anything else so Postgres assigns ``role.id`` from its
        # identity sequence. Hardcoding an id here would leave the sequence at
        # 1, and the next role a test inserts without an id would collide.
        session.flush()
        session.add(
            BillingSession(
                id='mock-billing-session-id',
                user_id='mock-user-id',
                status='completed',
                price=20,
                price_code='NA',
                created_at=datetime.fromisoformat('2025-03-03'),
                updated_at=datetime.fromisoformat('2025-03-04'),
            )
        )
        session.add(
            Feedback(
                id='mock-feedback-id',
                version='1.0',
                email='user@all-hands.dev',
                polarity='positive',
                permissions='public',
                trajectory=[],
            )
        )
        session.add(
            GithubAppInstallation(
                installation_id='mock-installation-id',
                encrypted_token='',
                created_at=datetime.fromisoformat('2025-03-05'),
                updated_at=datetime.fromisoformat('2025-03-06'),
            )
        )
        session.add(
            StoredConversationMetadata(
                conversation_id='mock-conversation-id',
                created_at=datetime.fromisoformat('2025-03-07'),
                last_updated_at=datetime.fromisoformat('2025-03-08'),
                accumulated_cost=5.25,
                prompt_tokens=500,
                completion_tokens=250,
                total_tokens=750,
            )
        )
        session.add(
            StoredConversationMetadataSaas(
                conversation_id='mock-conversation-id',
                user_id=UUID('5594c7b6-f959-4b81-92e9-b09c206f5081'),
                org_id=UUID('5594c7b6-f959-4b81-92e9-b09c206f5081'),
            )
        )
        session.add(
            StoredOfflineToken(
                user_id='mock-user-id',
                offline_token='mock-offline-token',
                created_at=datetime.fromisoformat('2025-03-07'),
                updated_at=datetime.fromisoformat('2025-03-08'),
            )
        )
        session.add(
            Org(
                id=uuid.UUID('5594c7b6-f959-4b81-92e9-b09c206f5081'),
                name='mock-org',
                org_version=ORG_SETTINGS_VERSION,
                enable_proactive_conversation_starters=True,
            )
        )
        session.add(
            User(
                id=uuid.UUID('5594c7b6-f959-4b81-92e9-b09c206f5081'),
                current_org_id=uuid.UUID('5594c7b6-f959-4b81-92e9-b09c206f5081'),
                user_consents_to_analytics=True,
            )
        )
        session.add(
            OrgMember(
                org_id=uuid.UUID('5594c7b6-f959-4b81-92e9-b09c206f5081'),
                user_id=uuid.UUID('5594c7b6-f959-4b81-92e9-b09c206f5081'),
                role_id=role.id,
                llm_api_key='mock-api-key',
                status='active',
            )
        )
        session.add(
            StripeCustomer(
                keycloak_user_id='mock-user-id',
                stripe_customer_id='mock-stripe-customer-id',
                created_at=datetime.fromisoformat('2025-03-09'),
                updated_at=datetime.fromisoformat('2025-03-10'),
            )
        )
        session.add(
            ConversationWork(
                conversation_id='mock-conversation-id',
                user_id='mock-user-id',
                created_at=datetime.fromisoformat('2025-03-07'),
                updated_at=datetime.fromisoformat('2025-03-08'),
            )
        )
        session.commit()


@pytest.fixture
def session_maker_with_minimal_fixtures(engine):
    session_maker = sessionmaker(bind=engine)
    add_minimal_fixtures(session_maker)
    return session_maker
