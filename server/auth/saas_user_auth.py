from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import SecretStr

from openhands.app_server.integrations.provider import (
    PROVIDER_TOKEN_TYPE,
    CustomSecret,
)
from openhands.app_server.integrations.service_types import ProviderType, UserGitInfo
from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.settings.settings_store import SettingsStore
from openhands.app_server.user_auth.user_auth import AuthType, UserAuth
from server.auth.auth_error import (
    NoCredentialsError,
)
from server.auth.authorization import (
    get_role_permissions,
    get_user_org_role,
    get_user_super_role,
)
from server.auth.composition import get_auth_services
from server.auth.token_manager import TokenManager
from server.logger import logger
from server.rate_limit import RateLimiter, create_redis_rate_limiter
from server.utils.rate_limit_utils import RATE_LIMIT_AUTH_WINDOWS
from storage.api_key_store import ApiKeyStore
from storage.org_store import OrgStore
from storage.saas_secrets_store import SaasSecretsStore
from storage.saas_settings_store import SaasSettingsStore

token_manager = TokenManager()


rate_limiter: RateLimiter | None = create_redis_rate_limiter(RATE_LIMIT_AUTH_WINDOWS)


@dataclass
class SaasUserAuth(UserAuth):
    refresh_token: SecretStr
    user_id: str
    email: str | None = None
    email_verified: bool | None = None
    access_token: SecretStr | None = None
    provider_tokens: PROVIDER_TOKEN_TYPE | None = None
    refreshed: bool = False
    settings_store: SaasSettingsStore | None = None
    secrets_store: SaasSecretsStore | None = None
    _settings: Settings | None = None
    _resolved_settings: Settings | None = None
    _secrets: Secrets | None = None
    accepted_tos: bool | None = None
    auth_type: AuthType = AuthType.COOKIE
    # AuthType describes the credential, while transport governs CSRF policy.
    # In particular, an API key supplied in a cookie is not bearer transport.
    credential_transport: str = 'keycloak_cookie'
    # API key context fields - populated when authenticated via API key
    api_key_org_id: UUID | None = None  # Org bound to the API key used for auth
    api_key_id: int | None = None
    api_key_name: str | None = None
    # Organization context fields - populated lazily via get_org_info()
    _org_id: str | None = None
    _org_name: str | None = None
    _role: str | None = None
    _permissions: list[str] | None = None
    _org_info_loaded: bool = False
    # Per-request `X-Org-Id` header (raw, unvalidated); see
    # `server/auth/org_context.py` for resolution rules.
    _x_org_id_header: str | None = None
    # Trusted server-side override used by background resolver contexts after
    # they have already resolved and membership-checked the target org.
    effective_org_id_override: UUID | None = None
    # Cached result of `get_effective_org_id()`. The `_resolved` flag is
    # needed to distinguish "not yet computed" from "computed and None".
    _effective_org_id: UUID | None = None
    _effective_org_id_resolved: bool = False

    def get_api_key_org_id(self) -> UUID | None:
        """Get the organization ID bound to the API key used for authentication.

        Returns:
            The org_id if authenticated via an API key with an explicit org
            binding; ``None`` for cookie auth or for *unbound* API keys (in
            which case the request's effective org is resolved per-request
            via the ``X-Org-Id`` header or, as a fallback, the caller's
            ``user.current_org_id`` -- see :meth:`_resolve_org_id`).
        """
        return self.api_key_org_id

    def set_effective_org_id_override(self, org_id: UUID | None) -> None:
        """Set a trusted server-side org override and clear org-scoped caches."""
        self.effective_org_id_override = org_id
        self._clear_org_scoped_caches()

    def _clear_org_scoped_caches(self) -> None:
        """Clear cached data that depends on the effective organization."""
        self._effective_org_id = None
        self._effective_org_id_resolved = False
        self.settings_store = None
        self.secrets_store = None
        self._settings = None
        # Org-scoped like _settings: the resolved launch view carries the
        # referenced LLM key + ref-filtered mcp_config of *this* org's active
        # profile, so it must not survive a re-scope to another org.
        self._resolved_settings = None
        self._secrets = None
        self.provider_tokens = None
        self._org_id = None
        self._org_name = None
        self._role = None
        self._permissions = None
        self._org_info_loaded = False

    async def _resolve_and_verify_override_org(self) -> UUID | None:
        """Verify and return the trusted resolver org override, if present."""
        if self.effective_org_id_override is None:
            return None

        # Import locally to avoid a circular import via authorization.py.
        from fastapi import status

        from storage.org_member_store import OrgMemberStore

        override_org_id = self.effective_org_id_override
        if self.api_key_org_id is not None and self.api_key_org_id != override_org_id:
            logger.warning(
                'effective_org_id_override_api_key_mismatch',
                extra={
                    'user_id': self.user_id,
                    'api_key_org_id': str(self.api_key_org_id),
                    'effective_org_id_override': str(override_org_id),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail='API key is not authorized for this organization',
            )
        try:
            user_uuid = UUID(self.user_id)
        except ValueError as exc:
            logger.exception(
                'effective_org_id_override_invalid_user_id',
                extra={'user_id': self.user_id},
                stack_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail='User is not a member of the requested organization',
            ) from exc

        member = await OrgMemberStore.get_org_member(override_org_id, user_uuid)
        if member is None:
            logger.warning(
                'effective_org_id_override_not_a_member',
                extra={
                    'user_id': self.user_id,
                    'effective_org_id_override': str(override_org_id),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail='User is not a member of the requested organization',
            )
        return override_org_id

    async def _resolve_org_id(self, *, verify_membership: bool) -> UUID | None:
        """Shared resolver for :meth:`get_effective_org_id` and
        :meth:`get_target_org_id_for_permission_check`.

        Precedence (highest first):

        1. ``effective_org_id_override`` (trusted server-side resolver
           context; always membership-checked by
           :meth:`_resolve_and_verify_override_org`). The membership
           check is intentional defense-in-depth for resolver code that
           sets the override; it is not relaxed for super-role users.
        2. ``api_key_org_id`` if the request authenticated with an
           org-bound API key. An ``X-Org-Id`` header that disagrees with
           the API key org raises 403.
        3. ``X-Org-Id`` header. Validated as a UUID; the API-key/header
           conflict above takes precedence. When ``verify_membership``
           is ``True`` the user must be a member of the requested org,
           **or** have a "super" role assigned via ``user.role_id``
           (403 otherwise). When ``False`` the org id is returned
           verbatim -- the caller is responsible for the access check
           (used by ``require_permission`` to allow "super" roles to
           target non-member orgs).
        4. ``user.current_org_id`` as a default.

        Note on the super-role bypass in case 3: routes that declare
        both ``Depends(require_permission(...))`` and ``EFFECTIVE_ORG_ID``
        would otherwise be inconsistent for non-member super users --
        ``require_permission`` would grant access via the super-role
        fallback but ``EFFECTIVE_ORG_ID`` would 403 the same request at
        the membership check here. This resolver only relaxes the
        coarse "must be a member" gate when a super role is present;
        the route's ``require_permission`` dependency is still the
        authoritative check for the specific permission needed.

        Raises:
            HTTPException: 400 for a malformed ``X-Org-Id`` header,
                403 for API-key / membership conflicts.
        """
        from fastapi import status

        override_org_id = await self._resolve_and_verify_override_org()
        if override_org_id is not None:
            return override_org_id

        header_value = self._x_org_id_header
        requested: UUID | None = None
        if header_value:
            try:
                requested = UUID(header_value)
            except ValueError as exc:
                logger.warning(
                    'x_org_id_invalid',
                    extra={'user_id': self.user_id, 'header': header_value},
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail='Invalid X-Org-Id header (must be a UUID)',
                ) from exc

        # Case 1: API key binds the org.
        if self.api_key_org_id is not None:
            if requested is not None and requested != self.api_key_org_id:
                logger.warning(
                    'x_org_id_api_key_mismatch',
                    extra={
                        'user_id': self.user_id,
                        'api_key_org_id': str(self.api_key_org_id),
                        'x_org_id': str(requested),
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail='API key is not authorized for this organization',
                )
            return self.api_key_org_id

        # Case 2: X-Org-Id override.
        if requested is not None:
            if not verify_membership:
                # ``require_permission`` will run the org-role + super-role
                # fallback against this org id and decide.
                return requested

            from storage.org_member_store import OrgMemberStore

            try:
                user_uuid = UUID(self.user_id)
            except ValueError as exc:
                # Shouldn't happen, but treat as not-a-member.
                logger.exception(
                    'x_org_id_invalid_user_id',
                    extra={'user_id': self.user_id},
                    stack_info=True,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail='User is not a member of the requested organization',
                ) from exc
            member = await OrgMemberStore.get_org_member(requested, user_uuid)
            if member is None:
                # Super-role bypass: a user with a cross-org "super"
                # role (``user.role_id``) is allowed to target an org
                # they have not joined. The route's
                # ``require_permission`` dependency still gates the
                # specific permission against this org id, so this only
                # relaxes the coarse membership gate -- it does not
                # grant access by itself.
                super_role = await get_user_super_role(self.user_id)
                if super_role is not None:
                    logger.debug(
                        'x_org_id_super_role_bypass',
                        extra={
                            'user_id': self.user_id,
                            'x_org_id': str(requested),
                            'super_role': super_role.name,
                        },
                    )
                    return requested

                logger.warning(
                    'x_org_id_not_a_member',
                    extra={
                        'user_id': self.user_id,
                        'x_org_id': str(requested),
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail='User is not a member of the requested organization',
                )
            return requested

        # Case 3: Fall back to the user's currently-selected org.
        user = await get_auth_services().accounts.get_user_by_id(self.user_id)
        if user is None:
            return None
        return user.current_org_id

    async def get_effective_org_id(self) -> UUID | None:
        """Resolve the effective organization ID for this request.

        Delegates to :meth:`_resolve_org_id` with ``verify_membership=True``
        and caches the result on the auth instance for the rest of the
        request. See the helper for the full precedence rules.

        Raises:
            HTTPException: 400 for a malformed header, 403 for
                membership / API-key conflicts.
        """
        if self._effective_org_id_resolved:
            return self._effective_org_id

        self._effective_org_id = await self._resolve_org_id(verify_membership=True)
        self._effective_org_id_resolved = True
        return self._effective_org_id

    async def get_target_org_id_for_permission_check(self) -> UUID | None:
        """Resolve the target organization for a permission check
        **without** requiring the authenticated user to be a member.

        Delegates to :meth:`_resolve_org_id` with ``verify_membership=False``.
        Used by ``require_permission`` on routes that lack an explicit
        ``{org_id}`` path parameter so that "super" role users (assigned
        via ``user.role_id``) can target orgs they have not joined. The
        result is **not** cached -- callers should treat it as
        request-scoped and call only once per request.

        Raises:
            HTTPException: 400 for a malformed ``X-Org-Id`` header,
                403 for API-key / header conflicts.
        """
        return await self._resolve_org_id(verify_membership=False)

    async def get_user_id(self) -> str | None:
        return self.user_id

    async def get_user_email(self) -> str | None:
        # Email lives in the local DB (User row), not only in the Keycloak
        # token. Sourcing it here lets API-key (bearer) auth — which no longer
        # refreshes against Keycloak — still resolve the email. Cookie auth
        # already has ``self.email`` set from the signed token, so the lookup
        # is skipped in that case.
        if self.email is None:
            user = await get_auth_services().accounts.get_user_by_id(self.user_id)
            if user:
                self.email = user.email
                self.email_verified = user.email_verified
        return self.email

    async def refresh(self) -> None:
        await get_auth_services().requests.refresh(self)

    def _is_token_expired(self, token: SecretStr) -> bool:
        return get_auth_services().requests.token_expired(self, token)

    def get_auth_type(self) -> AuthType | None:
        return self.auth_type

    async def get_user_settings(
        self,
        *,
        resolve_agent_profile: bool = False,
        override_agent_profile_id: str | None = None,
    ) -> Settings | None:
        if resolve_agent_profile or override_agent_profile_id is not None:
            # Effective launch view (active Agent Profile resolved). Memoized
            # separately from the persisted `_settings`; an explicit override
            # is a one-off and is never memoized.
            if override_agent_profile_id is None and self._resolved_settings:
                return self._resolved_settings
            settings_store = await self.get_user_settings_store()
            settings = await settings_store.load(
                resolve_agent_profile=True,
                override_agent_profile_id=override_agent_profile_id,
            )
            if settings:
                settings.email = await self.get_user_email()
                settings.email_verified = self.email_verified
                if override_agent_profile_id is None:
                    self._resolved_settings = settings
            return settings
        settings = self._settings
        if settings:
            return settings
        settings_store = await self.get_user_settings_store()
        settings = await settings_store.load()
        if settings:
            # Resolve email via get_user_email() so it is populated (from the DB)
            # for bearer auth, which no longer eagerly refreshes from Keycloak.
            settings.email = await self.get_user_email()
            settings.email_verified = self.email_verified
            self._settings = settings
        return settings

    async def get_secrets_store(self) -> SaasSecretsStore:
        logger.debug('saas_user_auth_get_secrets_store')
        secrets_store = self.secrets_store
        if secrets_store:
            return secrets_store
        # Scope secrets to the request's effective org so that callers
        # using an API key bound to org A — or supplying an X-Org-Id
        # header — read/write secrets under that org, not under whatever
        # `user.current_org_id` happens to point at.
        effective_org_id = await self.get_effective_org_id()
        secrets_store = await SaasSecretsStore.get_instance(
            self.user_id,
            effective_org_id=effective_org_id,
        )
        self.secrets_store = secrets_store
        return secrets_store

    async def get_secrets(self) -> Secrets | None:
        user_secrets = self._secrets
        if user_secrets:
            return user_secrets
        secrets_store = await self.get_secrets_store()
        user_secrets = await secrets_store.load()

        # Inject OPENHANDS_API_KEY (system-level, lazily generated)
        openhands_api_key = await self._get_openhands_api_key()
        if openhands_api_key:
            custom_secrets = dict(user_secrets.custom_secrets) if user_secrets else {}
            custom_secrets['OPENHANDS_API_KEY'] = CustomSecret(
                secret=SecretStr(openhands_api_key),
                description='OpenHands Cloud API Key for automations and integrations (system-managed)',
            )
            user_secrets = Secrets(
                custom_secrets=custom_secrets,
                provider_tokens=user_secrets.provider_tokens if user_secrets else {},
            )

        self._secrets = user_secrets
        return user_secrets

    async def get_access_token(self) -> SecretStr | None:
        return await get_auth_services().requests.get_access_token(self)

    async def get_latest_provider_token(self, provider: ProviderType) -> str | None:
        return await get_auth_services().requests.get_latest_provider_token(
            self, provider
        )

    async def get_user_git_info(self) -> UserGitInfo | None:
        return await super().get_user_git_info()

    async def get_provider_tokens(self) -> PROVIDER_TOKEN_TYPE | None:
        return await get_auth_services().requests.get_provider_tokens(self)

    async def get_user_settings_store(self) -> SettingsStore:
        settings_store = self.settings_store
        if settings_store:
            return settings_store
        # Scope settings to the request's effective org. See
        # `get_secrets_store` for the same rationale: the store mutates
        # the resolved Org row (and per-member overrides), so the
        # effective org must flow through here rather than letting the
        # store fall back to `user.current_org_id`.
        effective_org_id = await self.get_effective_org_id()
        settings_store = SaasSettingsStore(
            self.user_id, effective_org_id=effective_org_id
        )
        self.settings_store = settings_store
        return settings_store

    async def get_mcp_api_key(self) -> str:
        api_key_store = ApiKeyStore.get_instance()
        # Scope MCP_API_KEY to the request's effective org so that an
        # X-Org-Id override or API-key binding produces an MCP key in
        # the correct org context. Falls back to user.current_org_id
        # when no SAAS auth or effective org can be resolved.
        effective_org_id = await self.get_effective_org_id()
        mcp_api_key = await api_key_store.retrieve_mcp_api_key(
            self.user_id, org_id=effective_org_id
        )
        if not mcp_api_key:
            mcp_api_key = await api_key_store.create_api_key(
                self.user_id,
                'MCP_API_KEY',
                None,
                org_id=effective_org_id,
            )
        return mcp_api_key

    async def _get_openhands_api_key(self) -> str:
        """Get or create the user's OPENHANDS_API_KEY (system-level, non-deletable).

        This key is automatically generated on first access and stored as a system
        key that users cannot delete or modify. It is used for automations and
        integrations.

        The key is scoped to the request's *effective* organization (honoring
        an ``X-Org-Id`` override or API-key binding) rather than the user's
        persisted ``current_org_id``.
        """
        effective_org_id = await self.get_effective_org_id()
        if effective_org_id is None:
            raise ValueError(f'User {self.user_id} has no current organization')

        api_key_store = ApiKeyStore.get_instance()
        openhands_api_key = await api_key_store.get_or_create_system_api_key(
            user_id=self.user_id,
            org_id=effective_org_id,
            name='OPENHANDS_API_KEY',
        )
        return openhands_api_key

    async def get_org_info(self) -> dict | None:
        """Get organization info for the current user.

        Lazily loads and caches organization data including:
        - org_id: Current organization ID
        - org_name: Current organization name
        - role: User's role in the organization
        - permissions: List of permission names for the role

        Returns:
            dict with org_id, org_name, role, permissions or None if not available
        """
        if self._org_info_loaded:
            if self._org_id is None:
                return None
            return {
                'org_id': self._org_id,
                'org_name': self._org_name,
                'role': self._role,
                'permissions': self._permissions,
            }

        # Mark as loaded to avoid repeated attempts on failure
        self._org_info_loaded = True

        try:
            # Use the effective org id so that requests carrying an
            # X-Org-Id override (or an org-bound API key) see info for
            # the org they're actually operating in, not the user's
            # persisted current_org_id.
            effective_org_id = await self.get_effective_org_id()
            if effective_org_id is None:
                logger.warning(
                    f'No effective org for user {self.user_id} in get_org_info'
                )
                return None

            org = await OrgStore.get_org_by_id(effective_org_id)
            if not org:
                logger.warning(
                    f'Organization {effective_org_id} not found for user {self.user_id}'
                )
                return None

            # Get user's role in that org
            role = await get_user_org_role(self.user_id, effective_org_id)
            role_name = role.name if role else None

            # Get permissions for the role
            permissions: list[str] = []
            if role_name:
                role_permissions = get_role_permissions(role_name)
                permissions = [p.value for p in role_permissions]

            # Cache the results
            self._org_id = str(effective_org_id)
            self._org_name = org.name
            self._role = role_name
            self._permissions = permissions

            return {
                'org_id': self._org_id,
                'org_name': self._org_name,
                'role': self._role,
                'permissions': self._permissions,
            }
        except HTTPException:
            # Propagate validation errors raised by get_effective_org_id().
            raise
        except Exception:
            logger.exception(
                f'Error fetching org info for user {self.user_id}', stack_info=True
            )
            return None

    @classmethod
    async def get_instance(cls, request: Request) -> 'SaasUserAuth':
        logger.debug('saas_user_auth_get_instance')
        instance = await get_auth_services().requests.from_request(request)
        if instance is None:
            logger.debug('saas_user_auth_get_instance:no_credentials')
            raise NoCredentialsError('failed to authenticate')
        # Capture the raw X-Org-Id header (if any) so it can be validated
        # lazily by `get_effective_org_id()` the first time the request
        # needs an org context. See `server.auth.org_context`.
        instance._x_org_id_header = request.headers.get('X-Org-Id')
        if not getattr(request.state, 'user_rate_limit_processed', False):
            user_id = await instance.get_user_id()
            if user_id:
                # Ensure requests are only counted once
                request.state.user_rate_limit_processed = True
                # Will raise if rate limit is reached.
                if rate_limiter is not None:
                    await rate_limiter.hit('auth_uid', user_id)
        return instance

    @classmethod
    async def get_for_user(cls, user_id: str) -> 'SaasUserAuth':
        return await get_auth_services().requests.for_user(user_id)


def get_api_key_from_header(request: Request) -> str | None:
    return get_auth_services().requests.api_key(request)


async def saas_user_auth_from_bearer(request: Request) -> SaasUserAuth | None:
    return await get_auth_services().requests.from_bearer(request)


async def saas_user_auth_from_cookie(request: Request) -> SaasUserAuth | None:
    return await get_auth_services().requests.from_cookie(request)


async def saas_user_auth_from_signed_token(signed_token: str) -> SaasUserAuth:
    return await get_auth_services().requests.from_signed_token(signed_token)


async def get_user_auth_from_keycloak_id(keycloak_user_id: str) -> UserAuth:
    return await SaasUserAuth.get_for_user(keycloak_user_id)
