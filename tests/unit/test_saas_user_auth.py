import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import Request
from keycloak.exceptions import KeycloakConnectionError
from pydantic import SecretStr

from openhands.app_server.integrations.provider import ProviderToken, ProviderType
from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.user_auth.user_auth import AuthType
from server.auth.auth_error import (
    AuthError,
    BearerTokenError,
    CookieError,
    TokenRefreshError,
)
from server.auth.contracts import InvalidCredentials
from server.auth.saas_user_auth import (
    SaasUserAuth,
    get_api_key_from_header,
    get_user_auth_from_keycloak_id,
    saas_user_auth_from_bearer,
    saas_user_auth_from_cookie,
    saas_user_auth_from_signed_token,
)
from storage.api_key_store import ApiKeyValidationResult
from storage.user_authorization import UserAuthorizationType


@pytest.fixture
def mock_request():
    request = MagicMock(spec=Request)
    request.headers = {}
    request.cookies = {}
    return request


def create_mock_jwt_tokens(
    user_id='11111111-1111-4111-8111-111111111111', exp_offset=3600
):
    """Helper to create valid JWT tokens for mocking."""
    payload = {
        'sub': user_id,
        'exp': int(time.time()) + exp_offset,
        'email': 'test@example.com',
        'email_verified': True,
    }
    access_token = jwt.encode(payload, 'secret', algorithm='HS256')
    refresh_token = jwt.encode(
        {'sub': user_id, 'exp': int(time.time()) + exp_offset},
        'secret',
        algorithm='HS256',
    )
    return {'access_token': access_token, 'refresh_token': refresh_token}


@pytest.fixture
def mock_token_manager():
    with patch('server.auth.saas_user_auth.token_manager') as mock_tm:
        mock_tm.refresh = AsyncMock(return_value=create_mock_jwt_tokens())
        mock_tm.get_user_info_from_user_id = AsyncMock(
            return_value={
                'federatedIdentities': [
                    {
                        'identityProvider': 'github',
                        'userId': 'github_user_id',
                    }
                ]
            }
        )
        mock_tm.get_idp_token = AsyncMock(return_value='github_token')
        yield mock_tm


@pytest.fixture
def mock_config():
    from openhands.app_server.services.jwt_service import JwtService
    from openhands.app_server.utils.encryption_key import EncryptionKey

    jwt_svc = JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('test_secret'), active=True)]
    )
    with patch('storage.encrypt_utils.get_jwt_service', return_value=jwt_svc):
        yield


@pytest.mark.asyncio
async def test_get_user_id():
    """Test that get_user_id returns the user_id."""
    user_auth = SaasUserAuth(
        user_id='11111111-1111-4111-8111-111111111111',
        refresh_token=SecretStr('refresh_token'),
    )

    user_id = await user_auth.get_user_id()

    assert user_id == '11111111-1111-4111-8111-111111111111'


@pytest.mark.asyncio
async def test_get_user_email():
    """Test that get_user_email returns the email."""
    user_auth = SaasUserAuth(
        user_id='11111111-1111-4111-8111-111111111111',
        refresh_token=SecretStr('refresh_token'),
        email='test@example.com',
    )

    email = await user_auth.get_user_email()

    assert email == 'test@example.com'


@pytest.mark.asyncio
async def test_refresh(mock_token_manager):
    """Test that refresh updates the tokens."""
    refresh_token = jwt.encode(
        {
            'sub': '11111111-1111-4111-8111-111111111111',
            'exp': int(time.time()) + 3600,
        },
        'secret',
        algorithm='HS256',
    )

    user_auth = SaasUserAuth(
        user_id='11111111-1111-4111-8111-111111111111',
        refresh_token=SecretStr(refresh_token),
    )

    await user_auth.refresh()

    mock_token_manager.refresh.assert_called_once_with(refresh_token)
    # Access token should be a valid JWT
    access_token = user_auth.access_token.get_secret_value()
    decoded = jwt.decode(access_token, options={'verify_signature': False})
    assert decoded['sub'] == '11111111-1111-4111-8111-111111111111'
    assert decoded['email'] == 'test@example.com'
    assert user_auth.refreshed is True


@pytest.mark.asyncio
async def test_get_access_token_with_existing_valid_token(mock_token_manager):
    """Test that get_access_token returns the existing token if it's valid."""
    # Create a valid JWT token that expires in the future
    payload = {
        'sub': '11111111-1111-4111-8111-111111111111',
        'exp': int(time.time()) + 3600,  # Expires in 1 hour
    }
    access_token = jwt.encode(payload, 'secret', algorithm='HS256')

    user_auth = SaasUserAuth(
        user_id='11111111-1111-4111-8111-111111111111',
        refresh_token=SecretStr('refresh_token'),
        access_token=SecretStr(access_token),
    )

    result = await user_auth.get_access_token()

    assert result.get_secret_value() == access_token
    mock_token_manager.refresh.assert_not_called()


@pytest.mark.asyncio
async def test_get_access_token_with_expired_token(mock_token_manager):
    """Test that get_access_token refreshes the token if it's expired."""
    # Create expired access token and valid refresh token
    access_token, refresh_token = (
        jwt.encode(
            {
                'sub': '11111111-1111-4111-8111-111111111111',
                'exp': int(time.time()) + exp,
            },
            'secret',
            algorithm='HS256',
        )
        for exp in [-3600, 3600]
    )

    user_auth = SaasUserAuth(
        user_id='11111111-1111-4111-8111-111111111111',
        refresh_token=SecretStr(refresh_token),
        access_token=SecretStr(access_token),
    )

    result = await user_auth.get_access_token()

    # Verify the returned token is a valid JWT with correct user_id
    decoded = jwt.decode(result.get_secret_value(), options={'verify_signature': False})
    assert decoded['sub'] == '11111111-1111-4111-8111-111111111111'
    mock_token_manager.refresh.assert_called_once_with(refresh_token)


@pytest.mark.asyncio
async def test_get_access_token_with_no_token(mock_token_manager):
    """Test that get_access_token refreshes when no token exists."""
    refresh_token = jwt.encode(
        {
            'sub': '11111111-1111-4111-8111-111111111111',
            'exp': int(time.time()) + 3600,
        },
        'secret',
        algorithm='HS256',
    )

    user_auth = SaasUserAuth(
        user_id='11111111-1111-4111-8111-111111111111',
        refresh_token=SecretStr(refresh_token),
    )

    result = await user_auth.get_access_token()

    # Verify the returned token is a valid JWT with correct user_id
    decoded = jwt.decode(result.get_secret_value(), options={'verify_signature': False})
    assert decoded['sub'] == '11111111-1111-4111-8111-111111111111'
    mock_token_manager.refresh.assert_called_once_with(refresh_token)


