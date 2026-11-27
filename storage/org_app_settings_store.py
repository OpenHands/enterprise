"""Store class for managing organization app settings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.routes.org_models import (
    OrgAppSettingsUpdate,
    OrgConcurrentModificationError,
)
from storage.lite_llm_manager import is_litellm_enabled
from storage.org import Org
from storage.org_store import OrgStore
from storage.user import User


@dataclass
class OrgAppSettingsStore:
    """Store for organization app settings with injected db_session."""

    db_session: AsyncSession

    async def get_current_org_by_user_id(self, user_id: str) -> Org | None:
        org_id = await self.db_session.scalar(
            select(User.current_org_id).where(User.id == UUID(user_id))
        )
        return await self.get_org_by_id(org_id) if org_id else None

    async def get_org_by_id(self, org_id: UUID) -> Org | None:
        org = await self.db_session.get(Org, org_id)
        if org is None:
            return None
        gateway = await is_litellm_enabled()
        if OrgStore._needs_settings_upgrade(org, gateway):
            await self.db_session.refresh(org, with_for_update=True)
            if OrgStore._needs_settings_upgrade(org, gateway):
                OrgStore._upgrade_settings(org, gateway)
                await self.db_session.flush()
                await self.db_session.refresh(org)
        return org

    async def update_org_app_settings(
        self,
        org_id: UUID,
        update_data: OrgAppSettingsUpdate,
        *,
        user_id: str | None = None,
    ) -> Org | None:
        """Apply a revision-checked edit in the request's database transaction."""
        result = await self.db_session.execute(
            select(Org)
            .filter(Org.id == org_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        org = result.scalars().first()

        if not org:
            return None

        if user_id is not None:
            await OrgStore.require_settings_writer(
                self.db_session,
                org,
                user_id,
                admin=(
                    'registered_marketplaces' in update_data.model_fields_set
                    or bool(update_data.settings_edits)
                    or update_data.agent_settings_diff is not None
                ),
            )

        expected = update_data.last_known_updated_at
        if expected is not None:
            actual = org.updated_at
            expected = (
                expected if expected.tzinfo else expected.replace(tzinfo=timezone.utc)
            )
            actual = actual if actual.tzinfo else actual.replace(tzinfo=timezone.utc)
            if actual != expected:
                raise OrgConcurrentModificationError(str(org.id), expected, actual)

        # Normalize marketplace scope before persisting the organization document.
        update_dict = update_data.model_dump(
            exclude_unset=True,
            exclude={'last_known_updated_at', 'settings_edits'},
        )
        key = 'registered_marketplaces'
        if key in update_dict or update_data.settings_edits:
            from openhands.app_server.settings.marketplace_composition import (
                duplicate_marketplace_names,
                edit_marketplaces,
            )
            from openhands.app_server.settings.settings_models import (
                MarketplaceScope,
                normalize_marketplaces,
            )

            if update_data.settings_edits:
                if key in update_dict or update_data.last_known_updated_at is None:
                    raise ValueError(
                        'Settings edits require a revision and no marketplace snapshot'
                    )
                validated = edit_marketplaces(
                    org.registered_marketplaces,
                    MarketplaceScope.ORG,
                    update_data.settings_edits,
                )
            else:
                validated = normalize_marketplaces(
                    update_dict.pop(key), MarketplaceScope.ORG
                )
            conflicts = duplicate_marketplace_names(validated)
            if conflicts:
                raise ValueError(
                    'Duplicate marketplace name(s) not allowed: '
                    + ', '.join(sorted(conflicts))
                )
            org.registered_marketplaces = validated

        # Popped before the setattr loop: ``Org`` has no matching attribute,
        # and this is a diff to merge into the JSON column, not a value to set.
        agent_settings_diff = update_dict.pop('agent_settings_diff', None)
        if agent_settings_diff is not None:
            org.agent_settings = OrgStore._apply_agent_diff(
                org.agent_settings,
                agent_settings_diff,
            ).model_dump(mode='json', exclude_unset=True)

        # Update regular org fields
        for field, value in update_dict.items():
            setattr(org, field, value)

        # flush instead of commit - DbSessionInjector auto-commits at request end
        await self.db_session.flush()
        await self.db_session.refresh(org)
        return org
