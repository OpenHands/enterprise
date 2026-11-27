"""Store class for managing organizations."""

from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from openhands.app_server.settings.settings_editor import (
    apply_operation,
    apply_settings_edit,
    editable_conversation_path,
    editable_member_agent_path,
    resolve_list_edits,
)
from openhands.app_server.settings.settings_models import (
    Settings,
    _load_persisted_agent_settings,
    _load_persisted_conversation_settings,
    agent_llm,
)
from openhands.app_server.utils.llm import is_openhands_model
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.utils.openhands_provider import is_openhands_proxy_base_url
from openhands.sdk.settings import (
    AgentSettingsConfig,
    ConversationSettings,
    apply_agent_settings_diff,
)
from openhands.sdk.tool.defaults import (
    RETIRED_TOOL_SWITCHES,
    fold_retired_tool_switches,
    resolve_tool_specs,
)
from server import constants
from server.constants import (
    DEFAULT_V1_ENABLED,
    LITE_LLM_API_URL,
    ORG_SETTINGS_VERSION,
    get_default_llm_api_key,
    get_default_llm_base_url,
    get_default_llm_model,
    is_in_cluster_url,
)
from server.routes.org_models import (
    OrgConcurrentModificationError,
    OrgMemberSettingsUpdate,
    OrgUpdate,
    OrphanedUserError,
)
from storage.daily_sandbox_usage import DailySandboxUsage
from storage.database import a_session_maker
from storage.lite_llm_manager import (
    LiteLlmManager,
    get_openhands_cloud_key_alias,
    get_org_team_alias,
    is_litellm_enabled,
)
from storage.org import Org, OrganizationSettings
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_budget_threshold import OrgBudgetThreshold
from storage.org_default_settings import (
    apply_configured_org_condenser_default,
    apply_org_condenser_max_tokens_default,
    condenser_default_category,
)
from storage.org_git_claim import OrgGitClaim
from storage.org_invitation import OrgInvitation
from storage.org_member import OrgMember
from storage.org_user_budget_override import OrgUserBudgetOverride
from storage.role import Role
from storage.user import User
from storage.user_settings import UserSettings


@dataclass(frozen=True)
class OrgCondenserReconciliationResult:
    """Summary of org condenser max-token reconciliation."""

    updated_count: int
    skipped_agent_variant_count: int
    skipped_condenser_variant_count: int
    malformed_repaired_count: int


_ORG_SETTINGS_EXCLUDED_FIELDS = {
    'id',
    'name',
    'contact_name',
    'contact_email',
    'org_version',
    'llm_api_key',
}
_ORG_SETTINGS_FIELDS = {
    normalized
    for column in Org.__table__.columns
    if column.name != 'settings'
    and (normalized := column.name.lstrip('_')) not in _ORG_SETTINGS_EXCLUDED_FIELDS
} | (
    OrganizationSettings.model_fields.keys()
    - {'registered_marketplaces', 'member_settings_denials', 'meta_profiles'}
)