@pytest.mark.asyncio
async def test_get_access_token_classifies_keycloak_connection_failure(
    mock_token_manager,
):
    refresh_token = jwt.encode(
        {
            'sub': '11111111-1111-4111-8111-111111111111',
            'exp': int(time.time()) + 3600,
        },
        'secret',
        algorithm='HS256',
    )
    user_auth = SaasUserAuth(
        user_id='11111111-1111-4111-8111-111111111111',
        refresh_token=SecretStr(refresh_token),
    )
    mock_token_manager.refresh = AsyncMock(
        side_effect=KeycloakConnectionError('DNS failure')
    )

    with pytest.raises(
        TokenRefreshError, match='Authentication service temporarily unavailable'
    ):
        await user_auth.get_access_token()

    assert mock_token_manager.refresh.await_count == 1


@pytest.mark.asyncio
async def test_get_provider_tokens_delegates_to_credential_service():
    tokens = {
        ProviderType.GITHUB: ProviderToken(
            token=SecretStr('github_token'),
            user_id='verified-account',
            host='github.com',
        )
    }
    auth = SaasUserAuth(user_id='11111111-1111-4111-8111-111111111111')
    with patch(
        'server.auth.provider_credentials.ProviderCredentialService.list_tokens',
        AsyncMock(return_value=tokens),
    ) as load:
        assert await auth.get_provider_tokens() == tokens
    load.assert_awaited_once_with('11111111-1111-4111-8111-111111111111')


@pytest.mark.asyncio
async def test_provider_reconnect_does_not_expire_browser_session():
    from server.auth.contracts import ProviderReconnectRequired

    auth = SaasUserAuth(
        user_id='11111111-1111-4111-8111-111111111111',
        refresh_token=SecretStr('browser-session'),
    )
    with patch(
        'server.auth.provider_credentials.ProviderCredentialService.list_tokens',
        AsyncMock(side_effect=ProviderReconnectRequired('Reconnect GitHub')),
    ):
        with pytest.raises(ProviderReconnectRequired):
            await auth.get_provider_tokens()
    assert auth.refresh_token.get_secret_value() == 'browser-session'


@pytest.mark.asyncio
async def test_get_provider_tokens_cached(mock_token_manager):
    """Test that get_provider_tokens returns cached tokens if available."""
    user_auth = SaasUserAuth(
        user_id='11111111-1111-4111-8111-111111111111',
        refresh_token=SecretStr('refresh_token'),
        provider_tokens={
            ProviderType.GITHUB: ProviderToken(
                token=SecretStr('cached_github_token'),
                user_id='github_user_id',
            )
        },
    )

    result = await user_auth.get_provider_tokens()

    assert ProviderType.GITHUB in result
    assert result[ProviderType.GITHUB].token.get_secret_value() == 'cached_github_token'
    mock_token_manager.get_user_info_from_user_id.assert_not_called()
    mock_token_manager.get_idp_token.assert_not_called()


# =============================================================================
# API-key (bearer) decoupling from Keycloak offline sessions
# =============================================================================


@pytest.mark.asyncio
async def test_get_user_email_lazy_loads_from_db():
    """Bearer auth resolves email from the DB (User row), not from Keycloak."""
    # Arrange
    user_auth = SaasUserAuth(
        user_id='11111111-1111-4111-8111-111111111111',
        refresh_token=SecretStr(''),
        auth_type=AuthType.BEARER,
    )
    mock_user = MagicMock()
    mock_user.email = 'user@example.com'
    mock_user.email_verified = True

    with patch('server.auth.saas_user_auth.UserStore') as mock_user_store:
        mock_user_store.get_user_by_id = AsyncMock(return_value=mock_user)

        # Act
        email = await user_auth.get_user_email()

    # Assert
    assert email == 'user@example.com'
    assert user_auth.email_verified is True


@pytest.mark.asyncio
async def test_get_access_token_returns_none_for_bearer_when_session_revoked():
    """Bearer get_access_token degrades to None when the offline session is gone.

    A revoked offline session surfaces as a Keycloak refresh failure
    (``invalid_grant``). For API-key auth that must not raise a 401 — the
    Keycloak access token is optional.
    """
    # Arrange
    from keycloak.exceptions import KeycloakPostError

    offline_token = jwt.encode(
        {'sub': '11111111-1111-4111-8111-111111111111', 'exp': int(time.time()) + 3600},
        'secret',
        algorithm='HS256',
    )
    user_auth = SaasUserAuth(
        user_id='11111111-1111-4111-8111-111111111111',
        refresh_token=SecretStr(''),
        auth_type=AuthType.BEARER,
    )

    with patch('server.auth.saas_user_auth.token_manager') as mock_tm:
        mock_tm.load_offline_token = AsyncMock(return_value=offline_token)
        mock_tm.refresh = AsyncMock(
            side_effect=KeycloakPostError(
                'invalid_grant: Offline user session not found'
            )
        )

        # Act
        result = await user_auth.get_access_token()

    # Assert
    assert result is None


@pytest.mark.asyncio
async def test_get_provider_tokens_succeeds_without_offline_session():
    user_auth = SaasUserAuth(
        user_id='11111111-1111-4111-8111-111111111111', auth_type=AuthType.BEARER
    )
    tokens = {ProviderType.GITHUB: ProviderToken(token=SecretStr('github_token'))}
    with (
        patch(
            'server.auth.provider_credentials.ProviderCredentialService.list_tokens',
            AsyncMock(return_value=tokens),
        ),
        patch('server.auth.saas_user_auth.token_manager') as manager,
    ):
        assert await user_auth.get_provider_tokens() == tokens
    manager.refresh.assert_not_called()
    manager.load_offline_token.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'build', [SaasUserAuth.get_for_user, get_user_auth_from_keycloak_id]
)
async def test_background_user_auth_does_not_require_offline_session(build):
    user_id = str(uuid.uuid4())
    user = MagicMock(
        is_disabled=False,
        email='user@example.com',
        email_verified=True,
        accepted_tos=True,
        current_org_id=uuid.UUID(user_id),
    )
    with (
        patch(
            'server.auth.user_management.EnterpriseUserManagementService.ensure_authenticated_account',
            AsyncMock(return_value=user),
        ),
        patch(
            'server.auth.saas_user_auth.UserStore.get_user_by_id',
            AsyncMock(return_value=user),
        ),
        patch('server.auth.saas_user_auth.token_manager') as manager,
    ):
        built = await build(user_id)
        assert await built.get_access_token() is None
    assert built.user_id == user_id
    assert built.auth_type == AuthType.BEARER
    assert built.refresh_token is None
    assert built.principal.authentication_method == 'background'
    manager.load_offline_token.assert_not_called()
    manager.refresh.assert_not_called()


