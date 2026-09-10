"""Unit tests for the user-provisioning admin endpoint.

These tests exercise the route handler directly (rather than through the
FastAPI test client) so they can mock the underlying Keycloak, database,
and LiteLLM dependencies without bringing up the entire SAAS stack. The
permission wiring itself is exercised separately by asserting on
``ROLE_PERMISSIONS``.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Response

from server.auth.authorization import (
    ROLE_PERMISSIONS,
    Permission,
    RoleName,
)
from server.routes.user_provisioning import (
    ProvisionUserRequest,
    _generate_password,
    provision_user,
)


@pytest.fixture(autouse=True)
def initialized_keycloak_mode(monkeypatch):
    from server.auth import mode

    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)


class TestGeneratePassword:
    """The generated password must satisfy a basic complexity policy."""

    def test_length_and_complexity(self):
        for _ in range(5):
            pw = _generate_password()
            assert len(pw) == 24
            assert any(c.islower() for c in pw)
            assert any(c.isupper() for c in pw)
            assert any(c.isdigit() for c in pw)
            assert any(c in '!@#$%^&*-_=+' for c in pw)

    def test_custom_length(self):
        pw = _generate_password(length=32)
        assert len(pw) == 32


class TestProvisionUserPermissionWiring:
    """The provision permission is available to org admins and super roles."""

    def test_permission_enum_includes_provision_user(self):
        assert Permission.PROVISION_USER.value == 'provision_user'

    def test_owner_has_permission(self):
        assert Permission.PROVISION_USER in ROLE_PERMISSIONS[RoleName.OWNER]

    def test_admin_has_permission(self):
        assert Permission.PROVISION_USER in ROLE_PERMISSIONS[RoleName.ADMIN]

    def test_member_does_not_have_permission(self):
        assert Permission.PROVISION_USER not in ROLE_PERMISSIONS[RoleName.MEMBER]

    def test_superadmin_has_permission(self):
        from server.auth.authorization import SUPER_ROLE_PERMISSIONS

        assert Permission.PROVISION_USER in SUPER_ROLE_PERMISSIONS[RoleName.ADMIN]

    def test_superowner_does_not_have_permission_yet(self):
        from server.auth.authorization import SUPER_ROLE_PERMISSIONS

        assert Permission.PROVISION_USER not in SUPER_ROLE_PERMISSIONS[RoleName.OWNER]

    def test_supermember_does_not_have_permission(self):
        from server.auth.authorization import SUPER_ROLE_PERMISSIONS

        assert Permission.PROVISION_USER not in SUPER_ROLE_PERMISSIONS[RoleName.MEMBER]


class TestProvisionUserRequestValidation:
    def test_email_is_required(self):
        with pytest.raises(ValueError):
            ProvisionUserRequest(email='not-an-email')  # type: ignore[arg-type]

    def test_password_min_length(self):
        with pytest.raises(ValueError):
            ProvisionUserRequest(email='a@b.com', password='short')

    def test_optional_password(self):
        req = ProvisionUserRequest(email='a@b.com')
        assert req.password is None

    def test_default_role_is_member(self):
        req = ProvisionUserRequest(email='a@b.com')
        assert req.role == 'member'

    def test_admin_role_is_allowed(self):
        req = ProvisionUserRequest(email='a@b.com', role='admin')
        assert req.role == 'admin'

    def test_owner_role_is_allowed(self):
        req = ProvisionUserRequest(email='a@b.com', role='owner')
        assert req.role == 'owner'

    def test_reissue_api_key_defaults_to_false(self):
        # Default is idempotent (return existing key). Reissuing is an
        # opt-in because the caller is about to invalidate whatever is
        # currently configured for the user.
        req = ProvisionUserRequest(email='a@b.com')
        assert req.reissue_api_key is False

    def test_reissue_api_key_can_be_enabled(self):
        req = ProvisionUserRequest(email='a@b.com', reissue_api_key=True)
        assert req.reissue_api_key is True


@pytest.fixture
async def provision_context(monkeypatch):
    from datetime import UTC, datetime

    from starlette.requests import Request

    from server.auth.contracts import Principal, UserProfile

    caller = uuid.uuid4()
    org = uuid.uuid4()
    user = UserProfile(uuid.uuid4(), 'new@example.com')
    principal = Principal(caller, 'api_key', datetime.now(UTC), organization_id=org)
    service = AsyncMock()
    service.provision.return_value = (user, True, True, 'sk-oh-provisioned')
    monkeypatch.setattr(
        'server.routes.user_provisioning.EnterpriseUserManagementService',
        lambda: service,
    )
    monkeypatch.setattr(
        'server.routes.user_provisioning.get_user_auth',
        AsyncMock(return_value=MagicMock(principal=principal)),
    )
    monkeypatch.setattr(
        'storage.org_store.OrgStore.get_org_by_id',
        AsyncMock(return_value=MagicMock(id=org)),
    )
    monkeypatch.setattr(
        'storage.user_store.UserStore.get_user_by_id', AsyncMock(return_value=None)
    )
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': []})
    return service, user, principal, request


@pytest.mark.parametrize('role', ['member', 'admin', 'owner'])
async def test_provision_creation_preserves_contract(provision_context, role):
    service, user, principal, request = provision_context
    response = Response()
    result = await provision_user(
        ProvisionUserRequest(email=user.email, password='Original password', role=role),
        response,
        request,
        str(principal.user_id),
        principal.organization_id,
    )
    assert response.status_code == 201
    assert result.password == 'Original password'
    assert result.created and result.action == 'created'
    assert result.user_id == str(user.id) and result.org_id == str(
        principal.organization_id
    )
    assert service.provision.await_args.kwargs['role_name'] == role
    assert (
        service.provision.await_args.args[1].get_secret_value() == 'Original password'
    )


@pytest.mark.parametrize(
    'added,action', [(True, 'added_to_org'), (False, 'reprovisioned')]
)
async def test_reprovision_returns_no_password(provision_context, added, action):
    service, user, principal, request = provision_context
    service.provision.return_value = (user, False, added, 'sk-oh-provisioned')
    response = Response()
    result = await provision_user(
        ProvisionUserRequest(email=user.email, reissue_api_key=True),
        response,
        request,
        str(principal.user_id),
        principal.organization_id,
    )
    assert response.status_code == 200
    assert result.password is None and not result.created and result.action == action
    assert service.provision.await_args.kwargs['reissue_api_key'] is True


async def test_provision_rejects_personal_workspace(provision_context, monkeypatch):
    service, user, principal, request = provision_context
    monkeypatch.setattr(
        'storage.user_store.UserStore.get_user_by_id', AsyncMock(return_value=user)
    )
    with pytest.raises(HTTPException) as error:
        await provision_user(
            ProvisionUserRequest(email=user.email),
            Response(),
            request,
            str(principal.user_id),
            principal.organization_id,
        )
    assert error.value.status_code == 403
    service.provision.assert_not_awaited()


async def test_provision_failure_retains_account_for_retry(provision_context):
    from server.auth.contracts import AuthenticationUnavailable

    service, user, principal, request = provision_context
    service.provision.side_effect = AuthenticationUnavailable(
        'upstream private details'
    )
    with pytest.raises(HTTPException) as error:
        await provision_user(
            ProvisionUserRequest(email=user.email),
            Response(),
            request,
            str(principal.user_id),
            principal.organization_id,
        )
    assert error.value.status_code == 503
    service.delete.assert_not_awaited()
    assert 'private' not in error.value.detail


@pytest.mark.parametrize(
    'body',
    [
        {'email': 'invalid-email', 'password': 'raw password must not appear'},
        {'email': 'person@example.com', 'password': 'short'},
        {'email': 'person@example.com', 'password': 'x' * 1025},
    ],
)
async def test_provision_validation_never_echoes_password(body, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from server.auth import mode
    from server.auth.http import AuthValidationRoute

    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.LOCAL)
    app = FastAPI()
    app.router.route_class = AuthValidationRoute

    @app.post('/parse')
    async def parse(body: ProvisionUserRequest):
        return {'ok': True}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as client:
        response = await client.post('/parse', json=body)
    assert response.status_code == 422
    assert response.json() == {'detail': 'Invalid authentication request.'}
    assert body['password'] not in response.text


@pytest.mark.parametrize('length', [15, 1024])
def test_local_provision_accepts_full_local_password_range(monkeypatch, length):
    from server.auth import mode

    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.LOCAL)
    body = ProvisionUserRequest(email='person@example.com', password='x' * length)
    assert len(body.password.get_secret_value()) == length
    assert 'x' * length not in repr(body)
