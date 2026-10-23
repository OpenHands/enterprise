"""Unit tests for install_gitlab_webhooks module."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from integrations.gitlab.webhook_installation import (
    BreakLoopException,
    verify_webhook_conditions,
)
from integrations.types import GitLabResourceType
from integrations.utils import GITLAB_WEBHOOK_URL
from storage.gitlab_webhook import GitlabWebhook, WebhookStatus

RUN_ID = uuid4()


@pytest.fixture
def mock_gitlab_service():
    """Create a mock GitLab service."""
    service = MagicMock()
    service.check_resource_exists = AsyncMock(return_value=(True, None))
    service.check_user_has_admin_access_to_resource = AsyncMock(
        return_value=(True, None)
    )
    service.check_webhook_exists_on_resource = AsyncMock(return_value=(False, None))
    service.install_webhook = AsyncMock(return_value=('webhook-id-123', None))
    return service


@pytest.fixture
def mock_webhook_store():
    """Create a mock webhook store."""
    store = MagicMock()
    store.delete_claimed = AsyncMock(return_value=True)
    store.update_claimed = AsyncMock(return_value=True)
    return store


@pytest.fixture
def sample_webhook():
    """Create a sample webhook object."""
    webhook = MagicMock(spec=GitlabWebhook)
    webhook.id = 7
    webhook.user_id = 'test_user_id'
    webhook.webhook_exists = False
    webhook.webhook_uuid = None
    return webhook


class TestVerifyWebhookConditions:
    """Test cases for verify_webhook_conditions function."""

    @pytest.mark.asyncio
    async def test_verify_conditions_all_pass(
        self, mock_gitlab_service, mock_webhook_store, sample_webhook
    ):
        """Test when all conditions are met for webhook installation."""
        # Arrange
        resource_type = GitLabResourceType.PROJECT
        resource_id = 'project-123'

        # Act
        # Should not raise any exception
        await verify_webhook_conditions(
            gitlab_service=mock_gitlab_service,
            resource_type=resource_type,
            resource_id=resource_id,
            webhook_store=mock_webhook_store,
            webhook=sample_webhook,
            claim_run_id=RUN_ID,
        )

        # Assert
        mock_gitlab_service.check_resource_exists.assert_called_once_with(
            resource_type, resource_id
        )
        mock_gitlab_service.check_user_has_admin_access_to_resource.assert_called_once_with(
            resource_type, resource_id
        )
        mock_gitlab_service.check_webhook_exists_on_resource.assert_called_once_with(
            resource_type=resource_type,
            resource_id=resource_id,
            webhook_url=GITLAB_WEBHOOK_URL,
        )
        mock_webhook_store.delete_claimed.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_conditions_resource_does_not_exist(
        self, mock_gitlab_service, mock_webhook_store, sample_webhook
    ):
        """Test when resource does not exist."""
        # Arrange
        resource_type = GitLabResourceType.PROJECT
        resource_id = 'project-999'
        mock_gitlab_service.check_resource_exists = AsyncMock(
            return_value=(False, None)
        )

        # Act & Assert
        with pytest.raises(BreakLoopException):
            await verify_webhook_conditions(
                gitlab_service=mock_gitlab_service,
                resource_type=resource_type,
                resource_id=resource_id,
                webhook_store=mock_webhook_store,
                webhook=sample_webhook,
                claim_run_id=RUN_ID,
            )

        # Assert webhook is deleted
        mock_webhook_store.delete_claimed.assert_called_once_with(
            sample_webhook.id, RUN_ID
        )

    @pytest.mark.asyncio
    async def test_verify_conditions_rate_limited_on_resource_check(
        self, mock_gitlab_service, mock_webhook_store, sample_webhook
    ):
        """Test when rate limited during resource existence check."""
        # Arrange
        resource_type = GitLabResourceType.PROJECT
        resource_id = 'project-123'
        mock_gitlab_service.check_resource_exists = AsyncMock(
            return_value=(False, WebhookStatus.RATE_LIMITED)
        )

        # Act & Assert
        with pytest.raises(BreakLoopException):
            await verify_webhook_conditions(
                gitlab_service=mock_gitlab_service,
                resource_type=resource_type,
                resource_id=resource_id,
                webhook_store=mock_webhook_store,
                webhook=sample_webhook,
                claim_run_id=RUN_ID,
            )

        # Should not delete webhook on rate limit
        mock_webhook_store.delete_claimed.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_conditions_user_no_admin_access(
        self, mock_gitlab_service, mock_webhook_store, sample_webhook
    ):
        """Test when user does not have admin access."""
        # Arrange
        resource_type = GitLabResourceType.GROUP
        resource_id = 'group-456'
        mock_gitlab_service.check_user_has_admin_access_to_resource = AsyncMock(
            return_value=(False, None)
        )

        # Act & Assert
        with pytest.raises(BreakLoopException):
            await verify_webhook_conditions(
                gitlab_service=mock_gitlab_service,
                resource_type=resource_type,
                resource_id=resource_id,
                webhook_store=mock_webhook_store,
                webhook=sample_webhook,
                claim_run_id=RUN_ID,
            )

        # Assert webhook is deleted
        mock_webhook_store.delete_claimed.assert_called_once_with(
            sample_webhook.id, RUN_ID
        )

    @pytest.mark.asyncio
    async def test_verify_conditions_rate_limited_on_admin_check(
        self, mock_gitlab_service, mock_webhook_store, sample_webhook
    ):
        """Test when rate limited during admin access check."""
        # Arrange
        resource_type = GitLabResourceType.PROJECT
        resource_id = 'project-123'
        mock_gitlab_service.check_user_has_admin_access_to_resource = AsyncMock(
            return_value=(False, WebhookStatus.RATE_LIMITED)
        )

        # Act & Assert
        with pytest.raises(BreakLoopException):
            await verify_webhook_conditions(
                gitlab_service=mock_gitlab_service,
                resource_type=resource_type,
                resource_id=resource_id,
                webhook_store=mock_webhook_store,
                webhook=sample_webhook,
                claim_run_id=RUN_ID,
            )

        # Should not delete webhook on rate limit
        mock_webhook_store.delete_claimed.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_conditions_webhook_already_exists(
        self, mock_gitlab_service, mock_webhook_store, sample_webhook
    ):
        """Test when webhook already exists on resource."""
        # Arrange
        resource_type = GitLabResourceType.PROJECT
        resource_id = 'project-123'
        mock_gitlab_service.check_webhook_exists_on_resource = AsyncMock(
            return_value=(True, None)
        )

        # Act & Assert
        with pytest.raises(BreakLoopException):
            await verify_webhook_conditions(
                gitlab_service=mock_gitlab_service,
                resource_type=resource_type,
                resource_id=resource_id,
                webhook_store=mock_webhook_store,
                webhook=sample_webhook,
                claim_run_id=RUN_ID,
            )

    @pytest.mark.asyncio
    async def test_verify_conditions_rate_limited_on_webhook_check(
        self, mock_gitlab_service, mock_webhook_store, sample_webhook
    ):
        """Test when rate limited during webhook existence check."""
        # Arrange
        resource_type = GitLabResourceType.PROJECT
        resource_id = 'project-123'
        mock_gitlab_service.check_webhook_exists_on_resource = AsyncMock(
            return_value=(False, WebhookStatus.RATE_LIMITED)
        )

        # Act & Assert
        with pytest.raises(BreakLoopException):
            await verify_webhook_conditions(
                gitlab_service=mock_gitlab_service,
                resource_type=resource_type,
                resource_id=resource_id,
                webhook_store=mock_webhook_store,
                webhook=sample_webhook,
                claim_run_id=RUN_ID,
            )

    @pytest.mark.asyncio
    async def test_verify_conditions_updates_webhook_status_mismatch(
        self, mock_gitlab_service, mock_webhook_store, sample_webhook
    ):
        """Test that webhook status is updated when database and API don't match."""
        # Arrange
        resource_type = GitLabResourceType.PROJECT
        resource_id = 'project-123'
        sample_webhook.webhook_exists = True  # DB says exists
        mock_gitlab_service.check_webhook_exists_on_resource = AsyncMock(
            return_value=(False, None)  # API says doesn't exist
        )

        # Act
        # Should not raise BreakLoopException when webhook doesn't exist (allows installation)
        await verify_webhook_conditions(
            gitlab_service=mock_gitlab_service,
            resource_type=resource_type,
            resource_id=resource_id,
            webhook_store=mock_webhook_store,
            webhook=sample_webhook,
            claim_run_id=RUN_ID,
        )

        # Assert webhook status was updated to match API
        mock_webhook_store.update_claimed.assert_called_once_with(
            sample_webhook.id, RUN_ID, webhook_exists=False
        )