@pytest.mark.asyncio
async def test_get_user_settings_store():
    """Test that get_user_settings_store returns a settings store."""
    user_id = str(uuid.uuid4())
    org_id = uuid.uuid4()
    mock_user = MagicMock()
    mock_user.current_org_id = org_id

    with (
        patch('server.auth.saas_user_auth.SaasSettingsStore') as mock_store_cls,
        patch('server.auth.saas_user_auth.UserStore') as mock_user_store,
    ):
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_user_store.get_user_by_id = AsyncMock(return_value=mock_user)

        user_auth = SaasUserAuth(
            user_id=user_id,
            refresh_token=SecretStr('refresh_token'),
        )

        result = await user_auth.get_user_settings_store()

        assert result == mock_store
        mock_store_cls.assert_called_once_with(user_id, effective_org_id=org_id)
        assert user_auth.settings_store == mock_store


@pytest.mark.asyncio
async def test_get_user_settings_store_cached():
    """Test that get_user_settings_store returns cached store if available."""
    mock_store = MagicMock()

    user_auth = SaasUserAuth(
        user_id='11111111-1111-4111-8111-111111111111',
        refresh_token=SecretStr('refresh_token'),
        settings_store=mock_store,
    )

    result = await user_auth.get_user_settings_store()

    assert result == mock_store


@pytest.mark.asyncio
async def test_get_instance_from_bearer(mock_request):
    with patch(
        'server.auth.authentication.AuthenticationService.authenticate_request',
        new_callable=AsyncMock,
    ) as authenticate_request:
        auth = SaasUserAuth(user_id='11111111-1111-4111-8111-111111111111')
        authenticate_request.return_value = auth
        assert await SaasUserAuth.get_instance(mock_request) is auth
        authenticate_request.assert_awaited_once_with(mock_request)


@pytest.mark.asyncio
async def test_get_instance_without_rate_limiter(mock_request):
    """Authentication succeeds without hitting a disabled rate limiter."""
    mock_request.state.user_rate_limit_processed = False
    mock_auth = MagicMock()
    mock_auth.get_user_id = AsyncMock(
        return_value='11111111-1111-4111-8111-111111111111'
    )

    with (
        patch(
            'server.auth.authentication.AuthenticationService.authenticate_request',
            return_value=mock_auth,
        ),
        patch('server.auth.saas_user_auth.rate_limiter', None),
    ):
        result = await SaasUserAuth.get_instance(mock_request)

    assert result == mock_auth
    mock_auth.get_user_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_instance_from_cookie(mock_request):
    mock_request.cookies = {'keycloak_auth': 'signed-cookie'}
    with patch(
        'server.auth.authentication.AuthenticationService.authenticate_request',
        new_callable=AsyncMock,
    ) as authenticate_request:
        auth = SaasUserAuth(user_id='11111111-1111-4111-8111-111111111111')
        authenticate_request.return_value = auth
        assert await SaasUserAuth.get_instance(mock_request) is auth
        authenticate_request.assert_awaited_once_with(mock_request)


@pytest.mark.asyncio
async def test_get_instance_no_auth(mock_request):
    with patch(
        'server.auth.authentication.AuthenticationService.authenticate_request',
        new_callable=AsyncMock,
        side_effect=InvalidCredentials('Authentication required'),
    ):
        with pytest.raises(InvalidCredentials):
            await SaasUserAuth.get_instance(mock_request)


@pytest.mark.asyncio
async def test_saas_user_auth_from_bearer_success():
    """A valid API key authenticates with no Keycloak round-trip.

    Bearer auth must not load an offline token or call refresh(): the key
    alone authenticates the request, so a missing/revoked offline session can
    no longer turn a valid key into a 401.
    """
    # Arrange
    mock_request = MagicMock(cookies={}, headers={})
    mock_request.headers = {'Authorization': 'Bearer test_api_key'}

    mock_org_id = uuid.uuid4()
    mock_validation_result = ApiKeyValidationResult(
        user_id='11111111-1111-4111-8111-111111111111',
        org_id=mock_org_id,
        key_id=42,
        key_name='Test Key',
    )

    with (
        patch('server.auth.authentication.ApiKeyStore') as mock_api_key_store_cls,
        patch('server.auth.saas_user_auth.token_manager') as mock_token_manager,
    ):
        mock_api_key_store = MagicMock()
        mock_api_key_store.validate_api_key = AsyncMock(
            return_value=mock_validation_result
        )
        mock_api_key_store_cls.get_instance.return_value = mock_api_key_store
        mock_token_manager.load_offline_token = AsyncMock()
        mock_token_manager.refresh = AsyncMock()

        # Act
        result = await saas_user_auth_from_bearer(mock_request)

        # Assert
        assert isinstance(result, SaasUserAuth)
        assert result.user_id == '11111111-1111-4111-8111-111111111111'
        assert result.api_key_org_id == mock_org_id
        assert result.api_key_id == 42
        assert result.api_key_name == 'Test Key'
        assert result.auth_type == AuthType.BEARER
        mock_api_key_store.validate_api_key.assert_called_once_with('test_api_key')
        # Decoupled from Keycloak: no offline-session load, no refresh.
        mock_token_manager.load_offline_token.assert_not_called()
        mock_token_manager.refresh.assert_not_called()


