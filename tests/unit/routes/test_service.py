"""Unit tests for service API routes."""

import hmac
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from server.routes.service import (
    CreateUserApiKeyRequest,
    delete_user_api_key,
    get_or_create_api_key_for_user,
    get_org_member_info,
    validate_service_api_key,
)


class TestValidateServiceApiKey:
    """Test cases for validate_service_api_key."""

    @pytest.mark.asyncio
    async def test_valid_service_key(self):
        """Test validation with valid service API key."""
        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-service-key'):
            result = await validate_service_api_key('test-service-key')
        assert result == 'automations-service'

    @pytest.mark.asyncio
    async def test_missing_service_key(self):
        """Test validation with missing service API key header."""
        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-service-key'):
            with pytest.raises(HTTPException) as exc_info:
                await validate_service_api_key(None)
        assert exc_info.value.status_code == 401
        assert 'X-Service-API-Key header is required' in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_invalid_service_key(self):
        """Test validation with invalid service API key."""
        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-service-key'):
            with pytest.raises(HTTPException) as exc_info:
                await validate_service_api_key('wrong-key')
        assert exc_info.value.status_code == 401
        assert 'Invalid service API key' in exc_info.value.detail

    @pytest.mark.asyncio
    @pytest.mark.parametrize('provided', ['wrong-key', 'tést-service-key'])
    async def test_invalid_service_key_is_rejected_in_constant_time(self, provided):
        """Wrong or non-ASCII keys get 401 through a constant-time compare."""
        with (
            patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-service-key'),
            patch('hmac.compare_digest', wraps=hmac.compare_digest) as spy,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await validate_service_api_key(provided)
        assert exc_info.value.status_code == 401
        spy.assert_called_once()

    @pytest.mark.asyncio
    async def test_service_auth_not_configured(self):
        """Test validation when service auth is not configured."""
        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', ''):
            with pytest.raises(HTTPException) as exc_info:
                await validate_service_api_key('any-key')
        assert exc_info.value.status_code == 503
        assert 'Service authentication not configured' in exc_info.value.detail


class TestCreateUserApiKeyRequest:
    """Test cases for CreateUserApiKeyRequest validation."""

    def test_valid_request(self):
        """Test valid request with all fields."""
        request = CreateUserApiKeyRequest(
            name='automation',
        )
        assert request.name == 'automation'

    def test_name_is_required(self):
        """Test that name field is required."""
        with pytest.raises(ValueError):
            CreateUserApiKeyRequest(
                name='',  # Empty name should fail
            )

    def test_name_is_stripped(self):
        """Test that name field is stripped of whitespace."""
        request = CreateUserApiKeyRequest(
            name='  automation  ',
        )
        assert request.name == 'automation'

    def test_whitespace_only_name_fails(self):
        """Test that whitespace-only name fails validation."""
        with pytest.raises(ValueError):
            CreateUserApiKeyRequest(
                name='   ',
            )


class TestGetOrCreateApiKeyForUser:
    """Test cases for get_or_create_api_key_for_user endpoint."""

    @pytest.fixture
    def valid_user_id(self):
        """Return a valid user ID."""
        return '5594c7b6-f959-4b81-92e9-b09c206f5081'

    @pytest.fixture
    def valid_org_id(self):
        """Return a valid org ID."""
        return uuid.UUID('5594c7b6-f959-4b81-92e9-b09c206f5081')

    @pytest.fixture
    def valid_request(self):
        """Create a valid request object."""
        return CreateUserApiKeyRequest(
            name='automation',
        )

    @pytest.mark.asyncio
    async def test_user_not_found(self, valid_user_id, valid_org_id, valid_request):
        """Test error when user doesn't exist."""
        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-key'):
            with patch(
                'server.routes.service.UserStore.get_user_by_id', new_callable=AsyncMock
            ) as mock_get_user:
                mock_get_user.return_value = None
                with pytest.raises(HTTPException) as exc_info:
                    await get_or_create_api_key_for_user(
                        user_id=valid_user_id,
                        org_id=valid_org_id,
                        request=valid_request,
                        x_service_api_key='test-key',
                    )
        assert exc_info.value.status_code == 404
        assert 'not found' in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_invalid_user_id_returns_404(self, valid_org_id, valid_request):
        """A user_id that is not a UUID is treated as an unknown user, not a 500."""
        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-key'):
            with patch(
                'server.routes.service.UserStore.get_user_by_id', new_callable=AsyncMock
            ) as mock_get_user:
                mock_get_user.return_value = MagicMock()
                with pytest.raises(HTTPException) as exc_info:
                    await get_or_create_api_key_for_user(
                        user_id='not-a-uuid',
                        org_id=valid_org_id,
                        request=valid_request,
                        x_service_api_key='test-key',
                    )
        assert exc_info.value.status_code == 404
        assert 'not found' in exc_info.value.detail
        # UserStore.get_user_by_id parses the ID too; it must not see invalid input.
        mock_get_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_not_in_org(self, valid_user_id, valid_org_id, valid_request):
        """Test error when user is not a member of the org."""
        mock_user = MagicMock()

        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-key'):
            with patch(
                'server.routes.service.UserStore.get_user_by_id', new_callable=AsyncMock
            ) as mock_get_user:
                with patch(
                    'server.routes.service.OrgMemberStore.get_org_member',
                    new_callable=AsyncMock,
                ) as mock_get_member:
                    mock_get_user.return_value = mock_user
                    mock_get_member.return_value = None
                    with pytest.raises(HTTPException) as exc_info:
                        await get_or_create_api_key_for_user(
                            user_id=valid_user_id,
                            org_id=valid_org_id,
                            request=valid_request,
                            x_service_api_key='test-key',
                        )
        assert exc_info.value.status_code == 403
        assert 'not a member of org' in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_successful_key_creation(
        self, valid_user_id, valid_org_id, valid_request
    ):
        """Test successful API key creation."""
        mock_user = MagicMock()
        mock_org_member = MagicMock()
        mock_api_key_store = MagicMock()
        mock_api_key_store.get_or_create_system_api_key = AsyncMock(
            return_value='sk-oh-test-key-12345678901234567890'
        )

        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-key'):
            with patch(
                'server.routes.service.UserStore.get_user_by_id', new_callable=AsyncMock
            ) as mock_get_user:
                with patch(
                    'server.routes.service.OrgMemberStore.get_org_member',
                    new_callable=AsyncMock,
                ) as mock_get_member:
                    with patch(
                        'server.routes.service.ApiKeyStore.get_instance'
                    ) as mock_get_store:
                        mock_get_user.return_value = mock_user
                        mock_get_member.return_value = mock_org_member
                        mock_get_store.return_value = mock_api_key_store

                        response = await get_or_create_api_key_for_user(
                            user_id=valid_user_id,
                            org_id=valid_org_id,
                            request=valid_request,
                            x_service_api_key='test-key',
                        )

        assert response.key == 'sk-oh-test-key-12345678901234567890'
        assert response.user_id == valid_user_id
        assert response.org_id == str(valid_org_id)
        assert response.name == 'automation'

        # Verify the store was called with correct arguments
        mock_api_key_store.get_or_create_system_api_key.assert_called_once_with(
            user_id=valid_user_id,
            org_id=valid_org_id,
            name='automation',
        )

    @pytest.mark.asyncio
    async def test_key_is_minted_for_canonical_user_id(
        self, valid_user_id, valid_org_id, valid_request
    ):
        """A non-canonical UUID spelling still mints the key for the canonical ID.

        Bearer auth and member removal match keys to org_member rows by the
        canonical (lowercase, hyphenated) user ID.
        """
        mock_api_key_store = MagicMock()
        mock_api_key_store.get_or_create_system_api_key = AsyncMock(
            return_value='sk-oh-test-key-12345678901234567890'
        )

        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-key'):
            with patch(
                'server.routes.service.UserStore.get_user_by_id', new_callable=AsyncMock
            ) as mock_get_user:
                with patch(
                    'server.routes.service.OrgMemberStore.get_org_member',
                    new_callable=AsyncMock,
                ) as mock_get_member:
                    with patch(
                        'server.routes.service.ApiKeyStore.get_instance'
                    ) as mock_get_store:
                        mock_get_user.return_value = MagicMock()
                        mock_get_member.return_value = MagicMock()
                        mock_get_store.return_value = mock_api_key_store

                        await get_or_create_api_key_for_user(
                            user_id=valid_user_id.upper(),
                            org_id=valid_org_id,
                            request=valid_request,
                            x_service_api_key='test-key',
                        )

        mock_api_key_store.get_or_create_system_api_key.assert_called_once_with(
            user_id=valid_user_id,
            org_id=valid_org_id,
            name='automation',
        )

    @pytest.mark.asyncio
    async def test_store_exception_handling(
        self, valid_user_id, valid_org_id, valid_request
    ):
        """Test error handling when store raises exception."""
        mock_user = MagicMock()
        mock_org_member = MagicMock()
        mock_api_key_store = MagicMock()
        mock_api_key_store.get_or_create_system_api_key = AsyncMock(
            side_effect=Exception('Database error')
        )

        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-key'):
            with patch(
                'server.routes.service.UserStore.get_user_by_id', new_callable=AsyncMock
            ) as mock_get_user:
                with patch(
                    'server.routes.service.OrgMemberStore.get_org_member',
                    new_callable=AsyncMock,
                ) as mock_get_member:
                    with patch(
                        'server.routes.service.ApiKeyStore.get_instance'
                    ) as mock_get_store:
                        mock_get_user.return_value = mock_user
                        mock_get_member.return_value = mock_org_member
                        mock_get_store.return_value = mock_api_key_store

                        with pytest.raises(HTTPException) as exc_info:
                            await get_or_create_api_key_for_user(
                                user_id=valid_user_id,
                                org_id=valid_org_id,
                                request=valid_request,
                                x_service_api_key='test-key',
                            )

        assert exc_info.value.status_code == 500
        assert 'Failed to get or create API key' in exc_info.value.detail


class TestDeleteUserApiKey:
    """Test cases for delete_user_api_key endpoint."""

    @pytest.fixture
    def valid_org_id(self):
        """Return a valid org ID."""
        return uuid.UUID('5594c7b6-f959-4b81-92e9-b09c206f5081')

    @pytest.mark.asyncio
    async def test_successful_delete(self, valid_org_id):
        """Test successful deletion of a system API key."""
        mock_api_key_store = MagicMock()
        mock_api_key_store.make_system_key_name.return_value = '__SYSTEM__:automation'
        mock_api_key_store.delete_api_key_by_name = AsyncMock(return_value=True)

        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-key'):
            with patch(
                'server.routes.service.ApiKeyStore.get_instance'
            ) as mock_get_store:
                mock_get_store.return_value = mock_api_key_store

                response = await delete_user_api_key(
                    user_id='user-123',
                    org_id=valid_org_id,
                    key_name='automation',
                    x_service_api_key='test-key',
                )

        assert response == {'message': 'API key deleted successfully'}

        # Verify the store was called with correct arguments
        mock_api_key_store.make_system_key_name.assert_called_once_with('automation')
        mock_api_key_store.delete_api_key_by_name.assert_called_once_with(
            user_id='user-123',
            org_id=valid_org_id,
            name='__SYSTEM__:automation',
            allow_system=True,
        )

    @pytest.mark.asyncio
    async def test_delete_uses_canonical_user_id(self, valid_org_id):
        """Delete matches the canonical user ID that the mint endpoint stores."""
        mock_api_key_store = MagicMock()
        mock_api_key_store.make_system_key_name.return_value = '__SYSTEM__:automation'
        mock_api_key_store.delete_api_key_by_name = AsyncMock(return_value=True)
        user_id = '5594c7b6-f959-4b81-92e9-b09c206f5081'

        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-key'):
            with patch(
                'server.routes.service.ApiKeyStore.get_instance'
            ) as mock_get_store:
                mock_get_store.return_value = mock_api_key_store

                await delete_user_api_key(
                    user_id=user_id.upper(),
                    org_id=valid_org_id,
                    key_name='automation',
                    x_service_api_key='test-key',
                )

        mock_api_key_store.delete_api_key_by_name.assert_called_once_with(
            user_id=user_id,
            org_id=valid_org_id,
            name='__SYSTEM__:automation',
            allow_system=True,
        )

    @pytest.mark.asyncio
    async def test_delete_key_not_found(self, valid_org_id):
        """Test error when key to delete is not found."""
        mock_api_key_store = MagicMock()
        mock_api_key_store.make_system_key_name.return_value = '__SYSTEM__:nonexistent'
        mock_api_key_store.delete_api_key_by_name = AsyncMock(return_value=False)

        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-key'):
            with patch(
                'server.routes.service.ApiKeyStore.get_instance'
            ) as mock_get_store:
                mock_get_store.return_value = mock_api_key_store

                with pytest.raises(HTTPException) as exc_info:
                    await delete_user_api_key(
                        user_id='user-123',
                        org_id=valid_org_id,
                        key_name='nonexistent',
                        x_service_api_key='test-key',
                    )

        assert exc_info.value.status_code == 404
        assert 'not found' in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_delete_invalid_service_key(self, valid_org_id):
        """Test error when service API key is invalid."""
        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-key'):
            with pytest.raises(HTTPException) as exc_info:
                await delete_user_api_key(
                    user_id='user-123',
                    org_id=valid_org_id,
                    key_name='automation',
                    x_service_api_key='wrong-key',
                )

        assert exc_info.value.status_code == 401
        assert 'Invalid service API key' in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_delete_missing_service_key(self, valid_org_id):
        """Test error when service API key header is missing."""
        with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-key'):
            with pytest.raises(HTTPException) as exc_info:
                await delete_user_api_key(
                    user_id='user-123',
                    org_id=valid_org_id,
                    key_name='automation',
                    x_service_api_key=None,
                )

        assert exc_info.value.status_code == 401
        assert 'X-Service-API-Key header is required' in exc_info.value.detail


class TestGetOrgMemberInfo:
    """Test cases for get_org_member_info (GET .../users/{user_id}/orgs/{org_id}/membership)."""

    USER_ID = '5594c7b6-f959-4b81-92e9-b09c206f5081'
    ORG_ID = uuid.UUID('7a1b2c3d-0000-4000-8000-000000000001')

    def _user(self, account_type='human', is_disabled=False, deletion_pending=False):
        user = MagicMock()
        user.account_type = account_type
        user.is_disabled = is_disabled
        user.deletion_pending = deletion_pending
        return user

    def _member(self, status='active'):
        member = MagicMock()
        member.status = status
        member.role_id = 4
        return member

    async def _call(self, user, member, role_name='member', user_id=None):
        role = MagicMock()
        role.name = role_name
        with (
            patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', 'test-key'),
            patch(
                'server.routes.service.UserStore.get_user_by_id',
                new=AsyncMock(return_value=user),
            ),
            patch(
                'server.routes.service.OrgMemberStore.get_org_member',
                new=AsyncMock(return_value=member),
            ),
            patch(
                'server.routes.service.RoleStore.get_role_by_id',
                new=AsyncMock(return_value=role),
            ),
        ):
            return await get_org_member_info(
                user_id=user_id or self.USER_ID,
                org_id=self.ORG_ID,
                x_service_api_key='test-key',
            )

    @pytest.mark.asyncio
    async def test_service_account(self):
        result = await self._call(
            self._user('service'), self._member(), 'service_account'
        )

        assert result.model_dump() == {
            'user_id': self.USER_ID,
            'org_id': str(self.ORG_ID),
            'account_type': 'service',
            'role': 'service_account',
            'status': 'active',
        }

    @pytest.mark.asyncio
    async def test_human(self):
        result = await self._call(self._user(), self._member(), 'admin')

        assert result.account_type == 'human'
        assert result.role == 'admin'
        assert result.status == 'active'

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'user',
        [
            {'is_disabled': True},
            {'deletion_pending': True},
        ],
    )
    async def test_disabled_user_reports_disabled(self, user):
        result = await self._call(self._user(**user), self._member())

        assert result.status == 'disabled'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('member_status', ['inactive', 'invited'])
    async def test_member_status_is_passed_through(self, member_status):
        result = await self._call(self._user(), self._member(member_status))

        assert result.status == member_status

    @pytest.mark.asyncio
    async def test_null_member_status_is_active(self):
        result = await self._call(self._user(), self._member(None))

        assert result.status == 'active'

    @pytest.mark.asyncio
    async def test_unknown_user_returns_404(self):
        with pytest.raises(HTTPException) as exc_info:
            await self._call(None, self._member())

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_no_member_row_returns_404(self):
        with pytest.raises(HTTPException) as exc_info:
            await self._call(self._user(), None)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_non_uuid_user_id_returns_404(self):
        with pytest.raises(HTTPException) as exc_info:
            await self._call(self._user(), self._member(), user_id='not-a-uuid')

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ('configured', 'provided', 'code'),
        [
            ('test-key', None, 401),
            ('test-key', 'wrong', 401),
            ('', 'test-key', 503),
        ],
    )
    async def test_service_key_is_checked_first(self, configured, provided, code):
        with (
            patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', configured),
            patch(
                'server.routes.service.UserStore.get_user_by_id',
                new=AsyncMock(),
            ) as get_user,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_org_member_info(
                    user_id=self.USER_ID,
                    org_id=self.ORG_ID,
                    x_service_api_key=provided,
                )

        assert exc_info.value.status_code == code
        get_user.assert_not_awaited()