class OrgStore:
    """Store for managing organizations."""

    @staticmethod
    async def _delete_litellm_user_best_effort(user_id: str, org_id: UUID) -> None:
        """Delete the LiteLLM user record without blocking org deletion."""
        try:
            await LiteLlmManager.delete_user(user_id)
        except Exception as exc:
            logger.warning(
                'Failed to delete LiteLLM user during org cascade cleanup',
                extra={'org_id': str(org_id), 'user_id': user_id, 'error': str(exc)},
            )

    @staticmethod
    def get_agent_settings_from_org(org: Org) -> AgentSettingsConfig:
        return _load_persisted_agent_settings(dict(org.agent_settings))

    @staticmethod
    def resolve_member_agent(org: Org, diff: dict[str, Any] | None) -> dict[str, Any]:
        from openhands.app_server.settings.meta_profiles import (
            ORG_OWNED_ROUTER_SETTINGS_KEYS,
        )
        from openhands.app_server.settings.settings_models import (
            normalize_member_agent_settings_diff,
        )

        parent = OrgStore.get_agent_settings_from_org(org).model_dump(mode='json')
        contribution = normalize_member_agent_settings_diff(dict(diff or {}))
        for key in (*ORG_OWNED_ROUTER_SETTINGS_KEYS, 'mcp_config'):
            parent.pop(key, None)
            contribution.pop(key, None)
        if 'schema_version' not in contribution and contribution.get('tools') == []:
            contribution.pop('tools')
        if not any(key in contribution for key in ('tools', *RETIRED_TOOL_SWITCHES)):
            contribution.pop('schema_version', None)
        if contribution.get('agent_kind') == 'llm':
            contribution['agent_kind'] = 'openhands'
        kind = contribution.get('agent_kind', parent['agent_kind'])
        if kind == 'acp':
            contribution.pop('llm', None)
        contribution = resolve_list_edits(
            parent if kind == parent['agent_kind'] else {},
            contribution,
            list_defaults={'tools': OrgStore.default_tool_document()}
            if kind == 'openhands'
            else None,
        )
        effective = apply_agent_settings_diff(parent, contribution).model_dump(
            mode='json'
        )
        effective.pop('mcp_config', None)
        return effective

    @staticmethod
    def default_tool_document() -> list[dict[str, Any]]:
        return [
            tool.model_dump(mode='json')
            for tool in resolve_tool_specs(None, enable_browser=True)
        ]

    @staticmethod
    def get_conversation_settings_from_org(org: Org) -> ConversationSettings:
        return _load_persisted_conversation_settings(dict(org.conversation_settings))

    @staticmethod
    async def create_org(
        kwargs: dict,
    ) -> Org:
        """Create a new organization."""
        async with a_session_maker() as session:
            org = Org(**kwargs)
            org.org_version = ORG_SETTINGS_VERSION
            agent_settings = org.agent_settings or {}
            if agent_settings.get('agent_kind') != 'acp':
                llm = dict(agent_settings.get('llm') or {})
                llm['model'] = llm.get('model') or get_default_llm_model()
                base_url = llm.pop('base_url', None) or get_default_llm_base_url()
                if base_url is not None:
                    llm['base_url'] = base_url
                agent_settings = {**agent_settings, 'llm': llm}
            org.agent_settings = apply_configured_org_condenser_default(agent_settings)
            if org.v1_enabled is None:
                org.v1_enabled = DEFAULT_V1_ENABLED
            session.add(org)
            await session.commit()
            await session.refresh(org)
            return org

    @staticmethod
    async def reconcile_applicable_org_condenser_max_tokens(
        session: AsyncSession,
        *,
        max_tokens: int,
        overwrite_existing: bool,
    ) -> OrgCondenserReconciliationResult:
        """Reconcile org-level LLM-summarizing condenser token thresholds."""

        updated = skipped_agent = skipped_condenser = repaired = 0
        # Lock before reading the document, so replacing its agent block cannot
        # lose concurrent preference edits. Keep batches bounded during bootstrap.
        after = None
        while True:
            query = select(Org).order_by(Org.id).limit(250).with_for_update()
            if after is not None:
                query = query.where(Org.id > after)
            batch = (await session.scalars(query)).all()
            if not batch:
                break
            after = batch[-1].id
            for org in batch:
                current = org.agent_settings
                category = condenser_default_category(current)
                if category == 'skipped_agent':
                    skipped_agent += 1
                elif category == 'skipped_condenser':
                    skipped_condenser += 1
                else:
                    candidate = apply_org_condenser_max_tokens_default(
                        current, max_tokens=max_tokens, overwrite=overwrite_existing
                    )
                    if candidate != current:
                        org.agent_settings = candidate
                        updated += 1
                        repaired += category == 'malformed'
            await session.flush()
        return OrgCondenserReconciliationResult(
            updated, skipped_agent, skipped_condenser, repaired
        )

    @staticmethod
    async def get_org_by_id(org_id: UUID) -> Org | None:
        """Get organization by ID."""
        async with a_session_maker() as session:
            result = await session.execute(select(Org).filter(Org.id == org_id))
            org = result.scalars().first()
        return await OrgStore._validate_org_version(org)

    @staticmethod
    async def enable_byor_export(org_id: UUID) -> Org | None:
        """Persist BYOR export enablement for an organization."""
        return await OrgStore._update_org_kwargs(
            org_id,
            {'byor_export_enabled': True},
        )

    @staticmethod
    async def set_org_status(org_id: UUID, status: str) -> Org | None:
        """Set organization lifecycle status (``active`` or ``suspended``)."""
        if status not in ('active', 'suspended'):
            raise ValueError(f'Invalid organization status: {status!r}')
        return await OrgStore._update_org_kwargs(org_id, {'status': status})

    @staticmethod
    async def get_orgs_by_ids(org_ids: list[UUID]) -> list[Org]:
        """Get multiple organizations by IDs in a single query.

        Args:
            org_ids: List of organization UUIDs to fetch.

        Returns:
            List of Org objects (may be fewer than input if some IDs don't exist).
        """
        if not org_ids:
            return []
        async with a_session_maker() as session:
            result = await session.execute(select(Org).filter(Org.id.in_(org_ids)))
            orgs = list(result.scalars().all())
        # Validate org versions for all returned orgs
        validated_orgs = []
        for org in orgs:
            validated = await OrgStore._validate_org_version(org)
            if validated:
                validated_orgs.append(validated)
        return validated_orgs

    @staticmethod
    async def get_current_org_from_keycloak_user_id(
        keycloak_user_id: str,
    ) -> Org | None:
        async with a_session_maker() as session:
            result = await session.execute(
                select(User)
                .options(joinedload(User.org_members))
                .filter(User.id == UUID(keycloak_user_id))
            )
            user = result.scalars().first()
            if not user:
                logger.warning(f'User not found for ID {keycloak_user_id}')
                return None
            org_id = user.current_org_id
            result = await session.execute(select(Org).filter(Org.id == org_id))
            org = result.scalars().first()
            if not org:
                logger.warning(
                    f'Org not found for ID {org_id} as the current org for user {keycloak_user_id}'
                )
                return None
            return await OrgStore._validate_org_version(org)

    @staticmethod
    async def get_org_by_name(name: str) -> Org | None:
        """Get organization by name."""
        async with a_session_maker() as session:
            result = await session.execute(select(Org).filter(Org.name == name))
            org = result.scalars().first()
        return await OrgStore._validate_org_version(org)

    @staticmethod
    async def get_default_org() -> Org | None:
        """Get the org flagged as the install's bootstrapped default org."""
        async with a_session_maker() as session:
            result = await session.execute(select(Org).filter(Org.is_default))
            org = result.scalars().first()
        return await OrgStore._validate_org_version(org)

    @staticmethod
    async def mark_org_as_default(org_id: UUID) -> Org | None:
        """Flag an org as the install's default org.

        Returns the org on success (or if already flagged). Returns None if
        the org does not exist or another org is already flagged (the partial
        unique index allows at most one default org).
        """
        async with a_session_maker() as session:
            result = await session.execute(select(Org).filter(Org.id == org_id))
            org = result.scalars().first()
            if not org:
                return None
            if not org.is_default:
                org.is_default = True
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    return None
        return org

    @staticmethod
    async def list_team_orgs(limit: int | None = None) -> list[Org]:
        """List orgs that are not personal workspaces (see count_team_orgs)."""
        async with a_session_maker() as session:
            stmt = select(Org).where(~select(User.id).where(User.id == Org.id).exists())
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    @staticmethod
    async def count_team_orgs() -> int:
        """Count orgs that are not personal workspaces.

        A personal workspace shares its id with its user, so team orgs are
        the orgs whose id has no matching user.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(func.count())
                .select_from(Org)
                .where(~select(User.id).where(User.id == Org.id).exists())
            )
            return int(result.scalar() or 0)

    @staticmethod
    def _uses_managed_default_llm(org: Org) -> bool:
        """Whether the org's effective LLM is the managed default (not BYOK).

        A version-bump upgrade must only reset an org's ``llm.model``/``base_url``
        when it is still on the managed proxy default. BYOK orgs point at a
        third-party ``base_url`` (or a bare model), so the lazy bump must leave
        them untouched instead of aiming their custom config at the managed proxy.
        """
        llm = dict(org.agent_settings).get('llm') or {}
        model = llm.get('model')
        base_url = llm.get('base_url')

        if not isinstance(model, str):
            return False

        managed_base_url = (LITE_LLM_API_URL or '').rstrip('/')
        normalized_base_url = (
            base_url.rstrip('/') if isinstance(base_url, str) else None
        )

        # No gateway: ``openhands/`` on the public proxy is OpenHands Cloud with
        # the org's own key (BYOK), not a managed default to repair.
        if (
            is_openhands_model(model)
            and not constants.ENABLE_LEGACY_LITELLM
            and is_openhands_proxy_base_url(normalized_base_url)
        ):
            return False

        if normalized_base_url == managed_base_url:
            return True
        # The bundled proxy's URL, left behind after LiteLLM was turned off.
        gateway_off_proxy_url = not constants.ENABLE_LEGACY_LITELLM and (
            normalized_base_url is None or is_in_cluster_url(normalized_base_url)
        )
        # Public OpenHands provider model with no explicit (or a managed-host)
        # base_url is managed.
        if is_openhands_model(model):
            return (
                normalized_base_url is None
                or 'all-hands.dev' in normalized_base_url.lower()
                or gateway_off_proxy_url
            )
        return model.startswith('litellm_proxy/') and gateway_off_proxy_url

    @staticmethod
    def _needs_gateway_off_repair(org: Org) -> bool:
        """Whether the org still defaults to a LiteLLM model the deployment no longer serves."""
        model = (dict(org.agent_settings).get('llm') or {}).get('model')
        return OrgStore._uses_managed_default_llm(org) and (
            model != get_default_llm_model()
        )

    @staticmethod
    def _deployment_default_llm(
        gateway_enabled: bool,
    ) -> tuple[dict[str, Any], str | None]:
        """The deployment default LLM, plus the install key to share when there is no gateway."""
        llm = {'model': get_default_llm_model(), 'base_url': get_default_llm_base_url()}
        return llm, None if gateway_enabled else get_default_llm_api_key()

    @staticmethod
    def _needs_settings_upgrade(org: Org, gateway_enabled: bool) -> bool:
        return org.org_version < ORG_SETTINGS_VERSION or (
            not gateway_enabled and OrgStore._needs_gateway_off_repair(org)
        )

    @staticmethod
    def _upgrade_settings(org: Org, gateway_enabled: bool) -> None:
        org.org_version = ORG_SETTINGS_VERSION
        if OrgStore._uses_managed_default_llm(org):
            llm, shared_key = OrgStore._deployment_default_llm(gateway_enabled)
            org.agent_settings = OrgStore._apply_agent_diff(
                org.agent_settings, {'llm': llm}
            ).model_dump(mode='json', exclude_unset=True)
            if shared_key:
                org.llm_api_key = SecretStr(shared_key)

    @staticmethod
    async def _validate_org_version(org: Org | None) -> Org | None:
        gateway_enabled = await is_litellm_enabled()
        if org and OrgStore._needs_settings_upgrade(org, gateway_enabled):
            async with a_session_maker() as session:
                current = await session.get(Org, org.id, with_for_update=True)
                if current is None or not OrgStore._needs_settings_upgrade(
                    current, gateway_enabled
                ):
                    return current
                OrgStore._upgrade_settings(current, gateway_enabled)
                await session.commit()
                await session.refresh(current)
                org = current
            # One-time, best-effort repair of a stale free-tier LiteLLM team
            # allowlist (the version bump is the once-per-org trigger). A
            # failed repair leaves the org upgraded but still 403ing exactly as
            # before, and is never retried on later loads.
            if org is not None:
                try:
                    await LiteLlmManager.ensure_free_team_models(str(org.id))
                except Exception:
                    logger.warning(
                        'Failed to repair free-tier LiteLLM team allowlist',
                        exc_info=True,
                        extra={'org_id': str(org.id)},
                    )
        return org

    @staticmethod
    async def list_orgs() -> list[Org]:
        """List all organizations."""
        async with a_session_maker() as session:
            result = await session.execute(select(Org))
            orgs = result.scalars().all()
            return list(orgs)

    @staticmethod
    async def get_user_orgs_paginated(
        user_id: UUID,
        page_id: str | None = None,
        limit: int = 100,
        name: str | None = None,
    ) -> tuple[list[Org], str | None]:
        """Get paginated list of organizations for a user.

        Args:
            user_id: User UUID
            page_id: Optional page ID (offset as string) for pagination
            limit: Maximum number of organizations to return
            name: Optional exact organization name filter

        Returns:
            Tuple of (list of Org objects, next_page_id or None)
        """
        async with a_session_maker() as session:
            # Build query joining OrgMember with Org
            query = (
                select(Org)
                .join(OrgMember, Org.id == OrgMember.org_id)
                .filter(OrgMember.user_id == user_id)
                .order_by(Org.name)
            )

            # Apply optional exact name filter
            if name is not None:
                query = query.filter(Org.name == name)

            # Apply pagination offset
            if page_id is not None:
                try:
                    offset = int(page_id)
                    query = query.offset(offset)
                except ValueError:
                    # If page_id is not a valid integer, start from beginning
                    offset = 0
            else:
                offset = 0

            # Fetch limit + 1 to check if there are more results
            query = query.limit(limit + 1)
            result = await session.execute(query)
            orgs = list(result.scalars().all())

            # Check if there are more results
            has_more = len(orgs) > limit
            if has_more:
                orgs = orgs[:limit]

            # Calculate next page ID
            next_page_id = None
            if has_more:
                next_page_id = str(offset + limit)

            # Validate org versions
            validated_orgs = []
            for org in orgs:
                if org:
                    validated = await OrgStore._validate_org_version(org)
                    if validated is not None:
                        validated_orgs.append(validated)

            return validated_orgs, next_page_id

    @staticmethod
    def _apply_agent_diff(
        current: dict[str, Any], changes: dict[str, Any]
    ) -> AgentSettingsConfig:
        return apply_agent_settings_diff(
            current or {}, fold_retired_tool_switches(changes, sparse=True)
        )

    @staticmethod
    async def update_org(
        org_id: UUID,
        update_data: OrgUpdate,
        user_id: str | None = None,
    ) -> Optional[Org]:
        """Update organization details from a validated OrgUpdate payload."""
        return await OrgStore._update_org_kwargs(
            org_id,
            update_data.model_update_dict(),
            user_id=user_id,
            update_data=update_data,
        )

    @staticmethod
    async def require_settings_writer(
        session: AsyncSession, org: Org, user_id: str, *, admin: bool
    ) -> None:
        from server.auth.authorization import Permission, has_permission

        role = await session.scalar(
            select(Role)
            .join(OrgMember, OrgMember.role_id == Role.id)
            .where(
                OrgMember.org_id == org.id,
                OrgMember.user_id == UUID(user_id),
                OrgMember.status.is_distinct_from('inactive'),
            )
            .with_for_update(of=OrgMember)
        )
        permission = (
            Permission.EDIT_ORG_SETTINGS
            if admin
            else Permission.MANAGE_APPLICATION_SETTINGS
        )
        if (
            org.status == 'suspended'
            or role is None
            or not has_permission(role, permission)
        ):
            raise PermissionError(
                'Active organization admin or owner required'
                if admin
                else 'Active organization membership required'
            )

    @staticmethod
    async def _update_org_kwargs(
        org_id: UUID,
        org_kwargs: dict[str, Any],
        user_id: str | None = None,
        update_data: OrgUpdate | None = None,
    ) -> Optional[Org]:
        """Internal helper for updating organization fields from raw kwargs."""
        from storage.org_member_store import OrgMemberStore

        org_kwargs = dict(org_kwargs)

        async with a_session_maker() as session:
            result = await session.execute(
                select(Org).filter(Org.id == org_id).with_for_update()
            )
            org = result.scalars().first()
            if not org:
                return None

            if user_id is not None and (
                'member_settings_denials' in org_kwargs
                or (update_data and update_data.restricted_fields())
            ):
                await OrgStore.require_settings_writer(
                    session, org, user_id, admin=True
                )

            if update_data and update_data.settings_edits:
                expected = update_data.org_settings_revision
                if expected is None:
                    raise ValueError('Settings edits require a revision')
                if expected != org.updated_at:
                    raise OrgConcurrentModificationError(
                        str(org.id), expected, org.updated_at
                    )
                blocks = {
                    key: getattr(org, key)
                    for key in ('agent_settings', 'conversation_settings')
                }
                agent = None
                changed = set()
                for edit in update_data.settings_edits:
                    block, *path = edit.path
                    if (
                        not (
                            editable_conversation_path(edit.path)
                            or (
                                editable_member_agent_path(edit.path)
                                and path not in (['tools'], ['condenser'])
                            )
                        )
                        or edit.op not in ('set', 'inherit')
                        or getattr(update_data, f'{block}_diff') is not None
                    ):
                        raise ValueError('Unsupported organization defaults edit')
                    if block == 'agent_settings':
                        if agent is None:
                            agent = OrgStore.get_agent_settings_from_org(org)
                        if agent.agent_kind == 'acp' or (
                            path[0] == 'condenser'
                            and path[1] not in type(agent.condenser).model_fields
                        ):
                            raise ValueError(
                                'Unsupported agent settings path for this variant'
                            )
                    blocks = apply_operation(blocks, edit, sparse=True)
                    changed.add(block)
                org_kwargs.update({block: blocks[block] for block in changed})

            old_name = org.name

            if 'id' in org_kwargs:
                org_kwargs.pop('id')

            agent_settings_diff = (
                update_data.agent_settings_diff if update_data else None
            )
            conversation_settings_diff = (
                update_data.conversation_settings_diff if update_data else None
            )
            for key, value in org_kwargs.items():
                if hasattr(org, key):
                    setattr(org, key, value)

            if agent_settings_diff is not None:
                org.agent_settings = OrgStore._apply_agent_diff(
                    org.agent_settings, agent_settings_diff
                ).model_dump(mode='json', exclude_unset=True)

            if conversation_settings_diff is not None:
                org.conversation_settings = apply_settings_edit(
                    OrgStore.get_conversation_settings_from_org(org),
                    conversation_settings_diff,
                ).model_dump(mode='json', exclude_unset=True)

            if update_data is not None and update_data.touches_llm_defaults():
                if user_id is None:
                    raise ValueError(
                        'user_id is required when updating shared LLM settings'
                    )
                effective_managed_key = await OrgStore._ensure_managed_llm_key_for_user(
                    session, org, user_id
                )
                if (
                    update_data.llm_api_key is not None
                    or effective_managed_key is not None
                ):
                    await OrgMemberStore.update_all_members_settings_async(
                        session,
                        org_id,
                        OrgMemberSettingsUpdate(
                            llm_api_key=update_data.llm_api_key or None,
                            has_custom_llm_api_key=False,
                        ),
                    )

            await session.commit()
            await session.refresh(org)

            # Keep the LiteLLM team_alias in sync with the org's display name so
            # the proxy dashboard stays readable after a rename. Best-effort —
            # never fail an org update because the proxy is briefly unreachable.
            if org.name != old_name:
                try:
                    await LiteLlmManager.update_team(
                        str(org.id),
                        get_org_team_alias(str(org.id), org.name, user_id),
                        None,
                    )
                except Exception:
                    logger.warning(
                        'Failed to propagate org rename to LiteLLM team_alias',
                        extra={'org_id': str(org.id)},
                    )
            return org

    @staticmethod
    def get_kwargs_from_settings(settings: Settings):
        dumped = settings.model_dump(mode='json', context={'expose_secrets': True})
        return {
            field: dumped[field] for field in _ORG_SETTINGS_FIELDS if field in dumped
        }

    @staticmethod
    def get_kwargs_from_user_settings(user_settings: UserSettings):
        kwargs = {
            field: getattr(user_settings, field)
            for field in _ORG_SETTINGS_FIELDS
            if hasattr(user_settings, field)
        }
        if kwargs.get('enable_proactive_conversation_starters') is None:
            kwargs.pop('enable_proactive_conversation_starters', None)
        kwargs['org_version'] = user_settings.user_version
        return kwargs

    @staticmethod
    async def persist_org_with_owner(
        org: Org,
        org_member: OrgMember | None,
    ) -> Org:
        """Persist organization and optional owner membership in a single transaction.

        Args:
            org: Organization entity to persist
            org_member: Optional organization member entity to persist

        Returns:
            Org: The persisted organization object

        Raises:
            Exception: If database operations fail
        """
        async with a_session_maker() as session:
            session.add(org)
            if org_member is not None:
                session.add(org_member)
            await session.commit()
            await session.refresh(org)
            return org

    @staticmethod
    async def delete_org_cascade(
        org_id: UUID, requester_user_id: str | None = None
    ) -> Org | None:
        """Delete organization and all associated data in cascade, including external LiteLLM cleanup.

        Users that belong to the org being deleted are handled in three ways:

        * Users with a membership in at least one other org have their
          ``current_org_id`` reassigned to one of those alternative orgs.
        * If the *requester themselves* is the only orphaned user (sole member
          of the org being deleted — the personal-org self-service case), the
          requester's user row is cascade-deleted in the same transaction. The
          Keycloak account is left untouched, so on the user's next login
          ``UserStore.create_user`` re-onboards them as a brand-new user. The
          new ``User.id`` and ``Org.id`` are derived from the Keycloak ``sub``
          claim, which is stable across logins, so the re-created personal-org
          identity matches the deleted one (``User.id == Org.id ==
          UUID(keycloak.sub)``) and downstream automations that key on
          ``keycloak_user_id`` continue to resolve correctly.
        * If any orphan is **not** the requester (i.e., a multi-user org where
          another member has no other org), ``OrphanedUserError`` is raised
          and the whole transaction is rolled back. The org owner must
          transfer or remove those members before deletion can proceed —
          we refuse to silently destroy accounts that did not consent.
        * Service accounts of the org are deleted with it (user rows,
          memberships and LiteLLM users). A service account belongs to one
          org only, so it is never an orphan.

        Args:
            org_id: UUID of the organization to delete
            requester_user_id: Keycloak ``sub`` of the user initiating the
                deletion. Required for the sole-orphan personal-org case;
                when ``None`` (e.g., internal callers), any orphan triggers
                ``OrphanedUserError``.

        Returns:
            Org: The deleted organization object, or None if not found

        Raises:
            OrphanedUserError: If any non-requester member of the org would be
                left without any organization by the deletion.
            Exception: If database operations or LiteLLM cleanup fail
        """
        async with a_session_maker() as session:
            # First get the organization to return it
            result = await session.execute(select(Org).filter(Org.id == org_id))
            org = result.scalars().first()
            if not org:
                return None

            try:
                # Preflight orphan check — fail fast before any writes.
                #
                # The orphan SELECT only reads ``user`` and ``org_member``,
                # neither of which is modified by the org-data cleanup
                # below, so we hoist it to the top of the transaction. If
                # the check fails we raise immediately and the rollback
                # has essentially no write work to undo.
                #
                # Running it inside the transaction (rather than as a
                # separate pre-flight call before opening the session) is
                # deliberate: it ensures the orphan computation shares the
                # same snapshot/locks as the destructive writes below, so
                # another session cannot create a new orphan between
                # check-and-act.
                #
                # No row-level lock (``FOR UPDATE``) is acquired here. A
                # concurrent session could in principle insert or remove
                # an ``org_member`` row for the requester between this
                # read and the ``DELETE User`` write below; we accept
                # that as an unlikely edge case for the personal-org
                # self-service path, where the requester is the only
                # actor with permission to mutate their own memberships.
                # Promote to ``FOR UPDATE`` if this code starts running
                # on behalf of multi-actor flows.
                orphaned_result = await session.execute(
                    text("""
                        SELECT u.id
                        FROM "user" u
                        WHERE u.current_org_id = :org_id
                        AND u.account_type != 'service'
                        AND NOT EXISTS (
                            SELECT 1 FROM org_member om
                            WHERE om.user_id = u.id AND om.org_id != :org_id
                        )
                    """),
                    {'org_id': str(org_id)},
                )
                orphaned_user_ids = [str(row[0]) for row in orphaned_result.fetchall()]

                # The org's service accounts go with it.
                service_account_result = await session.execute(
                    text("""
                        SELECT u.id
                        FROM "user" u
                        JOIN org_member om ON om.user_id = u.id
                        WHERE om.org_id = :org_id AND u.account_type = 'service'
                    """),
                    {'org_id': str(org_id)},
                )
                service_account_ids = [
                    str(row[0]) for row in service_account_result.fetchall()
                ]

                # Split the orphaned users into the requester and everyone
                # else. Only the requester is cascade-deleted: by calling
                # DELETE on their own org they've consented to losing their
                # account. Other members have not consented, so if any
                # non-requester would be orphaned we raise OrphanedUserError
                # and let the transaction roll back.
                other_orphans = [
                    uid for uid in orphaned_user_ids if uid != requester_user_id
                ]
                if other_orphans:
                    raise OrphanedUserError(other_orphans)

                requester_orphan_ids = [
                    uid for uid in orphaned_user_ids if uid == requester_user_id
                ]
                # Users whose rows this deletion removes (step 3a).
                deleted_user_ids = requester_orphan_ids + service_account_ids

                # 1. Delete conversation data for organization conversations
                await session.execute(
                    text("""
                    DELETE FROM conversation_metadata
                    WHERE conversation_id IN (
                        SELECT conversation_id FROM conversation_metadata_saas WHERE org_id = :org_id
                    )
                    """),
                    {'org_id': str(org_id)},
                )

                await session.execute(
                    text("""
                    DELETE FROM app_conversation_start_task
                    WHERE app_conversation_id IN (
                        SELECT CAST(conversation_id AS UUID) FROM conversation_metadata_saas WHERE org_id = :org_id
                    )
                    """),
                    {'org_id': str(org_id)},
                )

                # 2. Delete organization-owned data tables (direct org_id foreign keys)
                await session.execute(
                    text('DELETE FROM billing_sessions WHERE org_id = :org_id'),
                    {'org_id': str(org_id)},
                )
                await session.execute(
                    text(
                        'DELETE FROM conversation_metadata_saas WHERE org_id = :org_id'
                    ),
                    {'org_id': str(org_id)},
                )
                await session.execute(
                    text('DELETE FROM custom_secrets WHERE org_id = :org_id'),
                    {'org_id': str(org_id)},
                )
                await session.execute(
                    text('DELETE FROM api_keys WHERE org_id = :org_id'),
                    {'org_id': str(org_id)},
                )
                await session.execute(
                    text('DELETE FROM slack_conversation WHERE org_id = :org_id'),
                    {'org_id': str(org_id)},
                )
                await session.execute(
                    text('DELETE FROM slack_users WHERE org_id = :org_id'),
                    {'org_id': str(org_id)},
                )
                await session.execute(
                    text('DELETE FROM stripe_customers WHERE org_id = :org_id'),
                    {'org_id': str(org_id)},
                )

                # Budget rows do not use ON DELETE CASCADE. Overrides also
                # reference user.id, so remove them before the sole-requester
                # path can delete that user below.
                await session.execute(
                    delete(OrgUserBudgetOverride).where(
                        OrgUserBudgetOverride.org_id == org_id
                    )
                )
                await session.execute(
                    delete(OrgBudgetThreshold).where(
                        OrgBudgetThreshold.org_id == org_id
                    )
                )
                await session.execute(
                    delete(OrgBudgetSettings).where(OrgBudgetSettings.org_id == org_id)
                )

                # 3. Handle users with this as current_org_id BEFORE deleting
                # memberships. The orphan partition was computed in the
                # preflight check at the top of this try block;
                # ``requester_orphan_ids`` is set iff the requester is a
                # sole-org member of this org.

                # 3a. Cascade-delete the org's service accounts, and the
                # requester if they are a sole-org user
                # (personal-org self-service path). Their personal-org identity
                # is preserved on re-login because UserStore.create_user derives
                # both User.id and Org.id from the Keycloak ``sub`` claim (which
                # is stable across logins), so a re-onboarded user receives the
                # same UUIDs they had before. Downstream systems keyed on
                # ``keycloak_user_id``/``org_id`` continue to resolve correctly.
                #
                # We must remove the user row BEFORE deleting the org row,
                # because ``user.current_org_id`` is a NOT NULL FK to
                # ``org.id``. That in turn requires releasing other inbound
                # FKs to ``user.id`` first: ``org_invitation`` and
                # ``org_git_claim`` cascade on ``org.id`` in the DB schema,
                # but the org row still exists at this point, so we clean
                # those references explicitly here.
                #
                # FK edges on ``user.id`` cleared before ``DELETE User``:
                #   * ``org_invitation.inviter_id``
                #   * ``org_invitation.accepted_by_user_id``
                #   * ``org_git_claim.claimed_by``
                #   * ``daily_conversation_usage.user_id``
                #   * ``org_member.user_id``
                # *** If a future migration adds another table with a
                # FK to ``user.id`` (e.g. ``user_api_key``, ``audit_log``)
                # this block MUST be updated to release it, or the
                # ``DELETE User`` below will raise a runtime FK violation
                # with no obvious pointer back to this site. ***
                if deleted_user_ids:
                    await session.execute(
                        delete(OrgInvitation).where(
                            OrgInvitation.inviter_id.in_(deleted_user_ids)
                            | OrgInvitation.accepted_by_user_id.in_(deleted_user_ids)
                        )
                    )
                    await session.execute(
                        delete(OrgGitClaim).where(
                            OrgGitClaim.claimed_by.in_(deleted_user_ids)
                        )
                    )
                    await session.execute(
                        delete(DailySandboxUsage).where(
                            DailySandboxUsage.user_id.in_(deleted_user_ids)
                        )
                    )
                    # All of these users' memberships are for the org being
                    # deleted (definition of "orphan"; a service account has
                    # one org) and so would be removed by step 4 anyway, but
                    # we have to release them now to drop the user_id FK
                    # before the user rows go away.
                    await session.execute(
                        delete(OrgMember).where(OrgMember.user_id.in_(deleted_user_ids))
                    )
                    await session.execute(
                        delete(User).where(User.id.in_(deleted_user_ids))
                    )

                # 3b. Batch update: reassign current_org_id to an alternative org
                # for any remaining users. Orphan rows are gone at this point, so
                # the subquery is guaranteed to return a non-null org for every
                # row touched by the UPDATE.
                await session.execute(
                    text("""
                        UPDATE "user"
                        SET current_org_id = (
                            SELECT om.org_id FROM org_member om
                            WHERE om.user_id = "user".id AND om.org_id != :org_id
                            LIMIT 1
                        )
                        WHERE "user".current_org_id = :org_id
                    """),
                    {'org_id': str(org_id)},
                )

                # 4. Delete organization memberships (now safe)
                await session.execute(
                    text('DELETE FROM org_member WHERE org_id = :org_id'),
                    {'org_id': str(org_id)},
                )

                # 5. Finally delete the organization.
                # ``AsyncSession.delete`` is a coroutine; without ``await``
                # it is a silent no-op — the ORM never flushes the DELETE
                # and the ``org`` row survives the transaction even though
                # every preceding step committed. Forgetting the ``await``
                # here would leave the next sign-in colliding on
                # ``org_pkey`` in ``UserStore.create_user``, because both
                # the surviving row and the new row are keyed on the same
                # stable Keycloak ``sub``. Awaited explicitly to make that
                # invariant load-bearing rather than incidental.
                await session.delete(org)

                # 6. Clean up LiteLLM team before committing transaction
                logger.info(
                    'Deleting LiteLLM team within database transaction',
                    extra={'org_id': str(org_id)},
                )
                await LiteLlmManager.delete_team(str(org_id))

                for user_id in deleted_user_ids:
                    await OrgStore._delete_litellm_user_best_effort(user_id, org_id)

                # 7. Commit all changes only if everything succeeded
                await session.commit()

                logger.info(
                    'Successfully deleted organization and all associated data including LiteLLM team',
                    extra={'org_id': str(org_id), 'org_name': org.name},
                )

                return org

            except Exception:
                await session.rollback()
                logger.exception(
                    'Failed to delete organization - transaction rolled back',
                    extra={
                        'org_id': str(org_id),
                    },
                    stack_info=True,
                )
                raise

    @staticmethod
    async def _ensure_managed_llm_key_for_user(
        session,
        updated_org: Org,
        user_id: str,
        *,
        force: bool = False,
        llm: LLM | None = None,
    ) -> str | None:
        """Ensure the acting member has their own managed LLM key.

        ``force`` rotates the slot unconditionally even if the existing key
        looks valid — required when the slot still holds a stale BYOR key,
        which the reuse fast-path below can otherwise hand back as managed.

        ``llm`` overrides the LLM used to classify the key as managed;
        ``activate_profile`` passes the activated profile's LLM so
        classification reflects the profile being switched to, not the org's
        persisted default (which may still point at a prior BYOR profile).
        """
        if llm is not None:
            llm_model = llm.model
            llm_base_url = llm.base_url
            llm_provider_connection_id = getattr(llm, 'provider_connection_id', None)
        else:
            llm_settings = agent_llm(OrgStore.get_agent_settings_from_org(updated_org))
            if llm_settings is None:  # ACP agents make their own model calls
                return None
            llm_model = llm_settings.model
            llm_base_url = llm_settings.base_url
            llm_provider_connection_id = getattr(
                llm_settings, 'provider_connection_id', None
            )
        openhands_type = is_openhands_model(llm_model)
        # Imported locally to avoid a storage-internal circular import.
        from storage.saas_settings_store import managed_llm_key_config_from_model

        config = managed_llm_key_config_from_model(
            llm_model, llm_base_url, llm_provider_connection_id
        )
        if config is None or not config.openhands_type:
            return None

        if not await is_litellm_enabled():
            # LiteLLM is disabled deployment-wide: there is no gateway to
            # verify/rotate/generate a key against. A managed openhands/*
            # model is virtually every existing org's default, so without
            # this guard any org-defaults save would otherwise
            # unconditionally try to mint a key and raise ``ValueError``
            # out of ``LiteLlmManager.generate_key``.
            logger.info(
                'ensure_managed_llm_key_for_user:skipped_litellm_disabled',
                extra={'user_id': user_id, 'org_id': str(updated_org.id)},
            )
            return None

        # _get_effective_llm_api_key checks org.llm_api_key before the member
        # slot, so a stale org-level BYOR key would shadow the rotated member
        # key at launch. Clear it on switch to a managed profile (#421).
        if updated_org.llm_api_key is not None:
            logger.info(
                'Clearing stale org-level BYOR LLM key on switch to managed profile',
                extra={'user_id': user_id, 'org_id': str(updated_org.id)},
            )
            updated_org.llm_api_key = None

        result = await session.execute(
            select(OrgMember).where(
                OrgMember.org_id == updated_org.id,
                OrgMember.user_id == UUID(user_id),
            )
        )
        acting_member = result.scalars().first()
        if acting_member is None:
            logger.error(
                'Acting member row not found during managed LLM key '
                'rotation; skipping managed-key reconciliation.',
                extra={'user_id': user_id, 'org_id': str(updated_org.id)},
            )
            return None

        existing_key = acting_member.llm_api_key
        existing_key_raw = existing_key.get_secret_value() if existing_key else None
        if (
            not force
            and existing_key_raw
            and await LiteLlmManager.verify_existing_key(
                existing_key_raw,
                user_id,
                str(updated_org.id),
                openhands_type=openhands_type,
            )
        ):
            # The key is registered in LiteLLM, but it may still be stale
            # (e.g. revoked server-side). Do a real auth check before reusing it.
            if await LiteLlmManager.verify_key(existing_key_raw, user_id):
                return existing_key_raw
            logger.info(
                'Managed LLM key exists but failed auth verification; '
                'rotating on org-defaults save',
                extra={'user_id': user_id, 'org_id': str(updated_org.id)},
            )

        # One managed key per (user, org) under the same deterministic alias,
        # deleting any prior key first — symmetric across openhands/* and BYOR
        # defaults so switching between them never orphans a key.
        key_alias = get_openhands_cloud_key_alias(user_id, str(updated_org.id))
        await LiteLlmManager.delete_key_by_alias(key_alias=key_alias)
        logger.info(
            'Generated managed LLM key for acting user on org-defaults save',
            extra={'user_id': user_id, 'org_id': str(updated_org.id)},
        )
        generated_key = await LiteLlmManager.generate_key(
            user_id,
            str(updated_org.id),
            key_alias,
            {'type': 'openhands'} if openhands_type else None,
        )
        acting_member.llm_api_key = SecretStr(generated_key)
        return generated_key