@pytest.mark.asyncio
async def test_saas_user_auth_from_bearer_rejects_disabled_user():
    """A valid API key cannot authenticate a locally disabled user."""
    user_id = str(uuid.uuid4())
    request = MagicMock(cookies={}, headers={})
    request.headers = {'Authorization': 'Bearer disabled_key'}
    validation = ApiKeyValidationResult(
        user_id=user_id, org_id=uuid.uuid4(), key_id=7, key_name='key'
    )
    disabled_user = MagicMock(is_disabled=True)

    with (
        patch('server.auth.authentication.ApiKeyStore') as store_cls,
        patch(
            'server.auth.saas_user_auth.UserStore.get_user_by_id',
            AsyncMock(return_value=disabled_user),
        ),
    ):
        store = MagicMock()
        store.validate_api_key = AsyncMock(return_value=validation)
        store_cls.get_instance.return_value = store
        assert await saas_user_auth_from_bearer(request) is None


@pytest.mark.asyncio
async def test_saas_user_auth_from_bearer_rejects_deleted_user():
    """A valid API key whose user has been deleted must not authenticate."""
    user_id = str(uuid.uuid4())
    request = MagicMock(cookies={}, headers={})
    request.headers = {'Authorization': 'Bearer deleted_key'}
    validation = ApiKeyValidationResult(
        user_id=user_id, org_id=uuid.uuid4(), key_id=8, key_name='key'
    )

    with (
        patch('server.auth.authentication.ApiKeyStore') as store_cls,
        patch(
            'server.auth.saas_user_auth.UserStore.get_user_by_id',
            AsyncMock(return_value=None),
        ),
    ):
        store = MagicMock()
        store.validate_api_key = AsyncMock(return_value=validation)
        store_cls.get_instance.return_value = store
        assert await saas_user_auth_from_bearer(request) is None


@pytest.mark.asyncio
async def test_saas_user_auth_from_bearer_no_auth_header():
    """Test that saas_user_auth_from_bearer returns None if no auth header or cookie."""
    mock_request = MagicMock(cookies={}, headers={})
    mock_request.headers = {}
    mock_request.cookies = {}

    result = await saas_user_auth_from_bearer(mock_request)

    assert result is None


@pytest.mark.asyncio
async def test_saas_user_auth_from_bearer_invalid_api_key():
    """Test that saas_user_auth_from_bearer returns None if API key is invalid."""
    mock_request = MagicMock(cookies={}, headers={})
    mock_request.headers = {'Authorization': 'Bearer test_api_key'}

    with patch('server.auth.authentication.ApiKeyStore') as mock_api_key_store_cls:
        mock_api_key_store = MagicMock()
        mock_api_key_store.validate_api_key = AsyncMock(return_value=None)
        mock_api_key_store_cls.get_instance.return_value = mock_api_key_store

        result = await saas_user_auth_from_bearer(mock_request)

        assert result is None
        mock_api_key_store.validate_api_key.assert_called_once_with('test_api_key')


@pytest.mark.asyncio
async def test_saas_user_auth_from_bearer_key_outside_active_window():
    """A key that validate_api_key rejects (e.g. not yet active) must not produce auth."""
    mock_request = MagicMock(cookies={}, headers={})
    mock_request.headers = {'Authorization': 'Bearer scheduled_key'}

    with patch('server.auth.authentication.ApiKeyStore') as mock_api_key_store_cls:
        mock_api_key_store = MagicMock()
        # Simulate what validate_api_key returns when not_before is in the future.
        mock_api_key_store.validate_api_key = AsyncMock(return_value=None)
        mock_api_key_store_cls.get_instance.return_value = mock_api_key_store

        result = await saas_user_auth_from_bearer(mock_request)

        assert result is None


@pytest.mark.asyncio
async def test_saas_user_auth_from_bearer_exception():
    """Test that saas_user_auth_from_bearer raises BearerTokenError on exception."""
    mock_request = MagicMock(cookies={}, headers={})
    mock_request.headers = {'Authorization': 'Bearer test_api_key'}

    with patch('server.auth.authentication.ApiKeyStore') as mock_api_key_store_cls:
        mock_api_key_store_cls.get_instance.side_effect = Exception('Test error')

        with pytest.raises(BearerTokenError):
            await saas_user_auth_from_bearer(mock_request)


@pytest.mark.asyncio
async def test_saas_user_auth_from_cookie_success(mock_config):
    """Test successful authentication from cookie."""
    # Create a signed token
    payload = {
        'access_token': 'test_access_token',
        'refresh_token': 'test_refresh_token',
    }
    signed_token = jwt.encode(payload, 'test_secret', algorithm='HS256')

    mock_request = MagicMock(cookies={}, headers={})
    mock_request.cookies = {'keycloak_auth': signed_token}

    with patch(
        'server.auth.authentication.AuthenticationService.authenticate_request'
    ) as mock_from_signed:
        mock_auth = MagicMock()
        mock_from_signed.return_value = mock_auth

        result = await saas_user_auth_from_cookie(mock_request)

        assert result == mock_auth
        mock_from_signed.assert_called_once_with(mock_request)


@pytest.mark.asyncio
async def test_saas_user_auth_from_cookie_no_cookie():
    """Test that saas_user_auth_from_cookie returns None if no cookie."""
    mock_request = MagicMock(cookies={}, headers={})
    mock_request.cookies = {}

    result = await saas_user_auth_from_cookie(mock_request)

    assert result is None


@pytest.mark.asyncio
async def test_saas_user_auth_from_cookie_exception():
    """Test that saas_user_auth_from_cookie raises CookieError on exception."""
    mock_request = MagicMock(cookies={}, headers={})
    mock_request.cookies = {'keycloak_auth': 'invalid_token'}

    with pytest.raises(CookieError):
        await saas_user_auth_from_cookie(mock_request)


@pytest.mark.asyncio
async def test_saas_user_auth_from_signed_token(mock_config):
    """Test successful creation of SaasUserAuth from signed token."""
    # Create a JWT access token
    access_payload = {
        'sub': '11111111-1111-4111-8111-111111111111',
        'exp': int(time.time()) + 3600,
        'email': 'test@example.com',
        'email_verified': True,
    }
    access_token = jwt.encode(access_payload, 'access_secret', algorithm='HS256')

    # Create a signed token containing the access and refresh tokens
    token_payload = {
        'access_token': access_token,
        'refresh_token': 'test_refresh_token',
    }
    signed_token = jwt.encode(token_payload, 'test_secret', algorithm='HS256')

    # Mock UserAuthorizationStore to avoid database access
    with patch(
        'server.auth.user.default_user_authorizer.UserAuthorizationStore'
    ) as mock_user_auth_store:
        mock_user_auth_store.get_authorization_type = AsyncMock(return_value=None)

        result = await saas_user_auth_from_signed_token(signed_token)

        assert isinstance(result, SaasUserAuth)
        assert result.user_id == '11111111-1111-4111-8111-111111111111'
        assert result.access_token.get_secret_value() == access_token
        assert result.refresh_token.get_secret_value() == 'test_refresh_token'
        assert result.email == 'test@example.com'
        assert result.email_verified is True


