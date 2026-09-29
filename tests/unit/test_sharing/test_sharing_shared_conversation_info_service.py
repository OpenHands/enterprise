"""Tests for SharedConversationInfoService."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
    ConversationTrigger,
)
from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    SQLAppConversationInfoService,
)
from openhands.app_server.integrations.provider import ProviderType
from openhands.app_server.user.specifiy_user_context import SpecifyUserContext
from openhands.sdk.llm import MetricsSnapshot, TokenUsage
from server.auth.auth_error import NoCredentialsError
from server.sharing.sql_shared_conversation_info_service import (
    SQLSharedConversationInfoService,
    SQLSharedConversationInfoServiceInjector,
    resolve_viewer_user_id,
)
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas
from storage.user import User


@pytest.fixture
async def async_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create an async session for testing."""
    async_session_maker = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session_maker() as db_session:
        yield db_session


@pytest.fixture
async def shared_conversation_info_service(async_session):
    """Create a SharedConversationInfoService for testing."""
    return SQLSharedConversationInfoService(db_session=async_session)


@pytest.fixture
async def app_conversation_service(async_session):
    """Create an AppConversationInfoService for creating test data."""
    return SQLAppConversationInfoService(
        db_session=async_session, user_context=SpecifyUserContext(user_id=None)
    )


@pytest.fixture
def sample_conversation_info():
    """Create a sample conversation info for testing."""
    return AppConversationInfo(
        id=uuid4(),
        created_by_user_id='test_user',
        sandbox_id='test_sandbox',
        selected_repository='test/repo',
        selected_branch='main',
        git_provider=ProviderType.GITHUB,
        title='Test Conversation',
        trigger=ConversationTrigger.GUI,
        pr_number=[123],
        llm_model='gpt-4',
        metrics=MetricsSnapshot(
            accumulated_cost=1.5,
            max_budget_per_task=10.0,
            accumulated_token_usage=TokenUsage(
                prompt_tokens=100,
                completion_tokens=50,
                cache_read_tokens=0,
                cache_write_tokens=0,
                context_window=4096,
                per_turn_token=150,
            ),
        ),
        parent_conversation_id=None,
        sub_conversation_ids=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        public=True,  # Make it public for testing
    )


@pytest.fixture
def sample_private_conversation_info():
    """Create a sample private conversation info for testing."""
    return AppConversationInfo(
        id=uuid4(),
        created_by_user_id='test_user',
        sandbox_id='test_sandbox_private',
        selected_repository='test/private_repo',
        selected_branch='main',
        git_provider=ProviderType.GITHUB,
        title='Private Conversation',
        trigger=ConversationTrigger.GUI,
        pr_number=[124],
        llm_model='gpt-4',
        metrics=MetricsSnapshot(
            accumulated_cost=2.0,
            max_budget_per_task=10.0,
            accumulated_token_usage=TokenUsage(
                prompt_tokens=200,
                completion_tokens=100,
                cache_read_tokens=0,
                cache_write_tokens=0,
                context_window=4096,
                per_turn_token=300,
            ),
        ),
        parent_conversation_id=None,
        sub_conversation_ids=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        public=False,  # Make it private
    )


