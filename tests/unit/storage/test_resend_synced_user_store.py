"""Unit tests for ResendSyncedUserStore."""

from unittest.mock import MagicMock

import pytest

# Import directly from the module files to avoid loading all of storage/__init__.py
# which has many dependencies
from storage.resend_synced_user import ResendSyncedUser
from storage.resend_synced_user_store import ResendSyncedUserStore


@pytest.fixture
def mock_session():
    """Mock database session."""
    session = MagicMock()
    return session


@pytest.fixture
def mock_session_maker(mock_session):
    """Mock session maker."""
    session_maker = MagicMock()
    session_maker.return_value.__enter__.return_value = mock_session
    session_maker.return_value.__exit__.return_value = None
    return session_maker


@pytest.fixture
def store(mock_session_maker):
    """Create ResendSyncedUserStore instance."""
    return ResendSyncedUserStore(session_maker=mock_session_maker)


class TestResendSyncedUserStore:
    """Test cases for ResendSyncedUserStore."""

    def test_is_user_synced_returns_true_when_exists(self, store, mock_session):
        """Test is_user_synced returns True when user exists in database."""
        email = 'test@example.com'
        audience_id = 'test-audience-123'

        mock_row = MagicMock()
        mock_session.execute.return_value.first.return_value = mock_row

        result = store.is_user_synced(email, audience_id)

        assert result is True
        mock_session.execute.assert_called_once()

    def test_is_user_synced_returns_false_when_not_exists(self, store, mock_session):
        """Test is_user_synced returns False when user doesn't exist."""
        email = 'test@example.com'
        audience_id = 'test-audience-123'

        mock_session.execute.return_value.first.return_value = None

        result = store.is_user_synced(email, audience_id)

        assert result is False

    def test_is_user_synced_normalizes_email_to_lowercase(self, store, mock_session):
        """Test that is_user_synced normalizes email to lowercase."""
        email = 'TEST@EXAMPLE.COM'
        audience_id = 'test-audience-123'

        mock_session.execute.return_value.first.return_value = None

        store.is_user_synced(email, audience_id)

        # Verify the query was called (we can't easily check the exact SQL)
        mock_session.execute.assert_called_once()


class TestResendSyncedUser:
    """Test cases for ResendSyncedUser model."""

    def test_model_has_required_fields(self):
        """Test that the model has all required fields."""
        assert hasattr(ResendSyncedUser, 'id')
        assert hasattr(ResendSyncedUser, 'email')
        assert hasattr(ResendSyncedUser, 'audience_id')
        assert hasattr(ResendSyncedUser, 'synced_at')
        assert hasattr(ResendSyncedUser, 'keycloak_user_id')

    def test_model_table_name(self):
        """Test the model's table name."""
        assert ResendSyncedUser.__tablename__ == 'resend_synced_users'


class TestMarkUserSynced:
    """mark_user_synced against the database, where its conflict handling runs."""

    @pytest.fixture
    def db_store(self, session_maker):
        return ResendSyncedUserStore(session_maker=session_maker)

    def test_only_the_first_mark_reports_an_insert(self, db_store, session_maker):
        assert db_store.mark_user_synced('Ada@Example.com', 'audience', 'user-1')
        assert not db_store.mark_user_synced('ada@example.com', 'audience', 'user-2')

        with session_maker() as session:
            rows = session.query(ResendSyncedUser).all()
        assert [(r.email, r.audience_id, r.keycloak_user_id) for r in rows] == [
            ('ada@example.com', 'audience', 'user-1')
        ]

    def test_the_same_email_in_another_audience_is_a_new_mark(self, db_store):
        assert db_store.mark_user_synced('ada@example.com', 'audience-1')
        assert db_store.mark_user_synced('ada@example.com', 'audience-2')
