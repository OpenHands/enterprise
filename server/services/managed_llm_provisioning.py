"""Installation-selected provisioning of application-owned managed LLM keys."""

from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.utils.llm import is_openhands_model
from storage.org import Org
from storage.org_member import OrgMember

if TYPE_CHECKING:
    from storage.saas_settings_store import ManagedLlmKeyRotation, SaasSettingsStore


class ManagedLlmProvisioning(Protocol):
    async def get_account_email(self, user_id: str) -> str | None: ...
    async def prepare_settings_key(
        self,
        session: AsyncSession,
        item: Settings,
        org: Org,
        member: OrgMember,
        store: 'SaasSettingsStore',
    ) -> None: ...
    async def persist_shared_key(
        self, session: AsyncSession, org_id: UUID, key: str | None
    ) -> None: ...
    async def finish_member_update(
        self,
        session: AsyncSession,
        member: OrgMember,
        *,
        uses_managed_key: bool,
        org: Org,
    ) -> None: ...
    async def reconcile_org_members(self, session: AsyncSession, org: Org) -> None: ...
    async def ensure_org_member_key(
        self, session: AsyncSession, org: Org, member: OrgMember
    ) -> str | None: ...
    async def rotate_managed_key(
        self, store: 'SaasSettingsStore'
    ) -> 'ManagedLlmKeyRotation': ...


class KeycloakManagedLlmProvisioning:
    async def get_account_email(self, user_id: str) -> str | None:
        from server.auth.composition import get_auth_services

        account = await get_auth_services().accounts.get_account_info(user_id)
        return account.email if account is not None else None

    async def prepare_settings_key(
        self,
        session: AsyncSession,
        item: Settings,
        org: Org,
        member: OrgMember,
        store: 'SaasSettingsStore',
    ) -> None:
        fallback = (
            member.llm_api_key
            if not org._llm_api_key
            and not member.has_custom_llm_api_key
            and member._llm_api_key
            else None
        )
        await store._ensure_api_key(
            item,
            str(org.id),
            openhands_type=is_openhands_model(item.agent_settings.llm.model),
            fallback_api_key=fallback,
        )

    async def persist_shared_key(
        self, session: AsyncSession, org_id: UUID, key: str | None
    ) -> None:
        from server.routes.org_models import OrgMemberSettingsUpdate
        from storage.org_member_store import OrgMemberStore

        await OrgMemberStore.update_all_members_settings_async(
            session,
            org_id,
            OrgMemberSettingsUpdate(
                llm_api_key=SecretStr(key) if key is not None else None
            ),
        )

    async def finish_member_update(
        self,
        session: AsyncSession,
        member: OrgMember,
        *,
        uses_managed_key: bool,
        org: Org,
    ) -> None:
        """Legacy key updates have no generation-specific work to release."""

    async def reconcile_org_members(self, session: AsyncSession, org: Org) -> None:
        """Legacy members reconcile keys during their next settings access."""

    async def ensure_org_member_key(
        self, session: AsyncSession, org: Org, member: OrgMember
    ) -> str | None:
        from storage.org_store import OrgStore

        return await OrgStore._ensure_keycloak_managed_member_key(org, member)

    async def rotate_managed_key(
        self, store: 'SaasSettingsStore'
    ) -> 'ManagedLlmKeyRotation':
        return await store._rotate_keycloak_managed_llm_key()


class OpenHandsManagedLlmProvisioning:
    async def get_account_email(self, user_id: str) -> str | None:
        from server.auth.composition import get_auth_services

        account = await get_auth_services().accounts.get_account_info(user_id)
        if account is None:
            raise ValueError('Account is not available for provisioning')
        return account.email

    async def prepare_settings_key(
        self,
        session: AsyncSession,
        item: Settings,
        org: Org,
        member: OrgMember,
        store: 'SaasSettingsStore',
    ) -> None:
        from server.services.native_provisioning_service import prepare_managed_member

        item.agent_settings.llm.api_key = (
            org.llm_api_key
            if org._llm_api_key
            else await prepare_managed_member(session, member)
        )

    async def persist_shared_key(
        self, session: AsyncSession, org_id: UUID, key: str | None
    ) -> None:
        """A personal key update only changes the requesting membership."""

    async def finish_member_update(
        self,
        session: AsyncSession,
        member: OrgMember,
        *,
        uses_managed_key: bool,
        org: Org,
    ) -> None:
        from server.services.native_provisioning_service import release_managed_member

        if not uses_managed_key or org._llm_api_key:
            await release_managed_member(session, member)

    async def reconcile_org_members(self, session: AsyncSession, org: Org) -> None:
        from server.services.native_provisioning_service import (
            prepare_managed_member,
            release_managed_member,
        )
        from storage.org_store import OrgStore
        from storage.saas_settings_store import managed_llm_key_config_from_model

        llm = OrgStore.get_agent_settings_from_org(org).llm
        managed = managed_llm_key_config_from_model(llm.model, llm.base_url)
        members = await session.scalars(
            select(OrgMember).where(OrgMember.org_id == org.id)
        )
        for member in members:
            if managed is not None and not org._llm_api_key:
                if not member.has_custom_llm_api_key:
                    await prepare_managed_member(session, member)
            else:
                await release_managed_member(session, member)

    async def ensure_org_member_key(
        self, session: AsyncSession, org: Org, member: OrgMember
    ) -> str | None:
        from server.services.native_provisioning_service import (
            prepare_managed_member,
            release_managed_member,
        )

        if org._llm_api_key:
            await release_managed_member(session, member)
            return None
        key = await prepare_managed_member(session, member)
        return key.get_secret_value() or None

    async def rotate_managed_key(
        self, store: 'SaasSettingsStore'
    ) -> 'ManagedLlmKeyRotation':
        return await store._rotate_native_managed_llm_key()
