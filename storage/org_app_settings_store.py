"""Store class for managing organization app settings."""

from __future__ import annotations

import logging
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
        """Get the current organization for a user.

        Args:
            user_id: The user's ID (Keycloak user ID)

        Returns:
            Org: The organization object, or None if not found
        """
        # Get user with their current_org_id
        user_result = await self.db_session.execute(
            select(User).filter(User.id == UUID(user_id))
        )
        user = user_result.scalars().first()

        if not user:
            return None

        org_id = user.current_org_id
        if not org_id:
            return None

        return await self.get_org_by_id(org_id)

    async def get_org_by_id(self, org_id: UUID) -> Org | None:
        """Get an organization by its id, validating the org version.

        Args:
            org_id: The organization's UUID.

        Returns:
            Org: The organization object, or None if not found.
        """
        org_result = await self.db_session.execute(select(Org).filter(Org.id == org_id))
        org = org_result.scalars().first()
        if not org:
            return None
        return await self._validate_org_version(org)

    async def _validate_org_version(self, org: Org) -> Org:
        """Check if we need to update org version.

        Args:
            org: The organization to validate

        Returns:
            Org: The validated (and potentially updated) organization
        """
        gateway_enabled = await is_litellm_enabled()

        if OrgStore._needs_settings_upgrade(org, gateway_enabled):
            await self.db_session.refresh(org, with_for_update=True)
            if OrgStore._needs_settings_upgrade(org, gateway_enabled):
                OrgStore._upgrade_settings(org, gateway_enabled)
                await self.db_session.flush()
                await self.db_session.refresh(org)

        return org

    async def update_org_app_settings(
        self, org_id: UUID, update_data: OrgAppSettingsUpdate
    ) -> Org | None:
        """Update organization app settings.

        Only updates fields that are explicitly provided in update_data.
        Uses flush() - commit happens at request end via DbSessionInjector.

        ``agent_settings_diff`` merges into the existing ``agent_settings``
        defaults; every other field is a plain column write.

        Implements optimistic locking: if last_known_updated_at is provided and
        doesn't match the current DB version, raises OrgConcurrentModificationError.

        Args:
            org_id: The organization's ID
            update_data: Pydantic model with fields to update

        Returns:
            Org: The updated organization object, or None if not found

        Raises:
            OrgConcurrentModificationError: If optimistic locking detects a conflict
        """
        logger = logging.getLogger(__name__)

        result = await self.db_session.execute(
            select(Org)
            .filter(Org.id == org_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        org = result.scalars().first()

        if not org:
            return None

        # Optimistic locking: check if record was modified since client read it
        if update_data.last_known_updated_at is not None:
            current_updated_at = org.updated_at
            expected_updated_at = update_data.last_known_updated_at

            # Ensure timezone-aware comparison
            if current_updated_at.tzinfo is None:
                current_updated_at = current_updated_at.replace(tzinfo=timezone.utc)
            if expected_updated_at.tzinfo is None:
                expected_updated_at = expected_updated_at.replace(tzinfo=timezone.utc)

            # Raise conflict error if versions don't match
            if current_updated_at != expected_updated_at:
                logger.warning(
                    f"Org '{org.name}' concurrent modification detected. "
                    f'Expected: {expected_updated_at.isoformat()}, '
                    f'Current: {current_updated_at.isoformat()}. '
                    f'Raising conflict error.'
                )
                raise OrgConcurrentModificationError(
                    org_id=str(org.id),
                    expected_version=expected_updated_at,
                    actual_version=current_updated_at,
                )

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
