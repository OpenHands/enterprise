"""Route-level tests for the ``oauth_providers`` admin CRUD API.

These exercise the FastAPI routes through ``require_permission`` and stub
``OAuthProviderStore`` at the route module boundary, mirroring
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

from openhands.app_server.user_auth import get_user_id
from server.routes.oauth_providers_admin import oauth_provider_admin_router

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
    provider_category: str = 'github',
    display_name: str = 'GitHub',
    is_idp: bool = False,
    client_id: str = 'cid',
    client_secret: dict | None = None,
    authorization_url: str | None = 'https://example.com/auth',
    token_url: str | None = 'https://example.com/token',
    userinfo_url: str | None = 'https://example.com/userinfo',
    scopes: list[str] | None = None,
    permitted_drift_seconds: int = 60,
):
    provider = MagicMock()
    provider.id = provider_id
    provider.provider_category = provider_category
    provider.display_name = display_name
    provider.is_idp = is_idp
    provider.client_id = client_id
    provider.client_secret = client_secret
    provider.authorization_url = authorization_url
    provider.token_url = token_url
    provider.userinfo_url = userinfo_url
    provider.scopes = scopes or ['repo']
    provider.permitted_drift_seconds = permitted_drift_seconds
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    provider.created_at = now
    provider.updated_at = now
    return provider


@pytest.mark.asyncio
async def test_list_providers(mock_app, grant_manage_oauth_providers):
    providers = [_fake_provider(1, 'github'), _fake_provider(2, 'gitlab')]
    with patch(
        'server.routes.oauth_providers_admin.OAuthProviderStore.list_all',
        AsyncMock(return_value=providers),
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/oauth-providers')

    assert resp.status_code == 200
    body = resp.json()
    assert {p['provider_category'] for p in body} == {'github', 'gitlab'}
    # client_secret itself is never serialized.
    assert all('client_secret' not in p for p in body)


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
    provider = _fake_provider(client_secret={'v': 'shh'})
    with patch(
        'server.routes.oauth_providers_admin.OAuthProviderStore.get_by_id',
        AsyncMock(return_value=provider),
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/oauth-providers/1')

    assert resp.status_code == 200
    body = resp.json()
    assert body['id'] == 1
    # Secret presence is signalled, never the value.
    assert body['has_client_secret'] is True


@pytest.mark.asyncio
async def test_get_provider_missing_404(mock_app, grant_manage_oauth_providers):
    with patch(
        'server.routes.oauth_providers_admin.OAuthProviderStore.get_by_id',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/admin/oauth-providers/999')

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_provider(mock_app, grant_manage_oauth_providers):
    created = _fake_provider(client_secret={'v': 'super-secret'})
    with patch(
        'server.routes.oauth_providers_admin.OAuthProviderStore.create',
        AsyncMock(return_value=created),
    ) as create_mock:
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers',
                json={
                    'provider_category': 'github',
                    'display_name': 'GitHub',
                    'client_id': 'cid',
                    'client_secret': 'super-secret',
                },
            )

    assert resp.status_code == 201
    body = resp.json()
    assert body['provider_category'] == 'github'
    assert body['has_client_secret'] is True
    assert 'client_secret' not in body

    # The secret is wrapped for the EncryptedJSON column before persisting.
    (persisted,), _ = create_mock.call_args
    assert persisted.client_secret == {'v': 'super-secret'}
    assert persisted.provider_category == 'github'
    assert persisted.client_id == 'cid'


@pytest.mark.asyncio
async def test_create_provider_without_secret(mock_app, grant_manage_oauth_providers):
    created = _fake_provider(client_secret=None)
    with patch(
        'server.routes.oauth_providers_admin.OAuthProviderStore.create',
        AsyncMock(return_value=created),
    ) as create_mock:
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers',
                json={
                    'provider_category': 'enterprise_sso',
                    'display_name': 'Corp SSO',
                    'client_id': 'cid',
                    'is_idp': True,
                },
            )

    assert resp.status_code == 201
    assert resp.json()['has_client_secret'] is False
    (persisted,), _ = create_mock.call_args
    assert persisted.client_secret is None
    assert persisted.is_idp is True


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
                    'provider_category': 'github',
                    'display_name': 'GitHub',
                    'client_id': 'cid',
                },
            )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_provider_partial(mock_app, grant_manage_oauth_providers):
    updated = _fake_provider(display_name='New Name')
    with (
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.get_by_id',
            AsyncMock(return_value=_fake_provider(1, 'github')),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.update',
            AsyncMock(return_value=updated),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={'display_name': 'New Name'},
            )

    assert resp.status_code == 200
    assert resp.json()['display_name'] == 'New Name'
    # Only the explicitly-set field is forwarded to the store.
    update_mock.assert_awaited_once_with(1, display_name='New Name')


@pytest.mark.asyncio
async def test_update_provider_sets_new_secret(mock_app, grant_manage_oauth_providers):
    updated = _fake_provider(client_secret={'v': 'new-secret'})
    with (
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.get_by_id',
            AsyncMock(return_value=_fake_provider(1, 'github')),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.update',
            AsyncMock(return_value=updated),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={'client_secret': 'new-secret'},
            )

    assert resp.status_code == 200
    assert resp.json()['has_client_secret'] is True
    update_mock.assert_awaited_once_with(1, client_secret={'v': 'new-secret'})


@pytest.mark.asyncio
async def test_update_provider_clears_secret_with_empty_string(
    mock_app, grant_manage_oauth_providers
):
    updated = _fake_provider(client_secret=None)
    with (
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.get_by_id',
            AsyncMock(return_value=_fake_provider(1, 'github')),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.update',
            AsyncMock(return_value=updated),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={'client_secret': ''},
            )

    assert resp.status_code == 200
    assert resp.json()['has_client_secret'] is False
    update_mock.assert_awaited_once_with(1, client_secret=None)


@pytest.mark.asyncio
async def test_update_provider_omitted_secret_untouched(
    mock_app, grant_manage_oauth_providers
):
    updated = _fake_provider(display_name='Renamed')
    with (
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.get_by_id',
            AsyncMock(return_value=_fake_provider(1, 'github')),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.update',
            AsyncMock(return_value=updated),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={'display_name': 'Renamed'},
            )

    assert resp.status_code == 200
    # client_secret was not part of the request body, so it is not forwarded
    # to the store at all -- the existing secret is left untouched.
    update_mock.assert_awaited_once_with(1, display_name='Renamed')
    assert 'client_secret' not in update_mock.call_args.kwargs


@pytest.mark.asyncio
async def test_update_provider_missing_404(mock_app, grant_manage_oauth_providers):
    with patch(
        'server.routes.oauth_providers_admin.OAuthProviderStore.get_by_id',
        AsyncMock(return_value=None),
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/999',
                json={'display_name': 'Nope'},
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
                json={'display_name': 'Nope'},
            )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_provider_success(mock_app, grant_manage_oauth_providers):
    with (
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.get_by_id',
            AsyncMock(return_value=_fake_provider()),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderUserStore.get_by_user',
            AsyncMock(return_value=None),
        ) as get_by_user_mock,
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.delete',
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
        'server.routes.oauth_providers_admin.OAuthProviderStore.get_by_id',
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
            'server.routes.oauth_providers_admin.OAuthProviderStore.get_by_id',
            AsyncMock(return_value=_fake_provider()),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderUserStore.get_by_user',
            AsyncMock(return_value=MagicMock()),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.delete',
            AsyncMock(return_value=None),
        ) as delete_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.delete('/api/admin/oauth-providers/1')

    assert resp.status_code == 409
    delete_mock.assert_not_awaited()


# ── integrated/password IDP invariant: at most one row, always is_idp=True ──


@pytest.mark.asyncio
async def test_create_integrated_idp_succeeds_when_none_exists(
    mock_app, grant_manage_oauth_providers
):
    created = _fake_provider(
        1, provider_category='integrated_idp', is_idp=True, client_id='integrated-idp'
    )
    with (
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.get_first_by_category',
            AsyncMock(return_value=None),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.create',
            AsyncMock(return_value=created),
        ) as create_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers',
                json={
                    'provider_category': 'integrated_idp',
                    'display_name': 'Password Login',
                    'client_id': 'integrated-idp',
                    'is_idp': True,
                },
            )

    assert resp.status_code == 201
    create_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_integrated_idp_rejects_is_idp_false(
    mock_app, grant_manage_oauth_providers
):
    with patch(
        'server.routes.oauth_providers_admin.OAuthProviderStore.create',
        AsyncMock(),
    ) as create_mock:
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers',
                json={
                    'provider_category': 'integrated_idp',
                    'display_name': 'Password Login',
                    'client_id': 'integrated-idp',
                    'is_idp': False,
                },
            )

    assert resp.status_code == 400
    assert 'is_idp=True' in resp.json()['detail']
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_integrated_idp_rejects_duplicate(
    mock_app, grant_manage_oauth_providers
):
    """A second ``integrated_idp`` row is rejected even though it is
    otherwise valid (``is_idp=True``) -- at most one may exist."""
    existing = _fake_provider(
        7, provider_category='integrated_idp', is_idp=True, client_id='integrated-idp'
    )
    with (
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.get_first_by_category',
            AsyncMock(return_value=existing),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.create',
            AsyncMock(),
        ) as create_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.post(
                '/api/admin/oauth-providers',
                json={
                    'provider_category': 'integrated_idp',
                    'display_name': 'Another Password Login',
                    'client_id': 'integrated-idp-2',
                    'is_idp': True,
                },
            )

    assert resp.status_code == 409
    assert 'id=7' in resp.json()['detail']
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_provider_rejects_setting_integrated_idp_is_idp_false(
    mock_app, grant_manage_oauth_providers
):
    """Flipping ``is_idp`` off the existing integrated-IDP row is rejected --
    it must always stay ``is_idp=True`` (see ``oauth_v2.py`` / ``idp.py``
    special-casing by category vs. by ``is_idp``)."""
    existing = _fake_provider(
        3, provider_category='integrated_idp', is_idp=True, client_id='integrated-idp'
    )
    with (
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.get_by_id',
            AsyncMock(return_value=existing),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.update',
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
    """Updating an unrelated field on the existing integrated-IDP row must
    not be mistaken for creating a duplicate of itself."""
    existing = _fake_provider(
        3, provider_category='integrated_idp', is_idp=True, client_id='integrated-idp'
    )
    updated = _fake_provider(
        3,
        provider_category='integrated_idp',
        is_idp=True,
        client_id='integrated-idp',
        display_name='Renamed',
    )
    with (
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.get_by_id',
            AsyncMock(return_value=existing),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.get_first_by_category',
            AsyncMock(return_value=existing),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.update',
            AsyncMock(return_value=updated),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/3',
                json={'display_name': 'Renamed'},
            )

    assert resp.status_code == 200
    update_mock.assert_awaited_once_with(3, display_name='Renamed')


@pytest.mark.asyncio
async def test_update_provider_rejects_retargeting_category_to_integrated_idp(
    mock_app, grant_manage_oauth_providers
):
    """Re-pointing an unrelated row's category onto the existing
    integrated-IDP row's category is rejected as a duplicate."""
    existing_other = _fake_provider(1, provider_category='github', is_idp=False)
    existing_integrated = _fake_provider(
        3, provider_category='integrated_idp', is_idp=True, client_id='integrated-idp'
    )
    with (
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.get_by_id',
            AsyncMock(return_value=existing_other),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.get_first_by_category',
            AsyncMock(return_value=existing_integrated),
        ),
        patch(
            'server.routes.oauth_providers_admin.OAuthProviderStore.update',
            AsyncMock(),
        ) as update_mock,
    ):
        async with _client(mock_app) as client:
            resp = await client.patch(
                '/api/admin/oauth-providers/1',
                json={'provider_category': 'integrated_idp', 'is_idp': True},
            )

    assert resp.status_code == 409
    assert 'id=3' in resp.json()['detail']
    update_mock.assert_not_awaited()