def test_get_api_key_from_header_with_authorization_header():
    """Test that get_api_key_from_header extracts API key from Authorization header."""
    # Create a mock request with Authorization header
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {'Authorization': 'Bearer test_api_key'}

    # Call the function
    api_key = get_api_key_from_header(mock_request)

    # Assert that the API key was correctly extracted
    assert api_key == 'test_api_key'


def test_get_api_key_from_header_with_x_session_api_key():
    """Test that get_api_key_from_header extracts API key from X-Session-API-Key header."""
    # Create a mock request with X-Session-API-Key header
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {'X-Session-API-Key': 'session_api_key'}

    # Call the function
    api_key = get_api_key_from_header(mock_request)

    # Assert that the API key was correctly extracted
    assert api_key == 'session_api_key'


def test_get_api_key_from_header_with_both_headers():
    """Test that get_api_key_from_header prioritizes Authorization header when both are present."""
    # Create a mock request with both headers
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {
        'Authorization': 'Bearer auth_api_key',
        'X-Session-API-Key': 'session_api_key',
    }

    # Call the function
    api_key = get_api_key_from_header(mock_request)

    # Assert that the API key from Authorization header was used
    assert api_key == 'auth_api_key'


def test_get_api_key_from_header_with_no_headers():
    """Test that get_api_key_from_header returns None when no relevant headers or cookies are present."""
    # Create a mock request with no relevant headers or cookies
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {'Other-Header': 'some_value'}
    mock_request.cookies = {}

    # Call the function
    api_key = get_api_key_from_header(mock_request)

    # Assert that None was returned
    assert api_key is None


def test_get_api_key_from_header_with_invalid_authorization_format():
    """Test that get_api_key_from_header handles Authorization headers without 'Bearer ' prefix."""
    # Create a mock request with incorrectly formatted Authorization header
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {'Authorization': 'InvalidFormat api_key'}
    mock_request.cookies = {}

    # Call the function
    api_key = get_api_key_from_header(mock_request)

    # Assert that None was returned
    assert api_key is None


def test_get_api_key_from_header_with_x_access_token():
    """Test that get_api_key_from_header extracts API key from X-Access-Token header."""
    # Create a mock request with X-Access-Token header
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {'X-Access-Token': 'access_token_key'}

    # Call the function
    api_key = get_api_key_from_header(mock_request)

    # Assert that the API key was correctly extracted
    assert api_key == 'access_token_key'


def test_get_api_key_from_header_priority_authorization_over_x_access_token():
    """Test that Authorization header takes priority over X-Access-Token header."""
    # Create a mock request with both headers
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {
        'Authorization': 'Bearer auth_api_key',
        'X-Access-Token': 'access_token_key',
    }

    # Call the function
    api_key = get_api_key_from_header(mock_request)

    # Assert that the API key from Authorization header was used
    assert api_key == 'auth_api_key'


def test_get_api_key_from_header_priority_x_session_over_x_access_token():
    """Test that X-Session-API-Key header takes priority over X-Access-Token header."""
    # Create a mock request with both headers
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {
        'X-Session-API-Key': 'session_api_key',
        'X-Access-Token': 'access_token_key',
    }

    # Call the function
    api_key = get_api_key_from_header(mock_request)

    # Assert that the API key from X-Session-API-Key header was used
    assert api_key == 'session_api_key'


def test_get_api_key_from_header_all_three_headers():
    """Test header priority when all three headers are present."""
    # Create a mock request with all three headers
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {
        'Authorization': 'Bearer auth_api_key',
        'X-Session-API-Key': 'session_api_key',
        'X-Access-Token': 'access_token_key',
    }

    # Call the function
    api_key = get_api_key_from_header(mock_request)

    # Assert that the API key from Authorization header was used (highest priority)
    assert api_key == 'auth_api_key'


def test_get_api_key_from_header_invalid_authorization_fallback_to_x_access_token():
    """Test that invalid Authorization header falls back to X-Access-Token."""
    # Create a mock request with invalid Authorization header and X-Access-Token
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {
        'Authorization': 'InvalidFormat api_key',
        'X-Access-Token': 'access_token_key',
    }

    # Call the function
    api_key = get_api_key_from_header(mock_request)

    # Assert that the API key from X-Access-Token header was used
    assert api_key == 'access_token_key'


def test_get_api_key_from_header_empty_headers():
    """Test that empty header values are handled correctly."""
    # Create a mock request with empty header values
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {
        'Authorization': '',
        'X-Session-API-Key': '',
        'X-Access-Token': 'access_token_key',
    }

    # Call the function
    api_key = get_api_key_from_header(mock_request)

    # Assert that the API key from X-Access-Token header was used
    assert api_key == 'access_token_key'


def test_get_api_key_from_header_bearer_with_empty_token():
    """Test that Bearer header with empty token falls back to other headers."""
    # Create a mock request with Bearer header with empty token
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {
        'Authorization': 'Bearer ',
        'X-Access-Token': 'access_token_key',
    }

    # Call the function
    api_key = get_api_key_from_header(mock_request)

    # Assert that empty string from Bearer is returned (current behavior)
    # This tests the current implementation behavior
    assert api_key == ''


def test_get_api_key_from_header_with_api_key_cookie():
    """Test that get_api_key_from_header extracts API key from the api_key cookie."""
    # Create a mock request with the api_key cookie set
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.cookies = {'api_key': 'cookie_api_key'}

    # Call the function
    api_key = get_api_key_from_header(mock_request)

    # Assert that the API key from the cookie was correctly extracted
    assert api_key == 'cookie_api_key'


