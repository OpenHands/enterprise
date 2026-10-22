"""Route-level tests for the ``auth_providers`` admin CRUD API.

These exercise the FastAPI routes through ``require_permission`` and stub
``AuthProviderStore`` at the route module boundary, mirroring
``test_super_admins.py`` and ``test_feature_flags.py``: authorization is
faked by short-circuiting the org-role lookup to ``None`` and stacking a
``superadmin`` super role on top of the conftest-level
``get_user_super_role -> None`` default.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.user_auth import get_user_id
from server.routes.oauth_providers_admin import oauth_provider_admin_router
from server.services.saml_metadata import SamlIdpMetadata, SamlMetadataFetchError
from storage.auth_provider import DEFAULT_AUTH_PROVIDER_COLOR, AuthProvider
from storage.auth_provider_details import (
    OAuthProviderDetails,
    PasswordProviderDetails,
    SamlProviderDetails,
    generate_sp_keypair,
)

CALLER_USER_ID = str(uuid.uuid4())


@pytest.fixture
def mock_app():
    app = FastAPI()
    app.include_router(oauth_provider_admin_router)
    app.dependency_overrides[get_user_id] = lambda: CALLER_USER_ID
    return app


@pytest.fixture
def grant_manage_oauth_providers():
    """Make ``MANAGE_OAUTH_PROVIDERS`` succeed by faking a ``superadmin`` role."""
    superadmin = MagicMock()
    superadmin.name = 'admin'
    with (
        patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=None),
        ),
        patch(
            'server.auth.authorization.get_user_super_role',
            AsyncMock(return_value=superadmin),
        ),
    ):
        yield


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _fake_provider(
    provider_id: int = 1,
    category: str = 'github',
    title: str = 'GitHub',
    is_idp: bool = False,
    client_id: str = 'cid',
    client_secret: str | None = None,
    icon: str | None = None,
    color: str = DEFAULT_AUTH_PROVIDER_COLOR,
    allow_registration: bool = True,
    allow_match_by_email: bool = True,
    legacy_keycloak_type: ProviderType | None = None,
) -> AuthProvider:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    provider = AuthProvider(
        title=title,
        is_idp=is_idp,
        icon=icon,
        color=color,
        allow_registration=allow_registration,
        allow_match_by_email=allow_match_by_email,
        details=OAuthProviderDetails(
            provider_type=ProviderType(category),
            client_id=client_id,
            client_secret=client_secret,
            legacy_keycloak_type=legacy_keycloak_type,
        ),
    )
    provider.id = provider_id
    provider.created_at = now
    provider.updated_at = now
    return provider


@pytest.mark.asyncio
async def test_list_providers(mock_app, grant_manage_oauth_providers):
    providers = [
        _fake_provider(1, 'github'),
        _fake_provider(2, 'gitlab', title='GitLab'),
    ]
    with patch(
        'server.routes.oauth_providers_admin.AuthProviderStore.list_all',
        AsyncMock(return_value=providers),
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/oauth-providers')

    assert resp.status_code == 200
    body = resp.json()
    assert {p['details']['provider_type'] for p in body} == {'github', 'gitlab'}
    # client_secret itself is never serialized in the clear.
    assert all(p['details']['client_secret'] is None for p in body)


@pytest.mark.asyncio
async def test_list_providers_forbidden_without_permission(mock_app):
    with patch(
        'server.auth.authorization.get_user_org_role',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/oauth-providers')

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_provider_found(mock_app, grant_manage_oauth_providers):
    provider = _fake_provider(client_secret='shh')
    with patch(
        'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
        AsyncMock(return_value=provider),
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/oauth-providers/1')

    assert resp.status_code == 200
    body = resp.json()
    assert body['id'] == 1
    # The real secret value is never serialized, but its presence/absence is
    # still distinguishable via pydantic's masked SecretStr placeholder.
    assert body['details']['client_secret'] not in (None, 'shh')


@pytest.mark.asyncio
async def test_get_provider_missing_404(mock_app, grant_manage_oauth_providers):
    with patch(
        'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/oauth-providers/999')

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_provider(mock_app, grant_manage_oauth_providers):
    created = _fake_provider(client_secret='super-secret')
    with patch(
        'server.routes.oauth_providers_admin.AuthProviderStore.create',
        AsyncMock(return_value=created),
    ) as create_mock:
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers',
                json={
                    'title': 'GitHub',
                    'details': {
                        'kind': 'OAuthProviderDetails',
                        'provider_type': 'github',
                        'client_id': 'cid',
                        'client_secret': 'super-secret',
                    },
                },
            )

    assert resp.status_code == 201
    body = resp.json()
    assert body['details']['provider_type'] == 'github'
    assert body['details']['client_secret'] not in (None, 'super-secret')

    (persisted,), _ = create_mock.call_args
    assert isinstance(persisted, AuthProvider)
    assert isinstance(persisted.details, OAuthProviderDetails)
    assert persisted.details.client_secret.get_secret_value() == 'super-secret'
    assert persisted.details.provider_type == ProviderType.GITHUB
    assert persisted.details.client_id == 'cid'
    assert persisted.kind == OAuthProviderDetails.__name__


@pytest.mark.asyncio
async def test_create_provider_without_secret(mock_app, grant_manage_oauth_providers):
    created = _fake_provider(category='enterprise_sso', is_idp=True)
    with patch(
        'server.routes.oauth_providers_admin.AuthProviderStore.create',
        AsyncMock(return_value=created),
    ) as create_mock:
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers',
                json={
                    'title': 'Corp SSO',
                    'is_idp': True,
                    'details': {
                        'kind': 'OAuthProviderDetails',
                        'provider_type': 'enterprise_sso',
                        'client_id': 'cid',
                    },
                },
            )

    assert resp.status_code == 201
    assert resp.json()['details']['client_secret'] is None
    (persisted,), _ = create_mock.call_args
    assert persisted.details.client_secret is None
    assert persisted.is_idp is True


@pytest.mark.asyncio
async def test_create_integrated_idp_provider(mock_app, grant_manage_oauth_providers):
    """``PasswordProviderDetails`` takes no fields beyond the discriminator."""
    created = AuthProvider(
        title='Integrated IDP',
        is_idp=True,
        color=DEFAULT_AUTH_PROVIDER_COLOR,
        allow_registration=True,
        allow_match_by_email=True,
        details=PasswordProviderDetails(),
    )
    created.id = 3
    created.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    created.updated_at = created.created_at
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_first_by_kind',
            AsyncMock(return_value=None),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.create',
            AsyncMock(return_value=created),
        ) as create_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers',
                json={
                    'title': 'Integrated IDP',
                    'is_idp': True,
                    'details': {'kind': 'PasswordProviderDetails'},
                },
            )

    assert resp.status_code == 201
    assert resp.json()['kind'] == PasswordProviderDetails.__name__
    (persisted,), _ = create_mock.call_args
    assert isinstance(persisted.details, PasswordProviderDetails)


@pytest.mark.asyncio
async def test_create_provider_forbidden_without_permission(mock_app):
    with patch(
        'server.auth.authorization.get_user_org_role',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers',
                json={
                    'title': 'GitHub',
                    'details': {
                        'kind': 'OAuthProviderDetails',
                        'provider_type': 'github',
                        'client_id': 'cid',
                    },
                },
            )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_provider_partial(mock_app, grant_manage_oauth_providers):
    updated = _fake_provider(title='New Name')
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
            AsyncMock(return_value=_fake_provider(1, 'github')),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.update',
            AsyncMock(return_value=updated),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={'title': 'New Name'},
            )

    assert resp.status_code == 200
    assert resp.json()['title'] == 'New Name'
    # Only the explicitly-set field is forwarded to the store.
    update_mock.assert_awaited_once_with(1, title='New Name')


@pytest.mark.asyncio
async def test_update_provider_sets_new_secret(mock_app, grant_manage_oauth_providers):
    updated = _fake_provider(client_secret='new-secret')
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
            AsyncMock(return_value=_fake_provider(1, 'github')),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.update',
            AsyncMock(return_value=updated),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={
                    'details': {
                        'kind': 'OAuthProviderDetails',
                        'provider_type': 'github',
                        'client_id': 'cid',
                        'client_secret': 'new-secret',
                    }
                },
            )

    assert resp.status_code == 200
    assert resp.json()['details']['client_secret'] not in (None, 'new-secret')
    update_mock.assert_awaited_once()
    args, kwargs = update_mock.call_args
    assert args == (1,)
    assert isinstance(kwargs['details'], OAuthProviderDetails)
    assert kwargs['details'].client_secret.get_secret_value() == 'new-secret'


@pytest.mark.asyncio
async def test_update_provider_clears_secret_with_empty_string(
    mock_app, grant_manage_oauth_providers
):
    updated = _fake_provider(client_secret=None)
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
            AsyncMock(return_value=_fake_provider(1, 'github')),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.update',
            AsyncMock(return_value=updated),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={
                    'details': {
                        'kind': 'OAuthProviderDetails',
                        'provider_type': 'github',
                        'client_id': 'cid',
                        'client_secret': '',
                    }
                },
            )

    assert resp.status_code == 200
    assert resp.json()['details']['client_secret'] is None
    update_mock.assert_awaited_once()
    _, kwargs = update_mock.call_args
    assert kwargs['details'].client_secret is None


@pytest.mark.asyncio
async def test_update_provider_omitted_details_untouched(
    mock_app, grant_manage_oauth_providers
):
    updated = _fake_provider(title='Renamed')
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
            AsyncMock(return_value=_fake_provider(1, 'github')),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.update',
            AsyncMock(return_value=updated),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={'title': 'Renamed'},
            )

    assert resp.status_code == 200
    # `details` was not part of the request body, so it is not forwarded to
    # the store at all -- the existing configuration (including secrets) is
    # left untouched.
    update_mock.assert_awaited_once_with(1, title='Renamed')
    assert 'details' not in update_mock.call_args.kwargs


async def _patch_oauth_details(app, details: dict) -> OAuthProviderDetails:
    """PATCH ``details`` onto a row whose ``legacy_keycloak_type`` is github
    and return the ``details`` forwarded to the store."""
    existing = _fake_provider(
        1,
        'enterprise_sso',
        is_idp=True,
        client_secret='old-secret',
        legacy_keycloak_type=ProviderType.GITHUB,
    )
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
            AsyncMock(return_value=existing),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.update',
            AsyncMock(return_value=existing),
        ) as update_mock,
    ):
        async with _client(app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={
                    'details': {
                        'kind': 'OAuthProviderDetails',
                        'provider_type': 'enterprise_sso',
                        'client_id': 'cid',
                        **details,
                    }
                },
            )
    assert resp.status_code == 200
    return update_mock.call_args.kwargs['details']


@pytest.mark.asyncio
async def test_update_provider_keeps_unsent_legacy_keycloak_type(
    mock_app,
    grant_manage_oauth_providers,
):
    details = await _patch_oauth_details(mock_app, {'scopes': ['openid']})

    assert details.legacy_keycloak_type == ProviderType.GITHUB
    assert details.scopes == ['openid']
    assert details.client_secret.get_secret_value() == 'old-secret'


@pytest.mark.asyncio
async def test_update_provider_explicit_null_clears_legacy_keycloak_type(
    mock_app,
    grant_manage_oauth_providers,
):
    details = await _patch_oauth_details(mock_app, {'legacy_keycloak_type': None})

    assert details.legacy_keycloak_type is None


@pytest.mark.asyncio
async def test_update_provider_sets_legacy_keycloak_type(
    mock_app,
    grant_manage_oauth_providers,
):
    details = await _patch_oauth_details(mock_app, {'legacy_keycloak_type': 'gitlab'})

    assert details.legacy_keycloak_type == ProviderType.GITLAB


@pytest.mark.asyncio
async def test_update_provider_rejects_enterprise_sso_legacy_keycloak_type(
    mock_app, grant_manage_oauth_providers
):
    async with _client(mock_app) as client:
        resp = await client.patch(
            '/api/admin/oauth-providers/1',
            json={
                'details': {
                    'kind': 'OAuthProviderDetails',
                    'provider_type': 'enterprise_sso',
                    'client_id': 'cid',
                    'legacy_keycloak_type': 'enterprise_sso',
                }
            },
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_provider_missing_404(mock_app, grant_manage_oauth_providers):
    with patch(
        'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/999',
                json={'title': 'Nope'},
            )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_provider_forbidden_without_permission(mock_app):
    with patch(
        'server.auth.authorization.get_user_org_role',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={'title': 'Nope'},
            )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_provider_success(mock_app, grant_manage_oauth_providers):
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
            AsyncMock(return_value=_fake_provider()),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderUserStore.get_by_user',
            AsyncMock(return_value=None),
        ) as get_by_user_mock,
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.delete',
            AsyncMock(return_value=None),
        ) as delete_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.delete('/api/admin/oauth-providers/1')

    assert resp.status_code == 204
    assert resp.content == b''
    get_by_user_mock.assert_awaited_once_with(uuid.UUID(CALLER_USER_ID), 1)
    delete_mock.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_delete_provider_missing_404(mock_app, grant_manage_oauth_providers):
    with patch(
        'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.delete('/api/admin/oauth-providers/999')

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_provider_forbidden_without_permission(mock_app):
    with patch(
        'server.auth.authorization.get_user_org_role',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.delete('/api/admin/oauth-providers/1')

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_provider_refuses_callers_own_provider(
    mock_app, grant_manage_oauth_providers
):
    """A caller cannot delete the provider they themselves authenticated
    through -- cascade-deleting it would sever their own login/connection."""
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
            AsyncMock(return_value=_fake_provider()),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderUserStore.get_by_user',
            AsyncMock(return_value=MagicMock()),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.delete',
            AsyncMock(return_value=None),
        ) as delete_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.delete('/api/admin/oauth-providers/1')

    assert resp.status_code == 409
    delete_mock.assert_not_awaited()


# ── integrated/password IDP invariant: at most one row, always is_idp=True ──


def _fake_password_provider(provider_id: int = 1, title: str = 'Password Login'):
    provider = AuthProvider(
        title=title,
        is_idp=True,
        color=DEFAULT_AUTH_PROVIDER_COLOR,
        allow_registration=True,
        allow_match_by_email=True,
        details=PasswordProviderDetails(),
    )
    provider.id = provider_id
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    provider.created_at = now
    provider.updated_at = now
    return provider


@pytest.mark.asyncio
async def test_create_integrated_idp_succeeds_when_none_exists(
    mock_app, grant_manage_oauth_providers
):
    created = _fake_password_provider(1)
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_first_by_kind',
            AsyncMock(return_value=None),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.create',
            AsyncMock(return_value=created),
        ) as create_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers',
                json={
                    'title': 'Password Login',
                    'is_idp': True,
                    'details': {'kind': 'PasswordProviderDetails'},
                },
            )

    assert resp.status_code == 201
    create_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_integrated_idp_rejects_is_idp_false(
    mock_app, grant_manage_oauth_providers
):
    with patch(
        'server.routes.oauth_providers_admin.AuthProviderStore.create',
        AsyncMock(),
    ) as create_mock:
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers',
                json={
                    'title': 'Password Login',
                    'is_idp': False,
                    'details': {'kind': 'PasswordProviderDetails'},
                },
            )

    assert resp.status_code == 400
    assert 'is_idp=True' in resp.json()['detail']
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_integrated_idp_rejects_duplicate(
    mock_app, grant_manage_oauth_providers
):
    """A second ``PasswordProviderDetails`` row is rejected even though it is
    otherwise valid (``is_idp=True``) -- at most one may exist."""
    existing = _fake_password_provider(7)
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_first_by_kind',
            AsyncMock(return_value=existing),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.create',
            AsyncMock(),
        ) as create_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers',
                json={
                    'title': 'Another Password Login',
                    'is_idp': True,
                    'details': {'kind': 'PasswordProviderDetails'},
                },
            )

    assert resp.status_code == 409
    assert 'id=7' in resp.json()['detail']
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_provider_rejects_setting_integrated_idp_is_idp_false(
    mock_app, grant_manage_oauth_providers
):
    """Flipping ``is_idp`` off the existing password-IDP row is rejected --
    it must always stay ``is_idp=True`` (see ``oauth_v2.py`` / ``idp.py``
    special-casing by ``kind`` vs. by ``is_idp``)."""
    existing = _fake_password_provider(3)
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
            AsyncMock(return_value=existing),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.update',
            AsyncMock(),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/3',
                json={'is_idp': False},
            )

    assert resp.status_code == 400
    assert 'is_idp=True' in resp.json()['detail']
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_provider_allows_updating_integrated_idp_itself(
    mock_app, grant_manage_oauth_providers
):
    """Updating an unrelated field on the existing password-IDP row must not
    be mistaken for creating a duplicate of itself."""
    existing = _fake_password_provider(3)
    updated = _fake_password_provider(3, title='Renamed')
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
            AsyncMock(return_value=existing),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_first_by_kind',
            AsyncMock(return_value=existing),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.update',
            AsyncMock(return_value=updated),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/3',
                json={'title': 'Renamed'},
            )

    assert resp.status_code == 200
    update_mock.assert_awaited_once_with(3, title='Renamed')


@pytest.mark.asyncio
async def test_update_provider_rejects_retargeting_kind_to_integrated_idp(
    mock_app, grant_manage_oauth_providers
):
    """Re-pointing an unrelated row's ``details`` onto
    ``PasswordProviderDetails`` is rejected as a duplicate of the existing
    password-IDP row."""
    existing_other = _fake_provider(1, 'github', is_idp=False)
    existing_password = _fake_password_provider(3)
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
            AsyncMock(return_value=existing_other),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_first_by_kind',
            AsyncMock(return_value=existing_password),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.update',
            AsyncMock(),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={
                    'is_idp': True,
                    'details': {'kind': 'PasswordProviderDetails'},
                },
            )

    assert resp.status_code == 409
    assert 'id=3' in resp.json()['detail']
    update_mock.assert_not_awaited()


def _fake_saml_provider(
    provider_id: int = 1,
    title: str = 'Corp SAML',
    is_idp: bool = True,
    sp_private_key: str | None = None,
    sp_x509_cert: str | None = None,
    **saml_fields,
) -> AuthProvider:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    provider = AuthProvider(
        title=title,
        is_idp=is_idp,
        color=DEFAULT_AUTH_PROVIDER_COLOR,
        allow_registration=True,
        allow_match_by_email=True,
        details=SamlProviderDetails(
            sp_private_key=SecretStr(sp_private_key) if sp_private_key else None,
            sp_x509_cert=sp_x509_cert,
            **saml_fields,
        ),
    )
    provider.id = provider_id
    provider.created_at = now
    provider.updated_at = now
    return provider


@pytest.mark.asyncio
async def test_create_saml_provider_generates_sp_keypair(
    mock_app, grant_manage_oauth_providers
):
    """Admins never submit ``sp_private_key``/``sp_x509_cert`` -- the route
    must mint both."""
    private_key_pem, cert_pem = generate_sp_keypair()
    created = _fake_saml_provider(
        sp_private_key=private_key_pem,
        sp_x509_cert=cert_pem,
        idp_entity_id='https://idp.example.com',
        sso_url='https://idp.example.com/sso',
    )
    with patch(
        'server.routes.oauth_providers_admin.AuthProviderStore.create',
        AsyncMock(return_value=created),
    ) as create_mock:
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers',
                json={
                    'title': 'Corp SAML',
                    'is_idp': True,
                    'details': {
                        'kind': 'SamlProviderDetails',
                        'idp_entity_id': 'https://idp.example.com',
                        'sso_url': 'https://idp.example.com/sso',
                    },
                },
            )

    assert resp.status_code == 201
    body = resp.json()
    assert body['details']['idp_entity_id'] == 'https://idp.example.com'
    # The private key is never echoed back, but the (public) cert is.
    assert body['details']['sp_private_key'] not in (None, private_key_pem)
    assert body['details']['sp_x509_cert'] is not None
    assert body['details']['sp_x509_cert'].startswith('-----BEGIN CERTIFICATE-----')

    (persisted,), _ = create_mock.call_args
    assert isinstance(persisted.details, SamlProviderDetails)
    assert persisted.details.sp_private_key is not None
    assert persisted.details.sp_private_key.get_secret_value().startswith(
        '-----BEGIN PRIVATE KEY-----'
    )
    assert persisted.details.sp_x509_cert is not None
    assert persisted.details.sp_x509_cert.startswith('-----BEGIN CERTIFICATE-----')


@pytest.mark.asyncio
async def test_update_saml_provider_preserves_existing_sp_keypair(
    mock_app, grant_manage_oauth_providers
):
    """Editing an unrelated field must not rotate the existing SP key+cert."""
    private_key_pem, cert_pem = generate_sp_keypair()
    existing = _fake_saml_provider(
        sp_private_key=private_key_pem, sp_x509_cert=cert_pem
    )
    updated = _fake_saml_provider(sso_url='https://idp.example.com/sso/new')
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
            AsyncMock(return_value=existing),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.update',
            AsyncMock(return_value=updated),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={
                    'details': {
                        'kind': 'SamlProviderDetails',
                        'sso_url': 'https://idp.example.com/sso/new',
                    }
                },
            )

    assert resp.status_code == 200
    _, kwargs = update_mock.call_args
    assert kwargs['details'].sp_private_key.get_secret_value() == private_key_pem
    assert kwargs['details'].sp_x509_cert == cert_pem


@pytest.mark.asyncio
async def test_update_provider_generates_sp_keypair_when_switching_kind(
    mock_app, grant_manage_oauth_providers
):
    """Retargeting an OAuth row onto ``SamlProviderDetails`` mints a fresh
    key+cert pair -- there is no previous SAML pair to carry forward."""
    existing = _fake_provider(1, 'github')
    updated = _fake_saml_provider()
    with (
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.get_by_id',
            AsyncMock(return_value=existing),
        ),
        patch(
            'server.routes.oauth_providers_admin.AuthProviderStore.update',
            AsyncMock(return_value=updated),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={
                    'details': {
                        'kind': 'SamlProviderDetails',
                        'idp_entity_id': 'https://idp.example.com',
                    }
                },
            )

    assert resp.status_code == 200
    _, kwargs = update_mock.call_args
    assert kwargs['details'].sp_private_key is not None
    assert kwargs['details'].sp_x509_cert is not None


@pytest.mark.asyncio
async def test_preview_saml_metadata_success(mock_app, grant_manage_oauth_providers):
    parsed = SamlIdpMetadata(
        idp_entity_id='https://idp.example.com',
        sso_url='https://idp.example.com/sso',
        sso_binding='redirect',
        slo_url=None,
        idp_x509_cert='-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----',
        name_id_format=None,
    )
    with patch(
        'server.routes.oauth_providers_admin.fetch_saml_idp_metadata',
        AsyncMock(return_value=parsed),
    ) as fetch_mock:
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers/saml-metadata-preview',
                json={'metadata_url': 'https://idp.example.com/metadata'},
            )

    assert resp.status_code == 200
    assert resp.json()['idp_entity_id'] == 'https://idp.example.com'
    fetch_mock.assert_awaited_once_with('https://idp.example.com/metadata')


@pytest.mark.asyncio
async def test_preview_saml_metadata_fetch_error_returns_400(
    mock_app, grant_manage_oauth_providers
):
    with patch(
        'server.routes.oauth_providers_admin.fetch_saml_idp_metadata',
        AsyncMock(side_effect=SamlMetadataFetchError('Metadata URL must be https')),
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers/saml-metadata-preview',
                json={'metadata_url': 'http://idp.example.com/metadata'},
            )

    assert resp.status_code == 400
    assert 'https' in resp.json()['detail']


@pytest.mark.asyncio
async def test_preview_saml_metadata_forbidden_without_permission(mock_app):
    with patch(
        'server.auth.authorization.get_user_org_role',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers/saml-metadata-preview',
                json={'metadata_url': 'https://idp.example.com/metadata'},
            )

    assert resp.status_code == 403


# ── GET/PATCH /{provider_id}/users ("User Links") ──────────────────────
#
# These exercise the real ``auth_provider_users`` row shape end to end (the
# DB fixtures are cheap, and the filter/join/409 logic is easiest to get
# right against real Postgres), unlike the ``AuthProviderStore``-mocking
# tests above.


@pytest.fixture
def db(async_session_maker):
    """Point every store this router touches at the shared test database."""
    patches = [
        patch('storage.auth_provider_store.a_session_maker', async_session_maker),
        patch('storage.auth_provider_user_store.a_session_maker', async_session_maker),
        patch('storage.oauth_token_store.a_session_maker', async_session_maker),
        patch(
            'server.routes.oauth_providers_admin.a_session_maker', async_session_maker
        ),
    ]
    for p in patches:
        p.start()
    yield async_session_maker
    for p in patches:
        p.stop()


@pytest.fixture
def make_provider(db):
    async def _make(category: str = 'github') -> AuthProvider:
        provider = AuthProvider(
            title=category.title(),
            is_idp=False,
            details=OAuthProviderDetails(
                provider_type=ProviderType(category), client_id='cid'
            ),
        )
        async with db() as session:
            session.add(provider)
            await session.commit()
            await session.refresh(provider)
        return provider

    return _make


@pytest.fixture
def make_link(db):
    from storage.auth_provider_user import AuthProviderUser

    async def _make(
        provider_id: int, user_id, external_subject_id: str, external_email=None
    ) -> AuthProviderUser:
        link = AuthProviderUser(
            oauth_provider_id=provider_id,
            user_id=user_id,
            external_subject_id=external_subject_id,
            external_email=external_email,
        )
        async with db() as session:
            session.add(link)
            await session.commit()
            await session.refresh(link)
        return link

    return _make


@pytest.mark.asyncio
async def test_list_auth_provider_users_404_when_provider_missing(
    mock_app, grant_manage_oauth_providers, db
):
    async with _client(mock_app) as client:
        resp = await client.get('/api/admin/oauth-providers/999/users')
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_auth_provider_users_forbidden_without_permission(
    mock_app, make_provider
):
    provider = await make_provider()
    with patch(
        'server.auth.authorization.get_user_org_role',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.get(f'/api/admin/oauth-providers/{provider.id}/users')
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_auth_provider_users_scoped_and_joined(
    mock_app, grant_manage_oauth_providers, make_provider, make_link, create_user
):
    provider_a = await make_provider('github')
    provider_b = await make_provider('gitlab')
    user_a = create_user(email='a@example.com')
    user_b = create_user(email='b@example.com')
    await make_link(provider_a.id, user_a.id, 'sub-a', 'ext-a@example.com')
    await make_link(provider_b.id, user_b.id, 'sub-b', 'ext-b@example.com')

    async with _client(mock_app) as client:
        resp = await client.get(f'/api/admin/oauth-providers/{provider_a.id}/users')

    assert resp.status_code == 200
    body = resp.json()
    assert body['total'] == 1
    assert len(body['items']) == 1
    item = body['items'][0]
    assert item['user_id'] == str(user_a.id)
    assert item['email'] == 'a@example.com'
    assert item['external_subject_id'] == 'sub-a'
    assert item['external_email'] == 'ext-a@example.com'


@pytest.mark.asyncio
async def test_list_auth_provider_users_pagination(
    mock_app, grant_manage_oauth_providers, make_provider, make_link, create_user
):
    provider = await make_provider()
    for i in range(3):
        await make_link(provider.id, create_user().id, f'sub-{i}')

    async with _client(mock_app) as client:
        resp = await client.get(
            f'/api/admin/oauth-providers/{provider.id}/users',
            params={'limit': 2},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body['total'] == 3
    assert len(body['items']) == 2
    assert body['current_page'] == 1

    async with _client(mock_app) as client:
        resp2 = await client.get(
            f'/api/admin/oauth-providers/{provider.id}/users',
            params={'limit': 2, 'page_id': '2'},
        )
    assert resp2.status_code == 200
    assert len(resp2.json()['items']) == 1


@pytest.mark.asyncio
async def test_list_auth_provider_users_filter(
    mock_app, grant_manage_oauth_providers, make_provider, make_link, create_user
):
    provider = await make_provider()
    match = create_user(email='findme@example.com')
    await make_link(provider.id, match.id, 'sub-1')
    await make_link(provider.id, create_user().id, 'sub-2')

    async with _client(mock_app) as client:
        resp = await client.get(
            f'/api/admin/oauth-providers/{provider.id}/users',
            params={'q': 'findme'},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body['total'] == 1
    assert body['items'][0]['user_id'] == str(match.id)


@pytest.mark.asyncio
async def test_update_auth_provider_user_happy_path_preserves_external_email(
    mock_app, grant_manage_oauth_providers, make_provider, make_link, create_user
):
    provider = await make_provider()
    old_user = create_user()
    new_user = create_user(email='new@example.com')
    link = await make_link(provider.id, old_user.id, 'sub-1', 'ext@example.com')

    async with _client(mock_app) as client:
        resp = await client.patch(
            f'/api/admin/oauth-providers/{provider.id}/users/{link.id}',
            json={'user_id': str(new_user.id)},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body['user_id'] == str(new_user.id)
    assert body['email'] == 'new@example.com'
    assert body['external_email'] == 'ext@example.com'
    assert body['external_subject_id'] == 'sub-1'


@pytest.mark.asyncio
async def test_update_auth_provider_user_deletes_old_users_token(
    mock_app,
    grant_manage_oauth_providers,
    make_provider,
    make_link,
    create_user,
    db,
):
    from storage.oauth_token_store import OAuthTokenStore

    provider = await make_provider()
    old_user = create_user()
    new_user = create_user()
    link = await make_link(provider.id, old_user.id, 'sub-1')

    old_token_store = OAuthTokenStore(
        user_id=old_user.id, oauth_provider_id=provider.id
    )
    await old_token_store.store_tokens(
        access_token='at',
        refresh_token=None,
        access_token_expires_at=None,
        refresh_token_expires_at=None,
    )

    async with _client(mock_app) as client:
        resp = await client.patch(
            f'/api/admin/oauth-providers/{provider.id}/users/{link.id}',
            json={'user_id': str(new_user.id)},
        )

    assert resp.status_code == 200
    assert await old_token_store.get_raw() is None


@pytest.mark.asyncio
async def test_update_auth_provider_user_invalidates_webhook_user_cache(
    mock_app, grant_manage_oauth_providers, make_provider, make_link, create_user
):
    # Redis is not available in unit tests; the cache delete itself is covered
    # in test_automation_event_service.py.
    provider = await make_provider('gitlab')
    link = await make_link(provider.id, create_user().id, '42')

    with patch(
        'server.routes.oauth_providers_admin.invalidate_user_id_cache',
        new=AsyncMock(),
    ) as invalidate_cache:
        async with _client(mock_app) as client:
            resp = await client.patch(
                f'/api/admin/oauth-providers/{provider.id}/users/{link.id}',
                json={'user_id': str(create_user().id)},
            )

    assert resp.status_code == 200
    invalidate_cache.assert_awaited_once_with(ProviderType.GITLAB, '42')


@pytest.mark.asyncio
async def test_update_auth_provider_user_404_provider_not_found(
    mock_app, grant_manage_oauth_providers, db, create_user
):
    async with _client(mock_app) as client:
        resp = await client.patch(
            '/api/admin/oauth-providers/999/users/1',
            json={'user_id': str(create_user().id)},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_auth_provider_user_404_link_not_found(
    mock_app, grant_manage_oauth_providers, make_provider, create_user
):
    provider = await make_provider()
    async with _client(mock_app) as client:
        resp = await client.patch(
            f'/api/admin/oauth-providers/{provider.id}/users/999',
            json={'user_id': str(create_user().id)},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_auth_provider_user_404_link_belongs_to_other_provider(
    mock_app, grant_manage_oauth_providers, make_provider, make_link, create_user
):
    provider_a = await make_provider('github')
    provider_b = await make_provider('gitlab')
    link = await make_link(provider_a.id, create_user().id, 'sub-1')

    async with _client(mock_app) as client:
        resp = await client.patch(
            f'/api/admin/oauth-providers/{provider_b.id}/users/{link.id}',
            json={'user_id': str(create_user().id)},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_auth_provider_user_404_target_user_not_found(
    mock_app, grant_manage_oauth_providers, make_provider, make_link, create_user
):
    provider = await make_provider()
    link = await make_link(provider.id, create_user().id, 'sub-1')

    async with _client(mock_app) as client:
        resp = await client.patch(
            f'/api/admin/oauth-providers/{provider.id}/users/{link.id}',
            json={'user_id': str(uuid.uuid4())},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_auth_provider_user_409_target_already_linked(
    mock_app, grant_manage_oauth_providers, make_provider, make_link, create_user
):
    provider = await make_provider()
    target = create_user()
    await make_link(provider.id, target.id, 'sub-already-linked')
    link = await make_link(provider.id, create_user().id, 'sub-to-repoint')

    async with _client(mock_app) as client:
        resp = await client.patch(
            f'/api/admin/oauth-providers/{provider.id}/users/{link.id}',
            json={'user_id': str(target.id)},
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_auth_provider_user_forbidden_without_permission(
    mock_app, make_provider, make_link, create_user
):
    provider = await make_provider()
    link = await make_link(provider.id, create_user().id, 'sub-1')
    with patch(
        'server.auth.authorization.get_user_org_role',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                f'/api/admin/oauth-providers/{provider.id}/users/{link.id}',
                json={'user_id': str(create_user().id)},
            )
    assert resp.status_code == 403
