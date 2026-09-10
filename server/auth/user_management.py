"""Enterprise account boundary shared by browser and administrator workflows."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.contracts import (
    AuthActionEmail,
    AuthenticationUnavailable,
    Principal,
    UserProfile,
)
from server.auth.mode import SessionFactory, is_keycloak_enabled
from storage.api_key import ApiKey
from storage.database import a_session_maker
from storage.local_credentials import LocalCredentials, normalize_login_email
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User
from storage.user_store import UserStore


class AccountPermissionError(PermissionError):
    """The current Enterprise role does not authorize this account operation."""


class AccountConflict(ValueError):
    """The request conflicts with an existing account or enrollment."""


def user_profile(user: User) -> UserProfile:
    return UserProfile(
        user.id, user.email, bool(user.email_verified), user.is_disabled, user.role_id
    )


class EnterpriseUserManagementService:
    def __init__(self, session_factory: SessionFactory | None = None):
        self.session_factory = session_factory or a_session_maker

    async def get(self, user_id: UUID) -> UserProfile | None:
        user = await UserStore.get_user_by_id(str(user_id))
        return user_profile(user) if user else None

    async def search(self, email: str) -> list[UserProfile]:
        async with self.session_factory() as session:
            users = await session.scalars(
                select(User).where(
                    func.lower(User.email) == normalize_login_email(email)
                )
            )
            return [user_profile(user) for user in users]

    async def ensure_authenticated_account(
        self, user_id: UUID, user_info: dict | None = None
    ) -> User | None:
        """Compatibility hydration is explicit and only valid after authentication."""
        user = await UserStore.get_user_by_id(str(user_id))
        if not is_keycloak_enabled():
            return user
        if user is None or user.email is None or user.email_verified is None:
            from server.auth.keycloak.account_management import (
                KeycloakAccountManagement,
            )

            return await KeycloakAccountManagement().hydrate(user_id, user_info)
        return user

    @staticmethod
    async def _authorize(
        session: AsyncSession, actor: Principal, organization_id: UUID | None = None
    ) -> User:
        from server.auth.authorization import (
            Permission,
            has_permission,
        )

        user = await session.scalar(
            select(User).where(User.id == actor.user_id).with_for_update()
        )
        if user is None or user.is_disabled or actor.restricted:
            raise AccountPermissionError('Permission denied.')
        if (
            actor.organization_id is not None
            and organization_id is not None
            and actor.organization_id != organization_id
        ):
            raise AccountPermissionError('API key is bound to another organization.')
        permission = (
            Permission.PROVISION_USER if organization_id else Permission.MANAGE_USERS
        )
        role = await session.get(Role, user.role_id) if user.role_id else None
        if role and has_permission(role, permission, is_super=True):
            return user
        if organization_id is not None:
            member = await session.scalar(
                select(OrgMember).where(
                    OrgMember.org_id == organization_id,
                    OrgMember.user_id == actor.user_id,
                    OrgMember.status == 'active',
                )
            )
            org_role = await session.get(Role, member.role_id) if member else None
            if org_role and has_permission(org_role, permission):
                return user
        raise AccountPermissionError('Permission denied.')

    async def authorize(
        self, actor: Principal, organization_id: UUID | None = None
    ) -> None:
        async with self.session_factory() as session, session.begin():
            await self._authorize(session, actor, organization_id)

    async def create(
        self,
        email: str,
        password: SecretStr,
        *,
        actor: Principal,
        organization_id: UUID | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> UserProfile:
        user, _ = await self.provision_account(
            email,
            password,
            actor=actor,
            organization_id=organization_id,
            allow_existing=False,
            first_name=first_name,
            last_name=last_name,
        )
        return user

    async def provision_account(
        self,
        email: str,
        password: SecretStr,
        *,
        actor: Principal,
        organization_id: UUID | None = None,
        allow_existing: bool = True,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> tuple[UserProfile, bool]:
        await self.authorize(actor, organization_id)
        if is_keycloak_enabled():
            from server.auth.keycloak.account_management import (
                AccountConflict as KeycloakAccountConflict,
            )
            from server.auth.keycloak.account_management import (
                KeycloakAccountManagement,
            )

            try:
                user, created = await KeycloakAccountManagement().provision(
                    email, password
                )
            except KeycloakAccountConflict as exc:
                raise AccountConflict(str(exc)) from exc
            return user_profile(user), created
        from server.auth.local.accounts import create_local_account

        try:
            async with self.session_factory() as session, session.begin():
                await self._authorize(session, actor, organization_id)
                existing = await session.scalar(
                    select(User)
                    .join(LocalCredentials)
                    .where(
                        LocalCredentials.normalized_email
                        == normalize_login_email(email)
                    )
                )
                if existing:
                    if existing.is_disabled or not allow_existing:
                        raise AccountConflict(
                            'An account already uses this email address.'
                        )
                    return user_profile(existing), False
                user = await create_local_account(
                    session, email, password, first_name=first_name, last_name=last_name
                )
                return user_profile(user), True
        except IntegrityError:
            # Concurrent provisioning may have committed this same login. Never
            # replace its credential with the losing request's password.
            if not allow_existing:
                raise AccountConflict(
                    'An account already uses this email address.'
                ) from None
            async with self.session_factory() as session:
                existing = await session.scalar(
                    select(User)
                    .join(LocalCredentials)
                    .where(
                        LocalCredentials.normalized_email
                        == normalize_login_email(email)
                    )
                )
                if existing is None or existing.is_disabled:
                    raise AccountConflict(
                        'Account provisioning conflict. Retry the request.'
                    ) from None
                return user_profile(existing), False

    async def provision(
        self,
        email: str,
        password: SecretStr,
        *,
        actor: Principal,
        organization_id: UUID,
        role_name: str,
        api_key_name: str,
        reissue_api_key: bool = False,
    ) -> tuple[UserProfile, bool, bool, str]:
        """Commit local enrollment, membership and returned credentials together.

        Remote LLM setup is retried on admission/settings access, after the caller
        has received its initial password and API key. A failed database commit
        cannot strand a generated password in an otherwise hidden local account.
        """
        from storage.api_key_store import ApiKeyStore

        if ApiKeyStore.is_system_key_name(api_key_name):
            raise AccountConflict('Reserved API key name.')
        if is_keycloak_enabled():
            profile, created = await self.provision_account(
                email, password, actor=actor, organization_id=organization_id
            )
            added = await self.provision_membership(
                profile.id, organization_id, role_name, actor=actor
            )
            key = await self.provision_api_key(
                profile.id,
                organization_id,
                api_key_name,
                actor=actor,
                reissue=reissue_api_key,
            )
            return profile, created, added, key
        from server.auth.local.accounts import AccountConflict as LocalAccountConflict
        from server.auth.local.accounts import create_local_account

        for attempt in range(2):
            try:
                async with self.session_factory() as session, session.begin():
                    await self._authorize(session, actor, organization_id)
                    org = await session.get(Org, organization_id)
                    if (
                        org is None
                        or await session.get(User, organization_id) is not None
                    ):
                        raise AccountConflict('A team organization is required.')
                    role = await session.scalar(
                        select(Role).where(Role.name == role_name)
                    )
                    if role is None or role_name not in ('member', 'admin', 'owner'):
                        raise AccountConflict('Invalid organization role.')
                    user = await session.scalar(
                        select(User)
                        .join(LocalCredentials)
                        .where(
                            LocalCredentials.normalized_email
                            == normalize_login_email(email)
                        )
                        .with_for_update(of=User)
                    )
                    created = user is None
                    if user is None:
                        user = await create_local_account(session, email, password)
                    if user.is_disabled:
                        raise AccountPermissionError('Account is disabled or missing.')
                    member = await session.get(OrgMember, (organization_id, user.id))
                    added = member is None
                    if member is None:
                        session.add(
                            OrgMember(
                                org_id=organization_id,
                                user_id=user.id,
                                role_id=role.id,
                                status='active',
                                llm_api_key=SecretStr(''),
                                agent_settings_diff={},
                                conversation_settings_diff={},
                            )
                        )
                    user.accepted_tos = user.accepted_tos or datetime.now(UTC).replace(
                        tzinfo=None
                    )
                    user.user_consents_to_analytics = True
                    user.onboarding_completed = True
                    existing_key = await session.scalar(
                        select(ApiKey).where(
                            ApiKey.user_id == str(user.id),
                            ApiKey.org_id == organization_id,
                            ApiKey.name == api_key_name,
                        )
                    )
                    if existing_key is not None and not reissue_api_key:
                        key = existing_key.key
                    else:
                        if existing_key is not None:
                            await session.delete(existing_key)
                        key = ApiKeyStore.get_instance().generate_api_key()
                        session.add(
                            ApiKey(
                                user_id=str(user.id),
                                org_id=organization_id,
                                name=api_key_name,
                                key=key,
                            )
                        )
                    return user_profile(user), created, added, key
            except (IntegrityError, LocalAccountConflict):
                if attempt:
                    raise AccountConflict(
                        'Account provisioning conflict. Retry the request.'
                    ) from None
        raise AssertionError('Unreachable provisioning attempt')

    async def request_email_change(
        self, user_id: UUID, email: str, *, actor: Principal
    ) -> AuthActionEmail | None:
        if (
            actor.user_id != user_id
            or actor.restricted
            or actor.authentication_method != 'password'
            or is_keycloak_enabled()
        ):
            raise AccountPermissionError(
                'Email changes require the account password session.'
            )
        from server.auth.local.actions import LocalAccountActions

        return await LocalAccountActions(self.session_factory).request_verification(
            user_id, email
        )

    @staticmethod
    async def apply_verified_email(
        session: AsyncSession, user: User, credential: LocalCredentials, email: str
    ) -> None:
        """Internal setter: caller owns account lock and atomic proof consumption."""
        user.email = email
        user.email_verified = True
        credential.normalized_email = email
        org = await session.get(Org, user.id)
        if org:
            org.contact_email = email
        await session.flush()

    async def ensure_llm_provisioned(
        self, user_id: UUID, organization_id: UUID
    ) -> None:
        """Retry remote provisioning after the required account graph commits.

        A member's persisted key is the completion marker. Hold the account lock
        through this separate operation so simultaneous logins cannot rotate keys
        out from under one another and disabling waits for provisioning to finish.
        """
        from server.constants import (
            LITE_LLM_API_KEY,
            LITE_LLM_API_URL,
            get_default_llm_api_key,
            should_use_direct_llm_defaults,
        )
        from storage.lite_llm_manager import LiteLlmManager

        direct_defaults = should_use_direct_llm_defaults()
        if not direct_defaults and not (LITE_LLM_API_KEY and LITE_LLM_API_URL):
            return
        async with self.session_factory() as session, session.begin():
            user = await session.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
            if user is None or user.is_disabled:
                raise AccountPermissionError('Account is disabled or missing.')
            member = await session.scalar(
                select(OrgMember)
                .where(
                    OrgMember.user_id == user_id, OrgMember.org_id == organization_id
                )
                .with_for_update()
            )
            if member is None:
                raise AccountPermissionError('Organization membership is missing.')
            if member.has_custom_llm_api_key or (
                member.llm_api_key and member.llm_api_key.get_secret_value()
            ):
                return
            from storage.org_store import OrgStore
            from storage.saas_settings_store import managed_llm_key_config_from_model

            org = await session.get(Org, organization_id)
            if org is None:
                raise AccountPermissionError('Organization is missing.')
            if org.llm_api_key and org.llm_api_key.get_secret_value():
                return
            llm = OrgStore.get_agent_settings_from_org(org).llm
            overrides = member.agent_settings_diff.get('llm', {})
            if not isinstance(overrides, dict):
                return
            model = overrides.get('model', llm.model)
            base_url = overrides.get('base_url', llm.base_url)
            if direct_defaults:
                defaults = UserStore.default_settings().agent_settings.llm
                # Never send a deployment credential to a member's custom host.
                if model == defaults.model and base_url == defaults.base_url:
                    default_api_key = get_default_llm_api_key()
                    if default_api_key:
                        member.llm_api_key = SecretStr(default_api_key)
                return
            if managed_llm_key_config_from_model(model, base_url) is None:
                return
            settings = await LiteLlmManager.create_entries(
                str(organization_id), str(user_id), UserStore.default_settings(), False
            )
            if settings is None:
                raise AuthenticationUnavailable(
                    'LLM provisioning is incomplete. Retry the request.'
                )
            key = settings.agent_settings.llm.api_key
            member.llm_api_key = (
                key if isinstance(key, SecretStr) else SecretStr(key or '')
            )

    async def provision_membership(
        self, user_id: UUID, organization_id: UUID, role_name: str, *, actor: Principal
    ) -> bool:
        async with self.session_factory() as session, session.begin():
            await self._authorize(session, actor, organization_id)
            # User lock serializes membership/API-key work with lifecycle denial.
            user = await session.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
            if user is None or user.is_disabled:
                raise AccountPermissionError('Account is disabled or missing.')
            org = await session.get(Org, organization_id)
            if org is None:
                raise AccountConflict('Organization no longer exists.')
            member = await session.scalar(
                select(OrgMember).where(
                    OrgMember.org_id == organization_id, OrgMember.user_id == user_id
                )
            )
            created = member is None
            if member is None:
                role = await session.scalar(select(Role).where(Role.name == role_name))
                if role is None or role_name not in ('member', 'admin', 'owner'):
                    raise AccountConflict('Invalid organization role.')
                session.add(
                    OrgMember(
                        org_id=organization_id,
                        user_id=user_id,
                        role_id=role.id,
                        status='active',
                        llm_api_key=SecretStr(''),
                        agent_settings_diff={},
                        conversation_settings_diff={},
                    )
                )
            user.accepted_tos = user.accepted_tos or datetime.now(UTC).replace(
                tzinfo=None
            )
            user.user_consents_to_analytics = True
            user.onboarding_completed = True
        return created

    async def provision_api_key(
        self,
        user_id: UUID,
        organization_id: UUID,
        name: str,
        *,
        actor: Principal,
        reissue: bool = False,
    ) -> str:
        from storage.api_key_store import ApiKeyStore

        if ApiKeyStore.is_system_key_name(name):
            raise AccountConflict('Reserved API key name.')
        async with self.session_factory() as session, session.begin():
            await self._authorize(session, actor, organization_id)
            user = await session.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
            if user is None or user.is_disabled:
                raise AccountPermissionError('Account is disabled or missing.')
            key = await session.scalar(
                select(ApiKey).where(
                    ApiKey.user_id == str(user_id),
                    ApiKey.org_id == organization_id,
                    ApiKey.name == name,
                )
            )
            if key and not reissue:
                return key.key
            if key:
                await session.execute(delete(ApiKey).where(ApiKey.id == key.id))
            value = ApiKeyStore.get_instance().generate_api_key()
            session.add(
                ApiKey(
                    key=value, user_id=str(user_id), org_id=organization_id, name=name
                )
            )
            return value

    async def lifecycle(self, operation: str, user_id: UUID, *, actor: Principal):
        from server.services.admin_user_lifecycle_service import (
            AdminUserLifecycleService,
        )

        await self.authorize(actor)
        service = AdminUserLifecycleService(session_factory=self.session_factory)
        if operation == 'disable':
            return await service.disable_user(str(user_id))
        if operation == 'enable':
            return await service.enable_user(str(user_id))
        if operation == 'delete':
            return await service.delete_user(str(user_id))
        raise ValueError('Unknown account operation')

    async def disable(self, user_id: UUID, *, actor: Principal) -> None:
        await self.lifecycle('disable', user_id, actor=actor)

    async def delete(self, user_id: UUID, *, actor: Principal) -> None:
        await self.lifecycle('delete', user_id, actor=actor)