def test_get_api_key_from_header_priority_authorization_over_api_key_cookie():
    """Test that the Authorization header takes priority over the api_key cookie."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {'Authorization': 'Bearer auth_api_key'}
    mock_request.cookies = {'api_key': 'cookie_api_key'}

    api_key = get_api_key_from_header(mock_request)

    # The Authorization header value should win.
    assert api_key == 'auth_api_key'


def test_get_api_key_from_header_priority_x_session_over_api_key_cookie():
    """Test that the X-Session-API-Key header takes priority over the api_key cookie."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {'X-Session-API-Key': 'session_api_key'}
    mock_request.cookies = {'api_key': 'cookie_api_key'}

    api_key = get_api_key_from_header(mock_request)

    # The X-Session-API-Key header value should win.
    assert api_key == 'session_api_key'


def test_get_api_key_from_header_priority_x_access_token_over_api_key_cookie():
    """Test that the X-Access-Token header takes priority over the api_key cookie."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {'X-Access-Token': 'access_token_key'}
    mock_request.cookies = {'api_key': 'cookie_api_key'}

    api_key = get_api_key_from_header(mock_request)

    # The X-Access-Token header value should win over the cookie.
    assert api_key == 'access_token_key'


def test_get_api_key_from_header_with_empty_api_key_cookie():
    """An empty api_key cookie value should be treated as absent and fall through."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.cookies = {'api_key': ''}

    api_key = get_api_key_from_header(mock_request)

    # Empty cookie value yields an empty string (mirrors the empty-header
    # behaviour). Callers like `saas_user_auth_from_bearer` treat falsy
    # values as missing credentials.
    assert api_key == ''


