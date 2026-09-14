"""Native runtime integration against migrated PostgreSQL and real credentials."""

import json
import os
from collections.abc import AsyncIterator
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request
from sqlalchemy import update

from openhands.app_server.user.auth_user_context import AuthUserContext
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.user.user_models import UserInfo
from openhands.app_server.user_auth import user_auth as user_auth_module
from server.auth import (
    account_lookup,
    auth_config,
    composition,
    keycloak_manager,
    openhands_request_auth,
    saas_user_auth,
)
from server.auth.bootstrap import initialize_auth_installation
from server.auth.native_types import SessionFactory
from server.auth.saas_user_auth import SaasUserAuth
from server.routes import auth
from server.services import native_auth_service
from storage import api_key_store, role_store, user_store
from storage.api_key_store import ApiKeyStore
from storage.native_auth import AuthAccount
from storage.role import Role
from storage.user import User
from tests.unit.server.auth.native_test_types import (
    NativeRuntime,
    present,
)

ORIGIN = 'https://native.example.com'
FOREIGN_ORIGIN = 'https://untrusted.example.com'
PASSWORD = 'the telescope finds violet moons'


@pytest.fixture
async def native_runtime(
    monkeypatch: pytest.MonkeyPatch, async_session_maker: SessionFactory
) -> AsyncIterator[NativeRuntime]:
    for module in (
        auth_config,
        keycloak_manager,
    ):
        monkeypatch.setattr(module, 'ENABLE_KEYCLOAK', False)
    composition.get_auth_services.cache_clear()
    monkeypatch.setattr(auth_config, 'AUTH_MODE', 'native')
    monkeypatch.setenv('OH_WEB_URL', ORIGIN)
    monkeypatch.setenv('SUPERADMIN_EMAIL', 'native@example.com')
    monkeypatch.setenv('SUPERADMIN_PASSWORD', PASSWORD)
    monkeypatch.delenv('PERMITTED_CORS_ORIGINS', raising=False)
    for name in list(os.environ):
        if name.startswith('OH_PERMITTED_CORS_ORIGINS_'):
            monkeypatch.delenv(name)
    auth_config.get_native_auth_settings.cache_clear()
    monkeypatch.setattr(saas_user_auth, 'rate_limiter', None)
    monkeypatch.setattr(
        user_auth_module.server_config,
        'user_auth_class',
        'server.auth.saas_user_auth.SaasUserAuth',
    )
    for module in (user_store, api_key_store, role_store, auth, account_lookup):
        monkeypatch.setattr(module, 'a_session_maker', async_session_maker)
    service = native_auth_service.NativeAuthService(async_session_maker)
    monkeypatch.setattr(native_auth_service, 'get_native_auth_service', lambda: service)
    monkeypatch.setattr(
        openhands_request_auth, 'get_native_auth_service', lambda: service
    )
    # The migrated template intentionally clears seeded role/config rows.
    async with async_session_maker() as session, session.begin():
        session.add_all(
            [
                Role(name='owner', rank=0),
                Role(name='admin', rank=1),
                Role(name='member', rank=2),
            ]
        )

    with (
        patch.object(
            keycloak_manager,
            'KeycloakOpenID',
            side_effect=AssertionError('Keycloak constructor called'),
        ),
        patch.object(
            keycloak_manager,
            'KeycloakAdmin',
            side_effect=AssertionError('Keycloak constructor called'),
        ),
    ):
        await initialize_auth_installation(session_factory=async_session_maker)
        from sqlalchemy import select

        from storage.native_auth import PasswordCredential

        async with async_session_maker() as session, session.begin():
            account = present(await session.scalar(select(AuthAccount)))
            user = present(await session.get(User, account.id))
            credential = await session.get(PasswordCredential, account.id)
            login = await service._new_session(session, account, credential, user)
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(User)
                .where(User.id == login.principal.account_id)
                .values(accepted_tos=datetime.now())
            )
        api_key = await ApiKeyStore().create_api_key(str(login.principal.account_id))
        yield service, login, api_key
    auth_config.get_native_auth_settings.cache_clear()
    composition.get_auth_services.cache_clear()


@pytest.mark.asyncio
async def test_native_unverified_email_and_global_permissions(
    native_runtime: NativeRuntime,
) -> None:
    _, login, api_key = native_runtime
    from server.routes.users_v1 import get_current_user_saas

    request = Request(
        {
            'type': 'http',
            'headers': [(b'authorization', f'Bearer {api_key}'.encode())],
        }
    )
    user_auth = await SaasUserAuth.get_instance(request)
    assert user_auth.email_verified is False
    assert await user_auth.get_access_token() is None
    user_auth._org_info_loaded = True
    context = AuthUserContext(
        user_auth=user_auth, _user_info=UserInfo(id=str(login.principal.account_id))
    )
    response = await get_current_user_saas(context, expose_secrets=False)
    body = json.loads(response.body)
    assert 'manage_users' in body['global_permissions']
    assert body['permissions'] is None


async def test_global_permissions_use_the_user_context_identity_contract(
    native_runtime: NativeRuntime,
) -> None:
    from server.routes.users_v1 import get_current_user_saas

    _, login, _ = native_runtime
    context = AsyncMock(spec=UserContext)
    context.get_user_id.return_value = str(login.principal.account_id)
    context.get_user_info.return_value = UserInfo(id=str(login.principal.account_id))
    response = await get_current_user_saas(context, expose_secrets=False)
    assert 'manage_users' in json.loads(response.body)['global_permissions']
