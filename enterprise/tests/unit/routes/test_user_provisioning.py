"""Unit tests for the user-provisioning admin endpoint.

These tests exercise the route handler directly (rather than through the
FastAPI test client) so they can mock the underlying Keycloak, database,
and LiteLLM dependencies without bringing up the entire SAAS stack. The
permission wiring itself is exercised separately by asserting on
``ROLE_PERMISSIONS``.
"""

from __future__ import annotations

import contextlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Response
from keycloak.exceptions import KeycloakError
from pydantic import SecretStr
from server.auth.authorization import (
    ROLE_PERMISSIONS,
    Permission,
    RoleName,
)
from server.routes.user_provisioning import (
    DEFAULT_PROVISIONED_ROLE,
    ProvisionUserRequest,
    ProvisionUserResponse,
    _generate_password,
    provision_user,
)
from storage.user_store import UserStore


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


class TestProvisionUserHandler:
    """End-to-end handler test with all external collaborators mocked."""

    @pytest.fixture
    def caller_user_id(self) -> str:
        return '11111111-1111-1111-1111-111111111111'

    @pytest.fixture
    def target_org_id(self) -> uuid.UUID:
        return uuid.UUID('22222222-2222-2222-2222-222222222222')

    @pytest.fixture
    def new_user_id(self) -> str:
        # Distinct from target_org_id so the route takes the
        # "add to non-personal org" branch.
        return '33333333-3333-3333-3333-333333333333'

    def _patch_dependencies(
        self,
        new_user_id: str,
        target_org_id: uuid.UUID,
        *,
        org_exists: bool = True,
        keycloak_raises: Exception | None = None,
        existing_kc_user_id: str | None = None,
        existing_oh_user: MagicMock | None = None,
        existing_org_member: MagicMock | None = None,
        existing_api_key: str | None = None,
    ):
        """Return a stack of patches as a list of context managers.

        Tests enter all of them via ``contextlib.ExitStack`` so each
        patch's mock can be asserted on individually.
        """
        token_manager_mock = MagicMock()
        token_manager_mock.create_keycloak_user = AsyncMock(
            side_effect=keycloak_raises if keycloak_raises else None,
            return_value=new_user_id,
        )
        token_manager_mock.request_offline_token = AsyncMock(
            return_value='offline-refresh-token'
        )
        token_manager_mock.store_offline_token = AsyncMock()
        token_manager_mock.delete_keycloak_user = AsyncMock(return_value=True)
        token_manager_mock.get_user_id_from_user_email = AsyncMock(
            return_value=existing_kc_user_id
        )

        new_user = MagicMock()
        new_user.id = uuid.UUID(new_user_id)

        settings_mock = MagicMock()
        settings_mock.agent_settings.llm.api_key = SecretStr('litellm-key')

        role_mock = MagicMock()
        role_mock.id = 42
        role_store_mock = AsyncMock(return_value=role_mock)

        api_key_store_mock = MagicMock()
        api_key_store_mock.create_api_key = AsyncMock(
            return_value='sk-oh-generated-api-key'
        )
        api_key_store_mock.retrieve_api_key_by_name = AsyncMock(
            return_value=existing_api_key
        )
        api_key_store_mock.delete_api_key_by_name = AsyncMock(return_value=True)

        org = MagicMock() if org_exists else None

        # Rollback collaborators. We construct these as standalone
        # ``AsyncMock``s so tests can assert on call counts and
        # arguments without having to dig back into the patch object.
        delete_org_cascade_mock = AsyncMock(return_value=org)
        remove_member_mock = AsyncMock(return_value=True)
        set_flags_mock = AsyncMock()
        add_user_to_org_mock = AsyncMock()

        # If a pre-existing User row was supplied, return it from
        # ``UserStore.get_user_by_email``; otherwise return None
        # (i.e. "no existing local user"). The ``UserStore.create_user``
        # mock returns ``new_user`` on the create path; the recover
        # path reuses it idempotently.
        get_user_by_email_mock = AsyncMock(return_value=existing_oh_user)

        get_org_member_mock = AsyncMock(return_value=existing_org_member)

        patches = [
            patch(
                'server.routes.user_provisioning.TokenManager',
                return_value=token_manager_mock,
            ),
            patch(
                'server.routes.user_provisioning.OrgStore.get_org_by_id',
                new_callable=AsyncMock,
                return_value=org,
            ),
            patch(
                'server.routes.user_provisioning.UserStore.create_user',
                new_callable=AsyncMock,
                return_value=new_user,
            ),
            patch(
                'server.routes.user_provisioning.UserStore.get_user_by_email',
                get_user_by_email_mock,
            ),
            patch(
                'server.routes.user_provisioning._set_user_provisioned_flags',
                set_flags_mock,
            ),
            patch(
                'server.routes.user_provisioning.OrgService.create_litellm_integration',
                new_callable=AsyncMock,
                return_value=settings_mock,
            ),
            patch(
                'server.routes.user_provisioning.RoleStore.get_role_by_name',
                role_store_mock,
            ),
            patch(
                'server.routes.user_provisioning.OrgMemberStore.add_user_to_org',
                add_user_to_org_mock,
            ),
            patch(
                'server.routes.user_provisioning.OrgMemberStore.get_org_member',
                get_org_member_mock,
            ),
            patch(
                'server.routes.user_provisioning.ApiKeyStore.get_instance',
                return_value=api_key_store_mock,
            ),
            # Rollback path: ``_rollback_partial_provision`` calls
            # ``OrgMemberStore.remove_user_from_org`` first, then
            # ``OrgStore.delete_org_cascade`` on the personal org,
            # then ``TokenManager.delete_keycloak_user``. Patch the
            # first two here so the tests can assert on the rollback
            # order without hitting a real DB.
            patch(
                'server.routes.user_provisioning.OrgMemberStore.remove_user_from_org',
                remove_member_mock,
            ),
            patch(
                'server.routes.user_provisioning.OrgStore.delete_org_cascade',
                delete_org_cascade_mock,
            ),
            # OHE-2980: provision-user serializes concurrent callers
            # via the per-email ``UserStore.acquire_user_creation_lock``
            # helper. The default mock returns ``True`` immediately so
            # every existing test continues to take the fast path;
            # concurrency-specific tests override this with a
            # contention-aware mock (see ``test_concurrent_*``).
            patch.object(
                UserStore,
                'acquire_user_creation_lock',
                AsyncMock(return_value=True),
            ),
            patch.object(
                UserStore,
                'release_user_creation_lock',
                AsyncMock(return_value=True),
            ),
        ]
        return patches, {
            'token_manager': token_manager_mock,
            'api_key_store': api_key_store_mock,
            'set_flags': set_flags_mock,
            'add_user_to_org': add_user_to_org_mock,
            'get_user_by_email': get_user_by_email_mock,
            'get_org_member': get_org_member_mock,
            'role_store': role_store_mock,
            'remove_member': remove_member_mock,
            'delete_org_cascade': delete_org_cascade_mock,
        }

    @staticmethod
    def _enter_all(patches):
        """Enter every patch in ``patches`` via an ``ExitStack``.

        Returning the stack lets the caller use ``with stack:`` to
        guarantee tear-down. This avoids the brittle
        ``with (patches[0], patches[1], ...)`` pattern that has to be
        edited every time the patch list grows.
        """
        stack = contextlib.ExitStack()
        for p in patches:
            stack.enter_context(p)
        return stack

    @staticmethod
    async def _call(
        body, *, caller_user_id, target_org_id
    ) -> tuple[Response, ProvisionUserResponse]:
        """Invoke ``provision_user`` with a fresh ``Response``.

        The handler now takes a ``Response`` injection so it can
        override the default 201 status code on idempotent
        re-provisions. Tests get the response object back so they can
        assert on the final status code.
        """
        response = Response()
        result = await provision_user(
            body=body,
            response=response,
            caller_user_id=caller_user_id,
            target_org_id=target_org_id,
        )
        return response, result

    @pytest.mark.asyncio
    async def test_happy_path_with_supplied_password(
        self, caller_user_id, target_org_id, new_user_id
    ):
        patches, handles = self._patch_dependencies(new_user_id, target_org_id)
        with self._enter_all(patches):
            response, resp = await self._call(
                body=ProvisionUserRequest(
                    email='Alice@Example.com',
                    password='SuperSecret-1234',
                ),
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )

        assert resp.email == 'alice@example.com'
        assert resp.password == 'SuperSecret-1234'
        assert resp.api_key == 'sk-oh-generated-api-key'
        assert resp.user_id == new_user_id
        assert resp.org_id == str(target_org_id)
        assert resp.role == 'member'
        assert resp.created is True
        assert resp.action == 'created'
        # True first-time create returns 201 Created.
        assert response.status_code == 201

        # Offline token must have been stored against the newly created
        # Keycloak user id, not against the caller.
        handles['token_manager'].store_offline_token.assert_awaited_once_with(
            user_id=new_user_id, offline_token='offline-refresh-token'
        )
        handles['role_store'].assert_awaited_once_with('member')
        handles['add_user_to_org'].assert_awaited_once()
        add_kwargs = handles['add_user_to_org'].await_args.kwargs
        assert add_kwargs['role_id'] == 42

        # API key must be bound to the target org, not the personal one.
        handles['api_key_store'].create_api_key.assert_awaited_once()
        kwargs = handles['api_key_store'].create_api_key.await_args.kwargs
        assert kwargs['org_id'] == target_org_id
        assert kwargs['user_id'] == new_user_id
        # On the happy path no rollback should run.
        handles['delete_org_cascade'].assert_not_awaited()
        handles['remove_member'].assert_not_awaited()
        handles['token_manager'].delete_keycloak_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_can_provision_admin_role(
        self, caller_user_id, target_org_id, new_user_id
    ):
        patches, handles = self._patch_dependencies(new_user_id, target_org_id)
        with self._enter_all(patches):
            _, resp = await self._call(
                body=ProvisionUserRequest(
                    email='admin@example.com',
                    password='SuperSecret-1234',
                    role='admin',
                ),
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )

        assert resp.role == 'admin'
        assert resp.action == 'created'
        handles['role_store'].assert_awaited_once_with('admin')
        handles['add_user_to_org'].assert_awaited_once()
        add_kwargs = handles['add_user_to_org'].await_args.kwargs
        assert add_kwargs['role_id'] == 42

    @pytest.mark.asyncio
    async def test_can_provision_owner_role(
        self, caller_user_id, target_org_id, new_user_id
    ):
        patches, handles = self._patch_dependencies(new_user_id, target_org_id)
        with self._enter_all(patches):
            _, resp = await self._call(
                body=ProvisionUserRequest(
                    email='owner@example.com',
                    password='SuperSecret-1234',
                    role='owner',
                ),
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )

        assert resp.role == 'owner'
        assert resp.action == 'created'
        handles['role_store'].assert_awaited_once_with('owner')
        handles['add_user_to_org'].assert_awaited_once()
        add_kwargs = handles['add_user_to_org'].await_args.kwargs
        assert add_kwargs['role_id'] == 42

    @pytest.mark.asyncio
    async def test_generates_password_when_not_supplied(
        self, caller_user_id, target_org_id, new_user_id
    ):
        patches, handles = self._patch_dependencies(new_user_id, target_org_id)
        with self._enter_all(patches):
            _, resp = await self._call(
                body=ProvisionUserRequest(email='bob@example.com'),
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )

        assert resp.password is not None
        assert len(resp.password) >= 8
        assert resp.action == 'created'
        # Verify the same generated password was used for the Keycloak
        # account creation, not regenerated each time.
        kc_call = handles['token_manager'].create_keycloak_user.await_args
        assert kc_call.kwargs['password'] == resp.password

    @pytest.mark.asyncio
    async def test_target_org_not_found_returns_404(
        self, caller_user_id, target_org_id, new_user_id
    ):
        patches, handles = self._patch_dependencies(
            new_user_id, target_org_id, org_exists=False
        )
        with self._enter_all(patches):
            with pytest.raises(HTTPException) as exc_info:
                await self._call(
                    body=ProvisionUserRequest(email='bob@example.com'),
                    caller_user_id=caller_user_id,
                    target_org_id=target_org_id,
                )
        assert exc_info.value.status_code == 404
        # Keycloak must not have been touched.
        handles['token_manager'].create_keycloak_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_personal_workspace_rejected_with_403(
        self, caller_user_id, new_user_id
    ):
        """``target_org_id == caller's user id`` is a personal workspace.

        Every user is the owner of their personal org (Org.id ==
        User.id == UUID(keycloak.sub)), so a bare permission check
        on ``PROVISION_USER`` would otherwise let any normal user
        provision additional Keycloak/OpenHands accounts inside
        their own personal workspace and walk away with the
        credentials. Mirrors the personal-workspace rejection in
        ``server.services.org_invitation_service`` (403 Forbidden)
        and must run *before* Keycloak is touched.
        """
        caller_personal_org_id = uuid.UUID(caller_user_id)
        patches, handles = self._patch_dependencies(new_user_id, caller_personal_org_id)
        with self._enter_all(patches):
            with pytest.raises(HTTPException) as exc_info:
                await self._call(
                    body=ProvisionUserRequest(email='bob@example.com'),
                    caller_user_id=caller_user_id,
                    target_org_id=caller_personal_org_id,
                )

        assert exc_info.value.status_code == 403
        assert 'personal workspace' in exc_info.value.detail.lower()
        # The rejection must short-circuit before any side effects.
        handles['token_manager'].create_keycloak_user.assert_not_awaited()
        handles['delete_org_cascade'].assert_not_awaited()
        handles['remove_member'].assert_not_awaited()
        handles['token_manager'].delete_keycloak_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_keycloak_real_failure_returns_409(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """Non-409 Keycloak failures surface as 409 with no rollback.

        ``KeycloakError('user already exists')`` now triggers the
        TOCTOU recovery branch; this test exercises a *different*
        Keycloak failure (e.g. a 500 from a broken admin endpoint) so
        the route should surface it to the caller unchanged.
        """
        patches, handles = self._patch_dependencies(
            new_user_id,
            target_org_id,
            keycloak_raises=KeycloakError('admin endpoint exploded'),
        )
        # The follow-up lookup returns nothing (no recovery possible).
        handles['token_manager'].get_user_id_from_user_email.return_value = None
        with self._enter_all(patches):
            with pytest.raises(HTTPException) as exc_info:
                await self._call(
                    body=ProvisionUserRequest(email='dup@example.com'),
                    caller_user_id=caller_user_id,
                    target_org_id=target_org_id,
                )
        assert exc_info.value.status_code == 409
        # Cleanup should not run if Keycloak creation itself failed —
        # there is nothing to roll back.
        handles['token_manager'].delete_keycloak_user.assert_not_awaited()
        handles['delete_org_cascade'].assert_not_awaited()
        handles['remove_member'].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_keycloak_toctou_recovery_returns_409_when_lookup_fails(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """409 from Keycloak + no recovery: surface as 409.

        If the create raises a 409 but the second
        ``get_user_id_from_user_email`` lookup also returns ``None``,
        the route cannot recover — Keycloak has reported a duplicate
        but the admin token cannot see the row that caused the
        duplicate. The route surfaces a structured 409 instead of
        silently leaving the user in a half-state.
        """
        patches, handles = self._patch_dependencies(
            new_user_id,
            target_org_id,
            keycloak_raises=KeycloakError(
                'User exists',
                response_code=409,
            ),
            existing_kc_user_id=None,
            existing_oh_user=None,
        )
        # Both lookups return None — no recovery possible.
        handles['token_manager'].get_user_id_from_user_email.return_value = None
        handles['get_user_by_email'].return_value = None
        with self._enter_all(patches):
            with pytest.raises(HTTPException) as exc_info:
                await self._call(
                    body=ProvisionUserRequest(email='dup@example.com'),
                    caller_user_id=caller_user_id,
                    target_org_id=target_org_id,
                )

        assert exc_info.value.status_code == 409
        assert 'could not be recovered' in exc_info.value.detail
        handles['token_manager'].create_keycloak_user.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_keycloak_toctou_falls_through_to_idempotent(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """Late-arriving 409 from a concurrent provision falls through.

        Two admins call provision-user for the same email at the same
        time. The pre-check passes for both; the first wins the
        create, the second gets a 409 from Keycloak. The route must
        re-query, confirm the user is now there, and proceed
        idempotently — *not* surface the race as an error to the
        caller.
        """
        existing_kc_id = 'race-winner-kc-id'
        existing_oh_user = MagicMock()
        existing_oh_user.id = uuid.UUID(new_user_id)

        patches, handles = self._patch_dependencies(
            new_user_id,
            target_org_id,
            keycloak_raises=KeycloakError(
                'User exists with same username',
                response_code=409,
            ),
            # The first pre-check sees nothing; the second one (post
            # recovery) sees the just-created user.
            existing_kc_user_id=None,
            existing_oh_user=None,
            existing_org_member=MagicMock(),  # already a member
        )
        # Pre-check sees nothing first, then sees the user.
        handles['token_manager'].get_user_id_from_user_email.side_effect = [
            None,
            existing_kc_id,
        ]
        # ``UserStore.get_user_by_email`` also returns the user on
        # the second lookup — the concurrent winner already finished
        # the OpenHands-DB side too.
        handles['get_user_by_email'].side_effect = [None, existing_oh_user]
        with self._enter_all(patches):
            response, resp = await self._call(
                body=ProvisionUserRequest(email='dup@example.com'),
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )

        assert resp.action == 'reprovisioned'
        assert resp.created is False
        assert resp.password is None
        assert resp.user_id == existing_kc_id
        # 200 OK on the idempotent path; the caller did not actually
        # create a new identity.
        assert response.status_code == 200
        # The Keycloak user that "won the race" must not be deleted by
        # the rollback path on this call.
        handles['token_manager'].delete_keycloak_user.assert_not_awaited()
        handles['delete_org_cascade'].assert_not_awaited()
        handles['remove_member'].assert_not_awaited()
        # The pre-check must have been called twice — once up-front,
        # once for the recovery.
        assert handles['token_manager'].get_user_id_from_user_email.await_count == 2

    @pytest.mark.asyncio
    async def test_keycloak_toctou_partial_state_returns_409(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """Keycloak user found on re-check, but OpenHands DB row not yet written.

        Edge case of the TOCTOU race: the concurrent winner has
        committed the Keycloak user but not yet the OpenHands
        ``User`` row (the create is still mid-flight). The route
        must NOT create a duplicate OpenHands row tied to the same
        Keycloak sub; surface a 409 telling the caller to retry.
        """
        recovered_kc_id = 'race-winner-kc-id'

        patches, handles = self._patch_dependencies(
            new_user_id,
            target_org_id,
            keycloak_raises=KeycloakError(
                'User exists',
                response_code=409,
            ),
            existing_kc_user_id=None,
            existing_oh_user=None,
        )
        # First KC lookup: None. Second KC lookup (recovery): the
        # user is there. The OpenHands DB lookup returns None on
        # both calls (the winning concurrent provision is still
        # in flight on the DB side).
        handles['token_manager'].get_user_id_from_user_email.side_effect = [
            None,
            recovered_kc_id,
        ]
        handles['get_user_by_email'].return_value = None
        with self._enter_all(patches):
            with pytest.raises(HTTPException) as exc_info:
                await self._call(
                    body=ProvisionUserRequest(email='dup@example.com'),
                    caller_user_id=caller_user_id,
                    target_org_id=target_org_id,
                )

        assert exc_info.value.status_code == 409
        assert 'Retry' in exc_info.value.detail
        handles['token_manager'].delete_keycloak_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rollback_before_user_created_only_cleans_keycloak(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """Failure before ``UserStore.create_user`` only undoes Keycloak.

        The offline-token step runs *before* ``UserStore.create_user``,
        so when it blows up there are no OpenHands DB rows to
        compensate. The rollback must touch only the Keycloak user
        — exercising ``delete_org_cascade`` on a never-created
        personal org would log a misleading "not found".
        """
        patches, handles = self._patch_dependencies(new_user_id, target_org_id)
        # Make the offline-token step blow up after Keycloak succeeded
        # but before any OpenHands DB row was created.
        handles['token_manager'].request_offline_token.side_effect = RuntimeError(
            'boom'
        )

        with self._enter_all(patches):
            with pytest.raises(HTTPException) as exc_info:
                await self._call(
                    body=ProvisionUserRequest(email='bob@example.com'),
                    caller_user_id=caller_user_id,
                    target_org_id=target_org_id,
                )
        assert exc_info.value.status_code == 500
        # Only the Keycloak user gets removed; no DB rollback.
        handles['token_manager'].delete_keycloak_user.assert_awaited_once_with(
            new_user_id
        )
        handles['delete_org_cascade'].assert_not_awaited()
        handles['remove_member'].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rollback_when_set_flags_fails(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """``_set_user_provisioned_flags`` failure cascades the personal org.

        ``UserStore.create_user`` succeeded, so the User + personal Org
        + owner OrgMember + default settings now exist. The rollback
        must wipe them via ``delete_org_cascade`` and then delete the
        Keycloak user. No target-org membership was added yet, so
        ``remove_user_from_org`` must not run.
        """
        patches, handles = self._patch_dependencies(new_user_id, target_org_id)
        handles['set_flags'].side_effect = RuntimeError('flag update exploded')

        with self._enter_all(patches):
            with pytest.raises(HTTPException) as exc_info:
                await self._call(
                    body=ProvisionUserRequest(email='bob@example.com'),
                    caller_user_id=caller_user_id,
                    target_org_id=target_org_id,
                )

        assert exc_info.value.status_code == 500
        handles['remove_member'].assert_not_awaited()
        handles['delete_org_cascade'].assert_awaited_once_with(
            uuid.UUID(new_user_id), requester_user_id=new_user_id
        )
        handles['token_manager'].delete_keycloak_user.assert_awaited_once_with(
            new_user_id
        )

    @pytest.mark.asyncio
    async def test_rollback_when_litellm_integration_fails(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """Failure in ``create_litellm_integration`` cascades personal org.

        We have a User + personal org but no target-org membership
        yet, so the rollback should skip ``remove_user_from_org`` and
        only cascade the personal org before deleting Keycloak.
        """
        patches, handles = self._patch_dependencies(new_user_id, target_org_id)

        with self._enter_all(patches):
            with patch(
                'server.routes.user_provisioning.OrgService.create_litellm_integration',
                new_callable=AsyncMock,
                side_effect=RuntimeError('litellm down'),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await self._call(
                        body=ProvisionUserRequest(email='bob@example.com'),
                        caller_user_id=caller_user_id,
                        target_org_id=target_org_id,
                    )

        assert exc_info.value.status_code == 500
        handles['remove_member'].assert_not_awaited()
        handles['delete_org_cascade'].assert_awaited_once_with(
            uuid.UUID(new_user_id), requester_user_id=new_user_id
        )
        handles['token_manager'].delete_keycloak_user.assert_awaited_once_with(
            new_user_id
        )

    @pytest.mark.asyncio
    async def test_rollback_when_add_user_to_org_fails(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """``add_user_to_org`` failure: same shape as litellm failure.

        ``add_user_to_org`` is the call that sets
        ``target_membership_added`` after returning. If it raises
        instead, the membership row was never inserted, so the
        rollback must NOT call ``remove_user_from_org`` — that would
        try to remove a row that does not exist.
        """
        patches, handles = self._patch_dependencies(new_user_id, target_org_id)
        handles['add_user_to_org'].side_effect = RuntimeError('insert exploded')

        with self._enter_all(patches):
            with pytest.raises(HTTPException) as exc_info:
                await self._call(
                    body=ProvisionUserRequest(email='bob@example.com'),
                    caller_user_id=caller_user_id,
                    target_org_id=target_org_id,
                )

        assert exc_info.value.status_code == 500
        handles['remove_member'].assert_not_awaited()
        handles['delete_org_cascade'].assert_awaited_once_with(
            uuid.UUID(new_user_id), requester_user_id=new_user_id
        )
        handles['token_manager'].delete_keycloak_user.assert_awaited_once_with(
            new_user_id
        )

    @pytest.mark.asyncio
    async def test_rollback_when_api_key_creation_fails(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """API-key failure exercises the *full* rollback path.

        By the time ``create_api_key`` runs, the target-org membership
        has already been inserted, so the rollback must:

        1. Remove the target-org ``OrgMember`` row, *before* calling
           ``delete_org_cascade`` — otherwise the cascade would only
           reassign ``current_org_id`` and leave the User row behind.
        2. Cascade-delete the personal org (User + personal Org +
           settings + personal-org LiteLLM team).
        3. Delete the Keycloak user.

        We also assert step ordering by inspecting ``mock_calls`` on
        a shared parent mock.
        """
        patches, handles = self._patch_dependencies(new_user_id, target_org_id)
        handles['api_key_store'].create_api_key.side_effect = RuntimeError(
            'api key insert exploded'
        )

        # Shared parent mock to assert call ordering across the three
        # rollback collaborators.
        order_tracker = MagicMock()
        order_tracker.attach_mock(handles['remove_member'], 'remove_member')
        order_tracker.attach_mock(handles['delete_org_cascade'], 'delete_org_cascade')
        order_tracker.attach_mock(
            handles['token_manager'].delete_keycloak_user,
            'delete_keycloak_user',
        )

        with self._enter_all(patches):
            with pytest.raises(HTTPException) as exc_info:
                await self._call(
                    body=ProvisionUserRequest(email='bob@example.com'),
                    caller_user_id=caller_user_id,
                    target_org_id=target_org_id,
                )

        assert exc_info.value.status_code == 500
        handles['remove_member'].assert_awaited_once_with(
            target_org_id, uuid.UUID(new_user_id)
        )
        handles['delete_org_cascade'].assert_awaited_once_with(
            uuid.UUID(new_user_id), requester_user_id=new_user_id
        )
        handles['token_manager'].delete_keycloak_user.assert_awaited_once_with(
            new_user_id
        )
        # Ordering: target-membership ➜ personal-org cascade ➜ Keycloak.
        call_names = [c[0] for c in order_tracker.mock_calls]
        assert call_names == [
            'remove_member',
            'delete_org_cascade',
            'delete_keycloak_user',
        ]

    @pytest.mark.asyncio
    async def test_rollback_swallows_secondary_failures(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """Each cleanup step is wrapped so secondary errors do not mask the original.

        If ``remove_user_from_org`` *and* ``delete_org_cascade`` both
        raise during rollback, the route must still surface the
        original ``HTTPException(500)`` from the provisioning
        failure, and the remaining cleanup steps must keep running.
        """
        patches, handles = self._patch_dependencies(new_user_id, target_org_id)
        handles['api_key_store'].create_api_key.side_effect = RuntimeError(
            'api key insert exploded'
        )
        handles['remove_member'].side_effect = RuntimeError(
            'rollback step 1 also failed'
        )
        handles['delete_org_cascade'].side_effect = RuntimeError(
            'rollback step 2 also failed'
        )

        with self._enter_all(patches):
            with pytest.raises(HTTPException) as exc_info:
                await self._call(
                    body=ProvisionUserRequest(email='bob@example.com'),
                    caller_user_id=caller_user_id,
                    target_org_id=target_org_id,
                )

        assert exc_info.value.status_code == 500
        # Despite earlier rollback failures, the Keycloak deletion
        # must still be attempted so the upstream identity does not
        # outlive the failed provisioning attempt.
        handles['token_manager'].delete_keycloak_user.assert_awaited_once_with(
            new_user_id
        )

    @pytest.mark.asyncio
    async def test_skips_add_to_org_when_target_is_personal_org(
        self, caller_user_id, target_org_id, new_user_id
    ):
        # When the X-Org-Id matches the user's freshly-created personal
        # org (id == user_id), re-adding would violate the unique
        # constraint. The route must skip the explicit add.
        personal_org_id = uuid.UUID(new_user_id)
        patches, handles = self._patch_dependencies(new_user_id, personal_org_id)
        with self._enter_all(patches):
            await self._call(
                body=ProvisionUserRequest(email='solo@example.com'),
                caller_user_id=caller_user_id,
                target_org_id=personal_org_id,
            )
            handles['add_user_to_org'].assert_not_awaited()

    # ------------------------------------------------------------------
    # OHE-2980 recovery paths: idempotent re-provision and the
    # corresponding split-state / recover-branch handling. These
    # mirror the case branches documented in the module docstring.
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_idempotent_reprovision_returns_existing_api_key(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """Re-running with an existing KC user + User row is a no-op.

        No Keycloak create, no offline-token refresh, no ``UserStore.
        create_user``, no target-org membership insert. The route
        returns 200 OK with ``action='reprovisioned'`` and the same
        plaintext API key as before — never the regenerated default
        mock.
        """
        existing_kc_id = 'kc-already-there'
        existing_oh_user = MagicMock()
        existing_oh_user.id = uuid.UUID(new_user_id)
        existing_member = MagicMock()
        existing_api_key = 'sk-oh-already-issued'

        patches, handles = self._patch_dependencies(
            new_user_id,
            target_org_id,
            existing_kc_user_id=existing_kc_id,
            existing_oh_user=existing_oh_user,
            existing_org_member=existing_member,
            existing_api_key=existing_api_key,
        )
        with self._enter_all(patches):
            response, resp = await self._call(
                body=ProvisionUserRequest(email='alice@example.com'),
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )

        assert resp.action == 'reprovisioned'
        assert resp.created is False
        assert resp.user_id == existing_kc_id
        assert resp.password is None  # never rotated on re-provision
        assert resp.api_key == existing_api_key
        assert response.status_code == 200

        # No write-side effects.
        handles['token_manager'].create_keycloak_user.assert_not_awaited()
        handles['token_manager'].request_offline_token.assert_not_awaited()
        handles['token_manager'].store_offline_token.assert_not_awaited()
        handles['set_flags'].assert_not_awaited()
        handles['add_user_to_org'].assert_not_awaited()
        # API key was *not* recreated; we returned the cached one.
        handles['api_key_store'].create_api_key.assert_not_awaited()
        handles['api_key_store'].delete_api_key_by_name.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_idempotent_reprovision_adds_to_existing_api_key(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """Idempotent re-provision: member exists but API key does not.

        The caller is trying to mint an API key for a user who
        already has an OpenHands + Keycloak identity in this org but
        for some reason the API key row is missing (the most likely
        scenario in the OEM partner incident: an earlier partial
        provision). The route must add the membership (already there
        — no-op) and *create* the missing API key, without touching
        the Keycloak identity or offline token.
        """
        existing_kc_id = 'kc-already-there'
        existing_oh_user = MagicMock()
        existing_oh_user.id = uuid.UUID(new_user_id)

        patches, handles = self._patch_dependencies(
            new_user_id,
            target_org_id,
            existing_kc_user_id=existing_kc_id,
            existing_oh_user=existing_oh_user,
            existing_org_member=MagicMock(),
            existing_api_key=None,  # <-- the missing piece
        )
        with self._enter_all(patches):
            response, resp = await self._call(
                body=ProvisionUserRequest(email='alice@example.com'),
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )

        assert resp.action == 'reprovisioned'
        assert response.status_code == 200
        handles['token_manager'].create_keycloak_user.assert_not_awaited()
        handles['api_key_store'].create_api_key.assert_awaited_once_with(
            user_id=existing_kc_id,
            name='Initial API Key',
            org_id=target_org_id,
        )

    @pytest.mark.asyncio
    async def test_recover_branch_creates_user_row_for_existing_keycloak_user(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """Keycloak user exists, OpenHands ``User`` row does not.

        Classic OEM recovery scenario: the original provision got
        far enough to create the Keycloak identity but the OpenHands
        DB side never finished. Re-running should detect the
        half-state, skip the Keycloak create, run ``UserStore.
        create_user`` to attach the OpenHands row to the existing
        Keycloak id, and proceed normally.
        """
        existing_kc_id = 'kc-already-there'

        patches, handles = self._patch_dependencies(
            new_user_id,
            target_org_id,
            existing_kc_user_id=existing_kc_id,
            existing_oh_user=None,  # no DB row yet
            existing_org_member=MagicMock(),
        )
        with self._enter_all(patches):
            response, resp = await self._call(
                body=ProvisionUserRequest(email='alice@example.com'),
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )

        # Recover path — no password rotation, no Keycloak create.
        assert resp.user_id == existing_kc_id
        assert resp.password is None
        handles['token_manager'].create_keycloak_user.assert_not_awaited()
        # ``_set_user_provisioned_flags`` runs so the user is not
        # bounced through TOS / verification interstitials.
        handles['set_flags'].assert_awaited_once_with(existing_kc_id)
        # Member row already existed in this scenario.
        handles['add_user_to_org'].assert_not_awaited()
        # Since membership pre-existed, action is "reprovisioned", not
        # "added_to_org" — the OEM partner re-running the call is
        # treated as a recovery, not as an active re-add.
        assert resp.action == 'reprovisioned'
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_split_state_db_only_returns_409(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """OpenHands DB row exists but no Keycloak user — refuse to repair.

        ``UserStore.create_user`` is idempotent on the Keycloak sub,
        so silently re-creating the Keycloak identity would leave the
        OpenHands DB row orphaned (its ``id`` would no longer match
        any Keycloak ``sub``). Surface as a 409 with enough detail
        for an operator to repair by hand.
        """
        existing_oh_user = MagicMock()
        existing_oh_user.id = uuid.UUID(new_user_id)

        patches, handles = self._patch_dependencies(
            new_user_id,
            target_org_id,
            existing_kc_user_id=None,
            existing_oh_user=existing_oh_user,
        )
        with self._enter_all(patches):
            with pytest.raises(HTTPException) as exc_info:
                await self._call(
                    body=ProvisionUserRequest(email='alice@example.com'),
                    caller_user_id=caller_user_id,
                    target_org_id=target_org_id,
                )

        assert exc_info.value.status_code == 409
        assert 'Manual Keycloak intervention' in exc_info.value.detail
        # Must not have tried to mutate Keycloak on a split state.
        handles['token_manager'].create_keycloak_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reissue_api_key_deletes_existing_then_recreates(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """``reissue_api_key=True`` deletes the old key before minting.

        Default is idempotent (return existing key); the caller opts
        into a fresh one by setting ``reissue_api_key=True``. The
        old key row is deleted first so the ``create`` step does
        not collide with a uniqueness constraint, and a brand-new
        plaintext value is returned in the response.
        """
        existing_kc_id = 'kc-already-there'
        existing_oh_user = MagicMock()
        existing_oh_user.id = uuid.UUID(new_user_id)
        existing_api_key = 'sk-oh-original'

        patches, handles = self._patch_dependencies(
            new_user_id,
            target_org_id,
            existing_kc_user_id=existing_kc_id,
            existing_oh_user=existing_oh_user,
            existing_org_member=MagicMock(),
            existing_api_key=existing_api_key,
        )
        with self._enter_all(patches):
            _, resp = await self._call(
                body=ProvisionUserRequest(
                    email='alice@example.com',
                    reissue_api_key=True,
                ),
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )

        # Brand-new key, not the cached one.
        assert resp.api_key == 'sk-oh-generated-api-key'
        # Delete ran first with the right scoping.
        handles['api_key_store'].delete_api_key_by_name.assert_awaited_once_with(
            user_id=existing_kc_id,
            name='Initial API Key',
            org_id=target_org_id,
        )
        # Then create ran.
        handles['api_key_store'].create_api_key.assert_awaited_once_with(
            user_id=existing_kc_id,
            name='Initial API Key',
            org_id=target_org_id,
        )

    @pytest.mark.asyncio
    async def test_reissue_api_key_without_existing_key_just_creates(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """``reissue_api_key=True`` with no existing key is a no-op delete.

        If the user has no API key by that name, the lookup returns
        ``None`` and we skip straight to the create step — there is
        nothing to delete. Documented here so the behaviour does not
        surprise future maintainers.
        """
        existing_kc_id = 'kc-already-there'
        existing_oh_user = MagicMock()
        existing_oh_user.id = uuid.UUID(new_user_id)

        patches, handles = self._patch_dependencies(
            new_user_id,
            target_org_id,
            existing_kc_user_id=existing_kc_id,
            existing_oh_user=existing_oh_user,
            existing_org_member=MagicMock(),
            existing_api_key=None,
        )
        with self._enter_all(patches):
            _, resp = await self._call(
                body=ProvisionUserRequest(
                    email='alice@example.com',
                    reissue_api_key=True,
                ),
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )

        assert resp.api_key == 'sk-oh-generated-api-key'
        handles['api_key_store'].delete_api_key_by_name.assert_not_awaited()
        handles['api_key_store'].create_api_key.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_idempotent_reprovision_into_already_membered_org(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """Member already exists: return 200 reprovisioned, skip add.

        The OEM partner's recovery flow: existing user is *already*
        a member of the org they keep provisioning into. The route
        must not attempt to insert a duplicate ``OrgMember`` row,
        which would violate the ``(org_id, user_id)`` unique
        constraint.
        """
        existing_kc_id = 'kc-already-there'
        existing_oh_user = MagicMock()
        existing_oh_user.id = uuid.UUID(new_user_id)

        patches, handles = self._patch_dependencies(
            new_user_id,
            target_org_id,
            existing_kc_user_id=existing_kc_id,
            existing_oh_user=existing_oh_user,
            existing_org_member=MagicMock(),
            existing_api_key=None,
        )
        with self._enter_all(patches):
            response, resp = await self._call(
                body=ProvisionUserRequest(email='alice@example.com'),
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )

        assert resp.action == 'reprovisioned'
        assert response.status_code == 200
        handles['add_user_to_org'].assert_not_awaited()
        # API key still gets minted since one was missing.
        handles['api_key_store'].create_api_key.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_added_to_org_action_when_member_did_not_exist(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """Recover path that *did* add a membership returns added_to_org.

        Case c with no pre-existing ``OrgMember`` row: the user gets
        added to the target org on this call, so the response is
        ``added_to_org`` (200 OK), not ``created`` (201 Created) —
        distinguishing from a true first-time provision that the
        caller can use to track "this is a recovery, not a create".
        """
        existing_kc_id = 'kc-already-there'
        existing_oh_user = MagicMock()
        existing_oh_user.id = uuid.UUID(new_user_id)

        patches, handles = self._patch_dependencies(
            new_user_id,
            target_org_id,
            existing_kc_user_id=existing_kc_id,
            existing_oh_user=existing_oh_user,
            existing_org_member=None,  # <-- need to add
        )
        with self._enter_all(patches):
            response, resp = await self._call(
                body=ProvisionUserRequest(email='alice@example.com'),
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )

        assert resp.action == 'added_to_org'
        assert resp.created is False
        assert response.status_code == 200
        handles['add_user_to_org'].assert_awaited_once()

    def test_default_role_is_member(self):
        # Document the policy: provisioned users are not auto-promoted.
        assert DEFAULT_PROVISIONED_ROLE == 'member'

    # --- OHE-2980: per-email locking for concurrent provision-user ---
    #
    # ``provision_user`` now wraps its body in
    # ``UserStore.acquire_user_creation_lock(email)`` /
    # ``release_user_creation_lock(email)`` so two callers hitting
    # the endpoint with the same email cannot race past the
    # pre-check and produce two identities (or two API keys) for
    # one logical user. These tests cover (a) the lock is acquired
    # and released under the happy path, and (b) a concurrent
    # caller is serialized through the same lock and observes the
    # idempotent branch.

    @pytest.mark.asyncio
    async def test_lock_acquired_and_released_on_happy_path(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """Verify the lock is taken with the email as the resource id
        and released after a successful create."""
        patches, handles = self._patch_dependencies(new_user_id, target_org_id)
        with self._enter_all(patches):
            # Capture the patched-in mocks *before* calling the route
            # so we can assert on them after the request finishes.
            acquire_mock = UserStore.acquire_user_creation_lock
            release_mock = UserStore.release_user_creation_lock
            _, resp = await self._call(
                body=ProvisionUserRequest(
                    email='Alice@Example.com',
                    password='SuperSecret-1234',
                ),
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )
            assert resp.action == 'created'
            # Exactly one acquire / release round per request.
            acquire_mock.assert_awaited_once()
            release_mock.assert_awaited_once()
            # Both helpers see the normalized email so two callers with
            # different casings share the same lock key.
            assert acquire_mock.await_args.args == ('alice@example.com',)
            assert release_mock.await_args.args == ('alice@example.com',)

    @pytest.mark.asyncio
    async def test_lock_released_even_on_failure(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """A failure in Keycloak create must still release the lock."""
        patches, handles = self._patch_dependencies(
            new_user_id,
            target_org_id,
            keycloak_raises=KeycloakError('boom'),
        )
        with self._enter_all(patches):
            release_mock = UserStore.release_user_creation_lock
            with pytest.raises(HTTPException):
                await self._call(
                    body=ProvisionUserRequest(email='fail@example.com'),
                    caller_user_id=caller_user_id,
                    target_org_id=target_org_id,
                )
            release_mock.assert_awaited_once_with('fail@example.com')

    @pytest.mark.asyncio
    async def test_concurrent_provision_user_calls_share_lock_and_return_same_api_key(
        self, caller_user_id, target_org_id, new_user_id
    ):
        """Two simultaneous callers for the same email must be
        serialized: the first runs the create path, the second waits
        for the lock, re-runs the pre-check, and lands in the
        idempotent branch — both return the same API key.

        The test injects a contention-aware ``acquire_user_creation_lock``
        mock that returns ``False`` once (forces the second caller to
        sleep) before granting the lock, then asserts both callers see
        the same final API key and one of them went through the create
        branch while the other took the idempotent branch.
        """
        # State shared between the two in-flight callers:
        # - ``create_already_observed``: flips to True after the first
        #   caller's ``create_keycloak_user`` so the second caller's
        #   ``get_user_id_from_user_email`` returns the existing id.
        # - ``acquire_calls``: tracks how many times the lock was
        #   acquired so we can drive the contention path.
        create_already_observed = False
        acquire_calls = 0

        async def acquire_side_effect(resource_id):
            nonlocal acquire_calls
            acquire_calls += 1
            # Caller A's first acquire is granted immediately so it
            # can do the create work. The second acquire — the one
            # issued by caller B — is rejected once to simulate the
            # contention that the Redis lock is supposed to catch,
            # forcing B through the while-loop retry path. B's retry
            # (third acquire overall) is granted because by then A
            # has released the lock.
            return acquire_calls != 2

        async def get_user_id_side_effect(_email):
            # Once caller A's Keycloak create has finished, caller B
            # observes the same Keycloak user, so the pre-check
            # resolves to the idempotent branch.
            return new_user_id if create_already_observed else None

        async def create_keycloak_user_side_effect(*args, **kwargs):
            nonlocal create_already_observed
            create_already_observed = True
            return new_user_id

        token_manager_mock = MagicMock()
        token_manager_mock.create_keycloak_user = AsyncMock(
            side_effect=create_keycloak_user_side_effect,
        )
        token_manager_mock.request_offline_token = AsyncMock(
            return_value='offline-refresh-token'
        )
        token_manager_mock.store_offline_token = AsyncMock()
        token_manager_mock.delete_keycloak_user = AsyncMock(return_value=True)
        token_manager_mock.get_user_id_from_user_email = AsyncMock(
            side_effect=get_user_id_side_effect,
        )

        new_user = MagicMock()
        new_user.id = uuid.UUID(new_user_id)

        api_key_store_mock = MagicMock()
        api_key_store_mock.create_api_key = AsyncMock(
            return_value='sk-oh-generated-api-key'
        )
        api_key_store_mock.retrieve_api_key_by_name = AsyncMock(return_value=None)
        api_key_store_mock.delete_api_key_by_name = AsyncMock(return_value=True)

        role_mock = MagicMock()
        role_mock.id = 42

        # Stub the time.sleep inside the lock-retry loop so the test
        # does not actually wait. We patch asyncio.sleep *only* within
        # the user_provisioning module to avoid clobbering other
        # sleeps in the test runner.
        sleep_mock = AsyncMock()

        patches = [
            patch(
                'server.routes.user_provisioning.TokenManager',
                return_value=token_manager_mock,
            ),
            patch(
                'server.routes.user_provisioning.OrgStore.get_org_by_id',
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch(
                'server.routes.user_provisioning.UserStore.create_user',
                new_callable=AsyncMock,
                return_value=new_user,
            ),
            patch(
                'server.routes.user_provisioning.UserStore.get_user_by_email',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.user_provisioning._set_user_provisioned_flags',
                new_callable=AsyncMock,
            ),
            patch(
                'server.routes.user_provisioning.OrgService.create_litellm_integration',
                new_callable=AsyncMock,
                return_value=MagicMock(
                    agent_settings=MagicMock(llm=MagicMock(api_key=SecretStr('k')))
                ),
            ),
            patch(
                'server.routes.user_provisioning.RoleStore.get_role_by_name',
                new_callable=AsyncMock,
                return_value=role_mock,
            ),
            patch(
                'server.routes.user_provisioning.OrgMemberStore.add_user_to_org',
                new_callable=AsyncMock,
            ),
            patch(
                'server.routes.user_provisioning.OrgMemberStore.get_org_member',
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                'server.routes.user_provisioning.ApiKeyStore.get_instance',
                return_value=api_key_store_mock,
            ),
            patch(
                'server.routes.user_provisioning.OrgMemberStore.remove_user_from_org',
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                'server.routes.user_provisioning.OrgStore.delete_org_cascade',
                new_callable=AsyncMock,
            ),
            patch.object(
                UserStore,
                'acquire_user_creation_lock',
                side_effect=acquire_side_effect,
            ),
            patch.object(
                UserStore,
                'release_user_creation_lock',
                new_callable=AsyncMock,
            ),
            patch(
                'server.routes.user_provisioning.asyncio.sleep',
                sleep_mock,
            ),
        ]
        with self._enter_all(patches):
            acquire_mock = UserStore.acquire_user_creation_lock
            release_mock = UserStore.release_user_creation_lock
            body = ProvisionUserRequest(
                email='race@example.com',
                password='SuperSecret-1234',
            )
            response_a, resp_a = await self._call(
                body=body,
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )
            response_b, resp_b = await self._call(
                body=body,
                caller_user_id=caller_user_id,
                target_org_id=target_org_id,
            )

            # Same email -> same lock key -> same API key returned for
            # both callers, regardless of who created and who re-provisioned.
            assert resp_a.api_key == resp_b.api_key == 'sk-oh-generated-api-key'
            assert resp_a.user_id == resp_b.user_id == new_user_id
            # Exactly one caller went through the create branch (201);
            # the other landed in the idempotent branch (200).
            statuses = sorted([response_a.status_code, response_b.status_code])
            assert statuses == [200, 201]
            actions = sorted([resp_a.action, resp_b.action])
            # The second caller is technically an ``added_to_org`` (or
            # ``reprovisioned``) action — accept either since the test
            # only exercises that one of them created.
            assert 'created' in actions
            # Lock helpers: caller A acquired once, caller B
            # acquired twice (initial fail + retry). Releases match
            # acquires per-request (one each).
            assert acquire_mock.await_count == 3
            assert release_mock.await_count == 2
            # The retry path went through asyncio.sleep at least once.
            sleep_mock.assert_awaited()