class TestSharedConversationInfoService:
    """Test cases for SharedConversationInfoService."""

    @pytest.mark.asyncio
    async def test_get_shared_conversation_info_returns_public_conversation(
        self,
        shared_conversation_info_service,
        app_conversation_service,
        sample_conversation_info,
    ):
        """Test that get_shared_conversation_info returns a public conversation."""
        # Create a public conversation
        await app_conversation_service.save_app_conversation_info(
            sample_conversation_info
        )

        # Retrieve it via public service
        result = await shared_conversation_info_service.get_shared_conversation_info(
            sample_conversation_info.id
        )

        assert result is not None
        assert result.id == sample_conversation_info.id
        assert result.title == sample_conversation_info.title
        # Note: created_by_user_id is no longer stored in shared conversation metadata
        assert result.created_by_user_id is None

    @pytest.mark.asyncio
    async def test_get_shared_conversation_info_returns_none_for_private_conversation(
        self,
        shared_conversation_info_service,
        app_conversation_service,
        sample_private_conversation_info,
    ):
        """Test that get_shared_conversation_info returns None for private conversations."""
        # Create a private conversation
        await app_conversation_service.save_app_conversation_info(
            sample_private_conversation_info
        )

        # Try to retrieve it via public service
        result = await shared_conversation_info_service.get_shared_conversation_info(
            sample_private_conversation_info.id
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_get_shared_conversation_info_returns_none_for_nonexistent_conversation(
        self, shared_conversation_info_service
    ):
        """Test that get_shared_conversation_info returns None for nonexistent conversations."""
        nonexistent_id = uuid4()
        result = await shared_conversation_info_service.get_shared_conversation_info(
            nonexistent_id
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_batch_get_shared_conversation_info(
        self,
        shared_conversation_info_service,
        app_conversation_service,
        sample_conversation_info,
        sample_private_conversation_info,
    ):
        """Test batch getting public conversations."""
        # Create both public and private conversations
        await app_conversation_service.save_app_conversation_info(
            sample_conversation_info
        )
        await app_conversation_service.save_app_conversation_info(
            sample_private_conversation_info
        )

        # Batch get both conversations
        result = (
            await shared_conversation_info_service.batch_get_shared_conversation_info(
                [sample_conversation_info.id, sample_private_conversation_info.id]
            )
        )

        # Should return the public one and None for the private one
        assert len(result) == 2
        assert result[0] is not None
        assert result[0].id == sample_conversation_info.id
        assert result[1] is None


class TestSharedConversationInfoServiceWithSaasMetadata:
    """Test cases for SharedConversationInfoService with SAAS metadata.

    These tests verify that created_by_user_id is correctly retrieved from
    the conversation_metadata_saas table when it exists.
    """

    @pytest.fixture
    async def async_session_with_saas(
        self, async_engine
    ) -> AsyncGenerator[AsyncSession, None]:
        """Create an async session for testing with SAAS tables."""
        async_session_maker = async_sessionmaker(
            async_engine, class_=AsyncSession, expire_on_commit=False
        )

        async with async_session_maker() as db_session:
            yield db_session

    @pytest.fixture
    async def test_org(self, async_session_with_saas) -> Org:
        """Create a test organization."""
        org = Org(id=uuid4(), name=f'test_org_{uuid4().hex[:8]}')
        async_session_with_saas.add(org)
        await async_session_with_saas.commit()
        return org

    @pytest.fixture
    async def test_user(self, async_session_with_saas, test_org) -> User:
        """Create a test user belonging to the test organization."""
        user = User(id=uuid4(), current_org_id=test_org.id)
        async_session_with_saas.add(user)
        await async_session_with_saas.commit()
        return user

    @pytest.fixture
    async def shared_service_with_saas(self, async_session_with_saas):
        """Create a SharedConversationInfoService for testing."""
        return SQLSharedConversationInfoService(db_session=async_session_with_saas)

    @pytest.fixture
    async def app_service_with_saas(self, async_session_with_saas):
        """Create an AppConversationInfoService for creating test data."""
        return SQLAppConversationInfoService(
            db_session=async_session_with_saas,
            user_context=SpecifyUserContext(user_id=None),
        )

    async def _create_saas_metadata(
        self,
        db_session: AsyncSession,
        conversation_id: UUID,
        user_id: UUID,
        org_id: UUID,
    ) -> StoredConversationMetadataSaas:
        """Helper to create SAAS metadata for a conversation."""
        saas_metadata = StoredConversationMetadataSaas(
            conversation_id=str(conversation_id),
            user_id=user_id,
            org_id=org_id,
        )
        db_session.add(saas_metadata)
        await db_session.commit()
        return saas_metadata

    @pytest.mark.asyncio
    async def test_get_shared_conversation_returns_user_id_from_saas_metadata(
        self,
        shared_service_with_saas,
        app_service_with_saas,
        async_session_with_saas,
        test_user,
        test_org,
    ):
        """Test that get_shared_conversation_info returns created_by_user_id from SAAS metadata."""
        # Arrange
        conversation_id = uuid4()
        conversation = AppConversationInfo(
            id=conversation_id,
            created_by_user_id=None,
            sandbox_id='test_sandbox',
            title='Public Conversation With User',
            public=True,
            metrics=MetricsSnapshot(
                accumulated_cost=0.0,
                max_budget_per_task=10.0,
                accumulated_token_usage=TokenUsage(),
            ),
        )
        await app_service_with_saas.save_app_conversation_info(conversation)
        await self._create_saas_metadata(
            async_session_with_saas, conversation_id, test_user.id, test_org.id
        )

        # Act
        result = await shared_service_with_saas.get_shared_conversation_info(
            conversation_id
        )

        # Assert
        assert result is not None
        assert result.created_by_user_id == str(test_user.id)

    @pytest.mark.asyncio
    async def test_batch_get_shared_conversations_returns_user_id_from_saas_metadata(
        self,
        shared_service_with_saas,
        app_service_with_saas,
        async_session_with_saas,
        test_user,
        test_org,
    ):
        """Test that batch_get_shared_conversation_info returns created_by_user_id from SAAS metadata."""
        # Arrange
        conversation_id = uuid4()
        conversation = AppConversationInfo(
            id=conversation_id,
            created_by_user_id=None,
            sandbox_id='test_sandbox_batch',
            title='Batch Get Conversation',
            public=True,
            metrics=MetricsSnapshot(
                accumulated_cost=0.0,
                max_budget_per_task=10.0,
                accumulated_token_usage=TokenUsage(),
            ),
        )
        await app_service_with_saas.save_app_conversation_info(conversation)
        await self._create_saas_metadata(
            async_session_with_saas, conversation_id, test_user.id, test_org.id
        )

        # Act
        result = await shared_service_with_saas.batch_get_shared_conversation_info(
            [conversation_id]
        )

        # Assert
        assert len(result) == 1
        assert result[0] is not None
        assert result[0].created_by_user_id == str(test_user.id)

    # -- Org automation visibility ------------------------------------------

    @pytest.fixture
    async def member_role(self, async_session_with_saas) -> Role:
        """Reuse a seeded role, or create one, for org memberships."""
        result = await async_session_with_saas.execute(
            select(Role).order_by(Role.id).limit(1)
        )
        role = result.scalars().first()
        if role is None:
            role = Role(name=f'role_{uuid4().hex[:8]}', rank=1)
            async_session_with_saas.add(role)
            await async_session_with_saas.commit()
        return role

    async def _add_org_member(
        self, db_session: AsyncSession, org_id: UUID, user_id: UUID, role_id: int
    ) -> None:
        db_session.add(
            OrgMember(
                org_id=org_id,
                user_id=user_id,
                role_id=role_id,
                llm_api_key='test-api-key',
                status='active',
            )
        )
        await db_session.commit()

    @pytest.fixture
    async def org_member(self, async_session_with_saas, test_org, member_role) -> User:
        """A second user who is a member of the test organization."""
        user = User(id=uuid4(), current_org_id=test_org.id)
        async_session_with_saas.add(user)
        await async_session_with_saas.commit()
        await self._add_org_member(
            async_session_with_saas, test_org.id, user.id, member_role.id
        )
        return user

    @pytest.fixture
    async def other_org_member(self, async_session_with_saas, member_role) -> User:
        """A user who belongs to a different organization."""
        other_org = Org(id=uuid4(), name=f'other_org_{uuid4().hex[:8]}')
        user = User(id=uuid4(), current_org_id=other_org.id)
        async_session_with_saas.add_all([other_org, user])
        await async_session_with_saas.commit()
        await self._add_org_member(
            async_session_with_saas, other_org.id, user.id, member_role.id
        )
        return user

    async def _create_private_conversation(
        self,
        app_service: SQLAppConversationInfoService,
        db_session: AsyncSession,
        trigger: ConversationTrigger,
        user_id: UUID,
        org_id: UUID,
    ) -> UUID:
        """Create a non-public conversation owned by ``user_id`` in ``org_id``."""
        conversation_id = uuid4()
        conversation = AppConversationInfo(
            id=conversation_id,
            created_by_user_id=None,
            sandbox_id='test_sandbox_private',
            title='Private Conversation',
            trigger=trigger,
            public=False,
            metrics=MetricsSnapshot(
                accumulated_cost=0.0,
                max_budget_per_task=10.0,
                accumulated_token_usage=TokenUsage(),
            ),
        )
        await app_service.save_app_conversation_info(conversation)
        await self._create_saas_metadata(db_session, conversation_id, user_id, org_id)
        return conversation_id

    @pytest.mark.asyncio
    async def test_org_member_sees_automation_conversation_from_their_org(
        self,
        app_service_with_saas,
        async_session_with_saas,
        test_user,
        test_org,
        org_member,
    ):
        """An automation conversation is readable by other members of its org."""
        # Arrange
        conversation_id = await self._create_private_conversation(
            app_service_with_saas,
            async_session_with_saas,
            ConversationTrigger.AUTOMATION,
            test_user.id,
            test_org.id,
        )
        service = SQLSharedConversationInfoService(
            db_session=async_session_with_saas, viewer_user_id=org_member.id
        )

        # Act
        result = await service.get_shared_conversation_info(conversation_id)

        # Assert
        assert result is not None
        assert result.id == conversation_id
        assert result.created_by_user_id == str(test_user.id)

    @pytest.mark.asyncio
    async def test_anonymous_viewer_does_not_see_automation_conversation(
        self,
        shared_service_with_saas,
        app_service_with_saas,
        async_session_with_saas,
        test_user,
        test_org,
    ):
        """Without a viewer, a non-public automation conversation stays hidden."""
        # Arrange
        conversation_id = await self._create_private_conversation(
            app_service_with_saas,
            async_session_with_saas,
            ConversationTrigger.AUTOMATION,
            test_user.id,
            test_org.id,
        )

        # Act
        result = await shared_service_with_saas.get_shared_conversation_info(
            conversation_id
        )

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_member_of_another_org_does_not_see_automation_conversation(
        self,
        app_service_with_saas,
        async_session_with_saas,
        test_user,
        test_org,
        other_org_member,
    ):
        """Org visibility does not leak across organizations."""
        # Arrange
        conversation_id = await self._create_private_conversation(
            app_service_with_saas,
            async_session_with_saas,
            ConversationTrigger.AUTOMATION,
            test_user.id,
            test_org.id,
        )
        service = SQLSharedConversationInfoService(
            db_session=async_session_with_saas, viewer_user_id=other_org_member.id
        )

        # Act
        result = await service.get_shared_conversation_info(conversation_id)

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_org_member_does_not_see_private_conversation_without_automation_trigger(
        self,
        app_service_with_saas,
        async_session_with_saas,
        test_user,
        test_org,
        org_member,
    ):
        """Only automation-triggered conversations are shared with the org."""
        # Arrange
        conversation_id = await self._create_private_conversation(
            app_service_with_saas,
            async_session_with_saas,
            ConversationTrigger.GUI,
            test_user.id,
            test_org.id,
        )
        service = SQLSharedConversationInfoService(
            db_session=async_session_with_saas, viewer_user_id=org_member.id
        )

        # Act
        result = await service.get_shared_conversation_info(conversation_id)

        # Assert
        assert result is None


def _user_context_stub(user_id: str | None = None, error: Exception | None = None):
    """Build a ``get_user_context`` replacement for ``resolve_viewer_user_id``."""

    @asynccontextmanager
    async def get_user_context(state, request):
        if error is not None:
            raise error
        user_context = MagicMock()
        user_context.get_user_id = AsyncMock(return_value=user_id)
        yield user_context

    return get_user_context


class TestResolveViewerUserId:
    """Test cases for the optional viewer resolution of the sharing endpoints."""

    @pytest.mark.asyncio
    async def test_returns_none_without_request(self):
        # Arrange
        with patch('openhands.app_server.config.get_user_context') as get_user_context:
            # Act
            result = await resolve_viewer_user_id(MagicMock(), None)

        # Assert
        assert result is None
        get_user_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_authenticated_user_id(self):
        # Arrange
        user_id = uuid4()
        with patch(
            'openhands.app_server.config.get_user_context',
            _user_context_stub(user_id=str(user_id)),
        ):
            # Act
            result = await resolve_viewer_user_id(MagicMock(), MagicMock())

        # Assert
        assert result == user_id

    @pytest.mark.asyncio
    async def test_returns_none_without_credentials(self):
        # Arrange
        with patch(
            'openhands.app_server.config.get_user_context',
            _user_context_stub(error=NoCredentialsError('failed to authenticate')),
        ):
            # Act
            result = await resolve_viewer_user_id(MagicMock(), MagicMock())

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_resolution_fails_unexpectedly(self):
        # Arrange
        with patch(
            'openhands.app_server.config.get_user_context',
            _user_context_stub(error=RuntimeError('auth backend unavailable')),
        ):
            # Act
            result = await resolve_viewer_user_id(MagicMock(), MagicMock())

        # Assert
        assert result is None


class TestSQLSharedConversationInfoServiceInjector:
    """Test cases for SQLSharedConversationInfoServiceInjector."""

    @pytest.mark.asyncio
    async def test_injector_passes_resolved_viewer_to_service(self):
        # Arrange
        viewer_id = uuid4()
        mock_state = MagicMock()
        mock_request = MagicMock()
        mock_db_session = AsyncMock()
        mock_db_context = AsyncMock()
        mock_db_context.__aenter__.return_value = mock_db_session
        mock_db_context.__aexit__.return_value = None

        with (
            patch(
                'openhands.app_server.config.get_db_session',
                return_value=mock_db_context,
            ),
            patch(
                'server.sharing.sql_shared_conversation_info_service.resolve_viewer_user_id',
                AsyncMock(return_value=viewer_id),
            ) as mock_resolve,
        ):
            # Act
            services = [
                service
                async for service in SQLSharedConversationInfoServiceInjector().inject(
                    mock_state, mock_request
                )
            ]

        # Assert
        mock_resolve.assert_awaited_once_with(mock_state, mock_request)
        assert len(services) == 1
        assert services[0].db_session is mock_db_session
        assert services[0].viewer_user_id == viewer_id
