from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from pydantic import SecretStr

from openhands.app_server.integrations.provider import PROVIDER_TOKEN_TYPE
from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.secrets.secrets_store import SecretsStore
from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.settings.settings_store import SettingsStore
from openhands.app_server.user_auth.user_auth import AuthType, get_user_auth


async def get_provider_tokens(request: Request) -> PROVIDER_TOKEN_TYPE | None:
    user_auth = await get_user_auth(request)
    provider_tokens = await user_auth.get_provider_tokens()
    return provider_tokens


async def get_access_token(request: Request) -> SecretStr | None:
    user_auth = await get_user_auth(request)
    access_token = await user_auth.get_access_token()
    return access_token


async def get_user_id(
    request: Request,
    api_key_header: str | None = Depends(
        APIKeyHeader(name='X-Access-Token', auto_error=False)
    ),
) -> str | None:
    """Get the current user_id. Used for dependency injection - the
    api key header is used here to signal the requirement in OpenAPI
    docs"""
    user_auth = await get_user_auth(request)
    user_id = await user_auth.get_user_id()
    return user_id


async def get_user_settings(request: Request) -> Settings | None:
    user_auth = await get_user_auth(request)
    user_settings = await user_auth.get_user_settings()
    return user_settings


async def get_secrets_store(request: Request) -> SecretsStore:
    user_auth = await get_user_auth(request)
    secrets_store = await user_auth.get_secrets_store()
    return secrets_store


async def get_secrets(request: Request) -> Secrets | None:
    user_auth = await get_user_auth(request)
    user_secrets = await user_auth.get_secrets()
    return user_secrets


async def get_user_settings_store(request: Request) -> SettingsStore | None:
    user_auth = await get_user_auth(request)
    user_settings_store = await user_auth.get_user_settings_store()
    return user_settings_store


async def get_auth_type(request: Request) -> AuthType | None:
    user_auth = await get_user_auth(request)
    return user_auth.get_auth_type()


async def reject_service_account(request: Request) -> None:
    """Route dependency: answer 403 when the caller is a service account.

    Put it on routes that change the caller's own account (API keys,
    personal secrets, settings, git links). A service account's run key must
    not be able to create keys on request or rewrite its own configuration.

    Starting a run is allowed, and it still creates the account's
    ``OPENHANDS_API_KEY`` and ``MCP_API_KEY`` for the sandbox. These keys do
    not expire and the account cannot delete them. Disabling or deleting the
    service account revokes them, as it does every key of the account.
    """
    user_auth = await get_user_auth(request)
    if user_auth.is_service_account():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Service accounts cannot use this endpoint',
        )