def test_get_api_key_from_header_with_unrelated_cookies():
    """Unrelated cookies must not be picked up as an API key."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.cookies = {'session': 'abc123', 'preferences': 'dark'}

    api_key = get_api_key_from_header(mock_request)

    assert api_key is None


@pytest.mark.asyncio
async def test_saas_user_auth_from_bearer_via_api_key_cookie():
    """A valid api_key cookie should authenticate the same as an X-Access-Token header."""
    mock_request = MagicMock(cookies={}, headers={})
    mock_request.headers = {}
    mock_request.cookies = {'api_key': 'cookie_api_key'}

    mock_org_id = uuid.uuid4()
    mock_validation_result = ApiKeyValidationResult(
        user_id='11111111-1111-4111-8111-111111111111',
        org_id=mock_org_id,
        key_id=7,
        key_name='Cookie Key',
    )

    with patch('server.auth.authentication.ApiKeyStore') as mock_api_key_store_cls:
        mock_api_key_store = MagicMock()
        mock_api_key_store.validate_api_key = AsyncMock(
            return_value=mock_validation_result
        )
        mock_api_key_store_cls.get_instance.return_value = mock_api_key_store

        result = await saas_user_auth_from_bearer(mock_request)

        assert isinstance(result, SaasUserAuth)
        assert result.user_id == '11111111-1111-4111-8111-111111111111'
        assert result.api_key_org_id == mock_org_id
        assert result.api_key_id == 7
        assert result.api_key_name == 'Cookie Key'
        assert result.auth_type == AuthType.COOKIE
        mock_api_key_store.validate_api_key.assert_called_once_with('cookie_api_key')


@pytest.mark.asyncio
async def test_saas_user_auth_from_bearer_via_api_key_cookie_invalid():
    """An api_key cookie with an invalid key should produce no auth, same as a bad header."""
    mock_request = MagicMock(cookies={}, headers={})
    mock_request.headers = {}
    mock_request.cookies = {'api_key': 'invalid_cookie_key'}

    with patch('server.auth.authentication.ApiKeyStore') as mock_api_key_store_cls:
        mock_api_key_store = MagicMock()
        mock_api_key_store.validate_api_key = AsyncMock(return_value=None)
        mock_api_key_store_cls.get_instance.return_value = mock_api_key_store

        result = await saas_user_auth_from_bearer(mock_request)

        assert result is None
        mock_api_key_store.validate_api_key.assert_called_once_with(
            'invalid_cookie_key'
        )


@pytest.mark.asyncio
async def test_saas_user_auth_from_signed_token_rejects_disabled_user(mock_config):
    user_id = str(uuid.uuid4())
    access_payload = {
        'sub': user_id,
        'exp': int(time.time()) + 3600,
        'email': 'user@example.com',
        'email_verified': True,
    }
    access_token = jwt.encode(access_payload, 'access_secret', algorithm='HS256')
    signed_token = jwt.encode(
        {'access_token': access_token, 'refresh_token': 'test_refresh_token'},
        'test_secret',
        algorithm='HS256',
    )

    with patch(
        'server.auth.saas_user_auth.UserStore.get_user_by_id',
        AsyncMock(return_value=MagicMock(is_disabled=True)),
    ):
        with pytest.raises(AuthError, match='Account is unavailable'):
            await saas_user_auth_from_signed_token(signed_token)


@pytest.mark.asyncio
async def test_saas_user_auth_from_signed_token_rejects_deleted_user(mock_config):
    """A signed token whose user has been deleted must not authenticate."""
    user_id = str(uuid.uuid4())
    access_payload = {
        'sub': user_id,
        'exp': int(time.time()) + 3600,
        'email': 'user@example.com',
        'email_verified': True,
    }
    access_token = jwt.encode(access_payload, 'access_secret', algorithm='HS256')
    signed_token = jwt.encode(
        {'access_token': access_token, 'refresh_token': 'test_refresh_token'},
        'test_secret',
        algorithm='HS256',
    )

    with patch(
        'server.auth.saas_user_auth.UserStore.get_user_by_id',
        AsyncMock(return_value=None),
    ):
        with pytest.raises(AuthError, match='Account is unavailable'):
            await saas_user_auth_from_signed_token(signed_token)


@pytest.mark.asyncio
async def test_saas_user_auth_from_signed_token_blocked_domain(mock_config):
    """Test that saas_user_auth_from_signed_token raises AuthError when email domain is blocked."""
    # Arrange
    access_payload = {
        'sub': '11111111-1111-4111-8111-111111111111',
        'exp': int(time.time()) + 3600,
        'email': 'user@colsch.us',
        'email_verified': True,
    }
    access_token = jwt.encode(access_payload, 'access_secret', algorithm='HS256')

    token_payload = {
        'access_token': access_token,
        'refresh_token': 'test_refresh_token',
    }
    signed_token = jwt.encode(token_payload, 'test_secret', algorithm='HS256')

    with patch(
        'server.auth.user.default_user_authorizer.UserAuthorizationStore'
    ) as mock_user_auth_store:
        mock_user_auth_store.get_authorization_type = AsyncMock(
            return_value=UserAuthorizationType.BLACKLIST
        )

        # Act & Assert
        with pytest.raises(AuthError) as exc_info:
            await saas_user_auth_from_signed_token(signed_token)

        assert 'blocked' in str(exc_info.value)
        mock_user_auth_store.get_authorization_type.assert_called_once_with(
            'user@colsch.us', None
        )


@pytest.mark.asyncio
async def test_saas_user_auth_from_signed_token_allowed_domain(mock_config):
    """Test that saas_user_auth_from_signed_token succeeds when email domain is not blocked."""
    # Arrange
    access_payload = {
        'sub': '11111111-1111-4111-8111-111111111111',
        'exp': int(time.time()) + 3600,
        'email': 'user@example.com',
        'email_verified': True,
    }
    access_token = jwt.encode(access_payload, 'access_secret', algorithm='HS256')

    token_payload = {
        'access_token': access_token,
        'refresh_token': 'test_refresh_token',
    }
    signed_token = jwt.encode(token_payload, 'test_secret', algorithm='HS256')

    with patch(
        'server.auth.user.default_user_authorizer.UserAuthorizationStore'
    ) as mock_user_auth_store:
        mock_user_auth_store.get_authorization_type = AsyncMock(return_value=None)

        # Act
        result = await saas_user_auth_from_signed_token(signed_token)

        # Assert
        assert isinstance(result, SaasUserAuth)
        assert result.user_id == '11111111-1111-4111-8111-111111111111'
        assert result.email == 'user@example.com'
        mock_user_auth_store.get_authorization_type.assert_called_once_with(
            'user@example.com', None
        )


@pytest.mark.asyncio
async def test_saas_user_auth_from_signed_token_domain_blocking_inactive(mock_config):
    """Test that saas_user_auth_from_signed_token succeeds when email domain is not blocked."""
    # Arrange
    access_payload = {
        'sub': '11111111-1111-4111-8111-111111111111',
        'exp': int(time.time()) + 3600,
        'email': 'user@colsch.us',
        'email_verified': True,
    }
    access_token = jwt.encode(access_payload, 'access_secret', algorithm='HS256')

    token_payload = {
        'access_token': access_token,
        'refresh_token': 'test_refresh_token',
    }
    signed_token = jwt.encode(token_payload, 'test_secret', algorithm='HS256')

    with patch(
        'server.auth.user.default_user_authorizer.UserAuthorizationStore'
    ) as mock_user_auth_store:
        mock_user_auth_store.get_authorization_type = AsyncMock(return_value=None)

        # Act
        result = await saas_user_auth_from_signed_token(signed_token)

        # Assert
        assert isinstance(result, SaasUserAuth)
        assert result.user_id == '11111111-1111-4111-8111-111111111111'
        mock_user_auth_store.get_authorization_type.assert_called_once_with(
            'user@colsch.us', None
        )


# =============================================================================
# Tests for OPENHANDS_API_KEY injection
# =============================================================================


class TestOpenHandsApiKey:
    """Tests for OPENHANDS_API_KEY system secret generation and injection."""

    @pytest.mark.asyncio
    async def test_get_openhands_api_key_creates_system_key(self):
        """Test that _get_openhands_api_key creates a system key via ApiKeyStore."""
        user_id = '11111111-1111-4111-8111-111111111111'
        org_id = uuid.uuid4()
        expected_api_key = 'sk-oh-test-key-12345'

        # Create mock user
        mock_user = MagicMock()
        mock_user.current_org_id = org_id

        user_auth = SaasUserAuth(
            user_id=user_id,
            refresh_token=SecretStr('refresh_token'),
        )

        with (
            patch('server.auth.saas_user_auth.UserStore') as mock_user_store,
            patch('server.auth.saas_user_auth.ApiKeyStore') as mock_api_key_store_cls,
        ):
            mock_user_store.get_user_by_id = AsyncMock(return_value=mock_user)

            mock_api_key_store = MagicMock()
            mock_api_key_store.get_or_create_system_api_key = AsyncMock(
                return_value=expected_api_key
            )
            mock_api_key_store_cls.get_instance.return_value = mock_api_key_store

            # Act
            result = await user_auth._get_openhands_api_key()

            # Assert
            assert result == expected_api_key
            mock_user_store.get_user_by_id.assert_called_once_with(user_id)
            mock_api_key_store.get_or_create_system_api_key.assert_called_once_with(
                user_id=user_id,
                org_id=org_id,
                name='OPENHANDS_API_KEY',
            )

    @pytest.mark.asyncio
    async def test_get_openhands_api_key_raises_for_missing_user(self):
        """Test that _get_openhands_api_key raises ValueError if user not found.

        The error message now collapses ``user not found`` and
        ``user without org`` into a single ``has no current organization``
        case, since both ultimately mean we cannot resolve an effective
        org for the request.
        """
        user_id = 'nonexistent_user'

        user_auth = SaasUserAuth(
            user_id=user_id,
            refresh_token=SecretStr('refresh_token'),
        )

        with patch('server.auth.saas_user_auth.UserStore') as mock_user_store:
            mock_user_store.get_user_by_id = AsyncMock(return_value=None)

            # Act & Assert
            with pytest.raises(
                ValueError, match=f'User {user_id} has no current organization'
            ):
                await user_auth._get_openhands_api_key()

    @pytest.mark.asyncio
    async def test_get_openhands_api_key_raises_for_user_without_org(self):
        """Test that _get_openhands_api_key raises ValueError if user has no org."""
        user_id = '11111111-1111-4111-8111-111111111111'

        # Create mock user with no current organization
        mock_user = MagicMock()
        mock_user.current_org_id = None

        user_auth = SaasUserAuth(
            user_id=user_id,
            refresh_token=SecretStr('refresh_token'),
        )

        with patch('server.auth.saas_user_auth.UserStore') as mock_user_store:
            mock_user_store.get_user_by_id = AsyncMock(return_value=mock_user)

            # Act & Assert
            with pytest.raises(ValueError, match='has no current organization'):
                await user_auth._get_openhands_api_key()

    @pytest.mark.asyncio
    async def test_get_secrets_includes_openhands_api_key(self):
        """Test that get_secrets injects OPENHANDS_API_KEY into custom_secrets."""
        user_id = '11111111-1111-4111-8111-111111111111'
        org_id = uuid.uuid4()
        expected_api_key = 'sk-oh-test-key-12345'

        # Create mock user
        mock_user = MagicMock()
        mock_user.current_org_id = org_id

        # Create mock secrets from store (without OPENHANDS_API_KEY)
        mock_stored_secrets = Secrets(
            custom_secrets={
                'MY_SECRET': {
                    'secret': 'my-secret-value',
                    'description': 'My custom secret',
                }
            }
        )

        user_auth = SaasUserAuth(
            user_id=user_id,
            refresh_token=SecretStr('refresh_token'),
        )

        with (
            patch('server.auth.saas_user_auth.UserStore') as mock_user_store,
            patch('server.auth.saas_user_auth.ApiKeyStore') as mock_api_key_store_cls,
            patch(
                'server.auth.saas_user_auth.SaasSecretsStore'
            ) as mock_secrets_store_cls,
        ):
            mock_user_store.get_user_by_id = AsyncMock(return_value=mock_user)

            mock_api_key_store = MagicMock()
            mock_api_key_store.get_or_create_system_api_key = AsyncMock(
                return_value=expected_api_key
            )
            mock_api_key_store_cls.get_instance.return_value = mock_api_key_store

            mock_secrets_store = MagicMock()
            mock_secrets_store.load = AsyncMock(return_value=mock_stored_secrets)
            mock_secrets_store_cls.get_instance = AsyncMock(
                return_value=mock_secrets_store
            )

            # Act
            result = await user_auth.get_secrets()

            # Assert
            assert result is not None
            assert 'OPENHANDS_API_KEY' in result.custom_secrets
            assert (
                result.custom_secrets['OPENHANDS_API_KEY'].secret.get_secret_value()
                == expected_api_key
            )
            assert (
                'system-managed'
                in result.custom_secrets['OPENHANDS_API_KEY'].description
            )
            # Original secret should still be present
            assert 'MY_SECRET' in result.custom_secrets

    @pytest.mark.asyncio
    async def test_get_secrets_caches_result(self):
        """Test that get_secrets caches the result and doesn't call store again."""
        user_id = '11111111-1111-4111-8111-111111111111'
        org_id = uuid.uuid4()
        expected_api_key = 'sk-oh-test-key-12345'

        mock_user = MagicMock()
        mock_user.current_org_id = org_id

        mock_stored_secrets = Secrets()

        user_auth = SaasUserAuth(
            user_id=user_id,
            refresh_token=SecretStr('refresh_token'),
        )

        with (
            patch('server.auth.saas_user_auth.UserStore') as mock_user_store,
            patch('server.auth.saas_user_auth.ApiKeyStore') as mock_api_key_store_cls,
            patch(
                'server.auth.saas_user_auth.SaasSecretsStore'
            ) as mock_secrets_store_cls,
        ):
            mock_user_store.get_user_by_id = AsyncMock(return_value=mock_user)

            mock_api_key_store = MagicMock()
            mock_api_key_store.get_or_create_system_api_key = AsyncMock(
                return_value=expected_api_key
            )
            mock_api_key_store_cls.get_instance.return_value = mock_api_key_store

            mock_secrets_store = MagicMock()
            mock_secrets_store.load = AsyncMock(return_value=mock_stored_secrets)
            mock_secrets_store_cls.get_instance = AsyncMock(
                return_value=mock_secrets_store
            )

            # Act - call get_secrets twice
            result1 = await user_auth.get_secrets()
            result2 = await user_auth.get_secrets()

            # Assert - store.load should only be called once (caching)
            assert result1 is result2
            mock_secrets_store.load.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_secrets_handles_empty_stored_secrets(self):
        """Test that get_secrets works when store returns empty Secrets."""
        user_id = '11111111-1111-4111-8111-111111111111'
        org_id = uuid.uuid4()
        expected_api_key = 'sk-oh-test-key-12345'

        mock_user = MagicMock()
        mock_user.current_org_id = org_id

        # Empty secrets from store
        mock_stored_secrets = Secrets()

        user_auth = SaasUserAuth(
            user_id=user_id,
            refresh_token=SecretStr('refresh_token'),
        )

        with (
            patch('server.auth.saas_user_auth.UserStore') as mock_user_store,
            patch('server.auth.saas_user_auth.ApiKeyStore') as mock_api_key_store_cls,
            patch(
                'server.auth.saas_user_auth.SaasSecretsStore'
            ) as mock_secrets_store_cls,
        ):
            mock_user_store.get_user_by_id = AsyncMock(return_value=mock_user)

            mock_api_key_store = MagicMock()
            mock_api_key_store.get_or_create_system_api_key = AsyncMock(
                return_value=expected_api_key
            )
            mock_api_key_store_cls.get_instance.return_value = mock_api_key_store

            mock_secrets_store = MagicMock()
            mock_secrets_store.load = AsyncMock(return_value=mock_stored_secrets)
            mock_secrets_store_cls.get_instance = AsyncMock(
                return_value=mock_secrets_store
            )

            # Act
            result = await user_auth.get_secrets()

            # Assert - should have only OPENHANDS_API_KEY
            assert result is not None
            assert 'OPENHANDS_API_KEY' in result.custom_secrets
            assert len(result.custom_secrets) == 1


@pytest.fixture(autouse=True)
def initialized_keycloak_mode(monkeypatch):
    from server.auth import mode

    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)


@pytest.fixture(autouse=True)
def current_authentication_accounts(monkeypatch, request):
    from server.auth.user.default_user_authorizer import UserAuthorizationStore
    from storage.user import User

    email = 'test@example.com'
    if (
        'blocked_domain' in request.node.name
        or 'domain_blocking_inactive' in request.node.name
    ):
        email = 'user@colsch.us'
    elif 'allowed_domain' in request.node.name:
        email = 'user@example.com'

    async def current_user(user_id):
        return User(
            id=uuid.UUID(str(user_id)),
            email=email,
            email_verified=True,
            is_disabled=False,
            current_org_id=uuid.UUID(str(user_id)),
        )

    monkeypatch.setattr('storage.user_store.UserStore.get_user_by_id', current_user)
    monkeypatch.setattr(
        UserAuthorizationStore, 'get_authorization_type', AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        'server.auth.user_management.EnterpriseUserManagementService.ensure_authenticated_account',
        AsyncMock(return_value=None),
    )
