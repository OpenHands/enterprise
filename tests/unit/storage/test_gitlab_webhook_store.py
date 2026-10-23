"""Unit tests for GitlabWebhookStore."""

import pytest

from storage.gitlab_webhook import GitlabWebhook
from storage.gitlab_webhook_store import GitlabWebhookStore

# Use module-scoped engine to share database across fixtures
_test_engine = None


@pytest.fixture
async def webhook_store(async_session_maker):
    """Create a GitlabWebhookStore instance for testing.

    This fixture injects the test's async_session_maker to ensure
    the store uses the same in-memory database as the test fixtures.
    """
    # Import here to avoid circular imports

    store = GitlabWebhookStore()

    # Inject the test session maker - this needs to replace the module-level import
    import storage.gitlab_webhook_store as store_module

    store_module.a_session_maker = async_session_maker

    return store


@pytest.fixture
async def sample_webhooks(async_session_maker):
    """Create sample webhook records for testing."""
    async with async_session_maker() as session:
        # Create webhooks for user_1
        webhook1 = GitlabWebhook(
            project_id='project-1',
            group_id=None,
            user_id='user_1',
            webhook_exists=True,
            webhook_url='https://example.com/webhook',
            webhook_secret='secret-1',
            webhook_uuid='uuid-1',
        )
        webhook2 = GitlabWebhook(
            project_id='project-2',
            group_id=None,
            user_id='user_1',
            webhook_exists=True,
            webhook_url='https://example.com/webhook',
            webhook_secret='secret-2',
            webhook_uuid='uuid-2',
        )
        webhook3 = GitlabWebhook(
            project_id=None,
            group_id='group-1',
            user_id='user_1',
            webhook_exists=False,  # Already marked for reinstallation
            webhook_url='https://example.com/webhook',
            webhook_secret='secret-3',
            webhook_uuid='uuid-3',
        )

        # Create webhook for user_2
        webhook4 = GitlabWebhook(
            project_id='project-3',
            group_id=None,
            user_id='user_2',
            webhook_exists=True,
            webhook_url='https://example.com/webhook',
            webhook_secret='secret-4',
            webhook_uuid='uuid-4',
        )

        session.add_all([webhook1, webhook2, webhook3, webhook4])
        await session.commit()

        # Refresh to get IDs (outside of begin() context)
        await session.refresh(webhook1)
        await session.refresh(webhook2)
        await session.refresh(webhook3)
        await session.refresh(webhook4)

    return [webhook1, webhook2, webhook3, webhook4]


class TestGetWebhooksByResources:
    """Test cases for get_webhooks_by_resources method."""

    @pytest.mark.asyncio
    async def test_get_webhooks_by_resources_projects_only(
        self, webhook_store, async_session_maker, sample_webhooks
    ):
        """Test bulk fetching webhooks for multiple projects."""
        # Arrange
        project_ids = ['project-1', 'project-2', 'project-3']
        group_ids: list[str] = []

        # Act
        project_map, group_map = await webhook_store.get_webhooks_by_resources(
            project_ids, group_ids
        )

        # Assert
        assert len(project_map) == 3
        assert 'project-1' in project_map
        assert 'project-2' in project_map
        assert 'project-3' in project_map
        assert len(group_map) == 0

    @pytest.mark.asyncio
    async def test_get_webhooks_by_resources_groups_only(
        self, webhook_store, async_session_maker, sample_webhooks
    ):
        """Test bulk fetching webhooks for multiple groups."""
        # Arrange
        project_ids: list[str] = []
        group_ids = ['group-1']

        # Act
        project_map, group_map = await webhook_store.get_webhooks_by_resources(
            project_ids, group_ids
        )

        # Assert
        assert len(project_map) == 0
        assert len(group_map) == 1
        assert 'group-1' in group_map

    @pytest.mark.asyncio
    async def test_get_webhooks_by_resources_mixed(
        self, webhook_store, async_session_maker, sample_webhooks
    ):
        """Test bulk fetching webhooks for both projects and groups."""
        # Arrange
        project_ids = ['project-1', 'project-2']
        group_ids = ['group-1']

        # Act
        project_map, group_map = await webhook_store.get_webhooks_by_resources(
            project_ids, group_ids
        )

        # Assert
        assert len(project_map) == 2
        assert len(group_map) == 1
        assert 'project-1' in project_map
        assert 'project-2' in project_map
        assert 'group-1' in group_map

    @pytest.mark.asyncio
    async def test_get_webhooks_by_resources_empty_lists(
        self, webhook_store, async_session_maker
    ):
        """Test bulk fetching with empty ID lists."""
        # Arrange
        project_ids: list[str] = []
        group_ids: list[str] = []

        # Act
        project_map, group_map = await webhook_store.get_webhooks_by_resources(
            project_ids, group_ids
        )

        # Assert
        assert len(project_map) == 0
        assert len(group_map) == 0

    @pytest.mark.asyncio
    async def test_get_webhooks_by_resources_partial_matches(
        self, webhook_store, async_session_maker, sample_webhooks
    ):
        """Test bulk fetching when some IDs don't exist."""
        # Arrange
        project_ids = ['project-1', 'non-existent-project']
        group_ids = ['group-1', 'non-existent-group']

        # Act
        project_map, group_map = await webhook_store.get_webhooks_by_resources(
            project_ids, group_ids
        )

        # Assert
        assert len(project_map) == 1
        assert 'project-1' in project_map
        assert 'non-existent-project' not in project_map
        assert len(group_map) == 1
        assert 'group-1' in group_map
        assert 'non-existent-group' not in group_map
