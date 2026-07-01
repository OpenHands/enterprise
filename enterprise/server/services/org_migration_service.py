from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable
from uuid import UUID

from sqlalchemy import select

from openhands.app_server.utils.jsonpatch_compat import deep_merge_with_wholesale_keys
from storage.api_key import ApiKey
from storage.api_key_store import ApiKeyStore
from storage.database import a_session_maker
from storage.org_member import OrgMember
from storage.org_member_store import OrgMemberStore
from storage.org_service import OrgService
from storage.org_store import OrgStore
from storage.role_store import RoleStore
from storage.stored_custom_secrets import StoredCustomSecrets
from storage.user_store import UserStore

MIGRATION_TYPES = ('secrets', 'keys', 'mcp', 'automations')


@dataclass
class MigrationResult:
    user_id: str
    email: str | None
    source_org_id: UUID
    target_org_id: UUID
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class MigrationRunResult:
    results: list[MigrationResult]
    missing_identifiers: list[str]


def normalize_types(types: Iterable[str] | None) -> set[str]:
    if not types:
        return set(MIGRATION_TYPES)
    normalized = {value.lower() for value in types}
    if 'all' in normalized:
        return set(MIGRATION_TYPES)
    unknown = normalized - set(MIGRATION_TYPES)
    if unknown:
        raise ValueError(f'Unknown migration types: {sorted(unknown)}')
    return normalized


def _try_parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return None


async def resolve_org(identifier: str | UUID):
    org_id = identifier if isinstance(identifier, UUID) else _try_parse_uuid(identifier)
    if org_id:
        return await OrgStore.get_org_by_id(org_id)
    return await OrgStore.get_org_by_name(str(identifier))


async def resolve_users(
    identifiers: list[str], include_all: bool
) -> tuple[list[object], list[str]]:
    if include_all:
        return await UserStore.list_users(), []

    users: list[object] = []
    missing: list[str] = []
    seen_ids: set[str] = set()
    for identifier in identifiers:
        user = None
        user_id = _try_parse_uuid(identifier)
        if user_id:
            user = await UserStore.get_user_by_id(str(user_id))
        else:
            user = await UserStore.get_user_by_email(identifier)
        if not user:
            missing.append(identifier)
            continue
        user_key = str(user.id)
        if user_key in seen_ids:
            continue
        seen_ids.add(user_key)
        users.append(user)
    return users, missing


async def migrate_users(
    *,
    users: list[object],
    source_mode: str,
    source_org_id: UUID | None,
    target_org_id: UUID,
    types: set[str],
    dry_run: bool,
) -> list[MigrationResult]:
    results: list[MigrationResult] = []
    for user in users:
        if source_mode == 'personal':
            resolved_source_org = user.id
        else:
            resolved_source_org = source_org_id
        if resolved_source_org is None:
            result = MigrationResult(
                user_id=str(user.id),
                email=getattr(user, 'email', None),
                source_org_id=target_org_id,
                target_org_id=target_org_id,
                errors=['Source org is required when not using personal mode.'],
            )
            results.append(result)
            continue
        result = await _migrate_user(
            user,
            resolved_source_org,
            target_org_id,
            types,
            dry_run,
        )
        results.append(result)
    return results


async def _migrate_user(
    user: object,
    source_org_id: UUID,
    target_org_id: UUID,
    types: set[str],
    dry_run: bool,
) -> MigrationResult:
    result = MigrationResult(
        user_id=str(user.id),
        email=getattr(user, 'email', None),
        source_org_id=source_org_id,
        target_org_id=target_org_id,
    )

    if source_org_id == target_org_id:
        result.errors.append('Source and target orgs are the same.')
        return result

    source_org = await OrgStore.get_org_by_id(source_org_id)
    if not source_org:
        result.errors.append(f'Source org not found: {source_org_id}')
        return result

    target_org = await OrgStore.get_org_by_id(target_org_id)
    if not target_org:
        result.errors.append(f'Target org not found: {target_org_id}')
        return result

    source_member = await OrgMemberStore.get_org_member(source_org_id, user.id)
    if not source_member:
        result.errors.append('User is not a member of the source org.')
        return result

    target_member = await OrgMemberStore.get_org_member(target_org_id, user.id)
    if not target_member:
        created_member = await _ensure_target_membership(
            user, target_org_id, dry_run, result
        )
        target_member = created_member or target_member

    user_id = str(user.id)

    async with a_session_maker() as session:
        changed = False

        if 'secrets' in types:
            moved, conflicts = await _migrate_custom_secrets(
                session,
                user_id,
                source_org_id,
                target_org_id,
                dry_run,
            )
            if moved:
                result.actions.append(
                    _format_action(dry_run, f'migrated {moved} secret(s)')
                )
            if conflicts:
                result.warnings.append(
                    f'skipped {conflicts} secret(s) due to name conflicts in target org'
                )
            changed = changed or moved > 0

        if {'keys', 'mcp', 'automations'} & types:
            source_keys = await _fetch_keys(session, user_id, source_org_id)
            target_key_names = await _fetch_key_names(
                session, user_id, target_org_id
            )

            if 'keys' in types:
                moved, conflicts = _migrate_api_keys(
                    source_keys,
                    target_key_names,
                    source_org_id,
                    target_org_id,
                    key_filter=_is_user_key,
                    dry_run=dry_run,
                )
                if moved:
                    result.actions.append(
                        _format_action(dry_run, f'migrated {moved} API key(s)')
                    )
                if conflicts:
                    result.warnings.append(
                        f'skipped {conflicts} API key(s) due to name conflicts in target org'
                    )
                changed = changed or moved > 0

            if 'automations' in types:
                moved, conflicts = _migrate_api_keys(
                    source_keys,
                    target_key_names,
                    source_org_id,
                    target_org_id,
                    key_filter=_is_automation_key,
                    dry_run=dry_run,
                )
                if moved:
                    result.actions.append(
                        _format_action(dry_run, f'migrated {moved} automation key(s)')
                    )
                if conflicts:
                    result.warnings.append(
                        f'skipped {conflicts} automation key(s) due to name conflicts in target org'
                    )
                changed = changed or moved > 0

            if 'mcp' in types:
                moved, conflicts = _migrate_api_keys(
                    source_keys,
                    target_key_names,
                    source_org_id,
                    target_org_id,
                    key_filter=_is_mcp_key,
                    dry_run=dry_run,
                )
                if moved:
                    result.actions.append(
                        _format_action(dry_run, f'migrated {moved} MCP key(s)')
                    )
                if conflicts:
                    result.warnings.append(
                        f'skipped {conflicts} MCP key(s) due to name conflicts in target org'
                    )
                changed = changed or moved > 0

        if 'mcp' in types:
            mcp_result = await _migrate_mcp_config(
                session,
                source_org_id,
                target_org_id,
                user.id,
                target_member is not None or dry_run,
                dry_run,
            )
            if mcp_result == 'migrated':
                result.actions.append(_format_action(dry_run, 'migrated MCP config'))
                changed = True
            elif mcp_result == 'conflict':
                result.warnings.append('skipped MCP config due to target conflict')
            elif mcp_result == 'no-target':
                result.warnings.append(
                    'skipped MCP config because target membership is missing'
                )

        if changed and not dry_run:
            await session.commit()

    return result


async def _ensure_target_membership(
    user: object,
    target_org_id: UUID,
    dry_run: bool,
    result: MigrationResult,
):
    if dry_run:
        result.actions.append(
            _format_action(dry_run, 'create target org membership (role: member)')
        )
        return None

    role = await RoleStore.get_role_by_name('member')
    if role is None:
        result.errors.append('Role "member" not found; cannot add user to org.')
        return None

    settings = await OrgService.create_litellm_integration(
        target_org_id, str(user.id)
    )
    llm_key = ''
    llm_secret = settings.agent_settings.llm.api_key
    if llm_secret:
        llm_key = llm_secret.get_secret_value()  # type: ignore[union-attr]

    await OrgMemberStore.add_user_to_org(
        org_id=target_org_id,
        user_id=user.id,
        role_id=role.id,
        llm_api_key=llm_key,
        status='active',
        agent_settings_diff={},
        conversation_settings_diff={},
    )
    result.actions.append('Created target org membership (role: member).')
    return await OrgMemberStore.get_org_member(target_org_id, user.id)


async def _migrate_custom_secrets(
    session,
    user_id: str,
    source_org_id: UUID,
    target_org_id: UUID,
    dry_run: bool,
) -> tuple[int, int]:
    result = await session.execute(
        select(StoredCustomSecrets).filter(
            StoredCustomSecrets.keycloak_user_id == user_id,
            StoredCustomSecrets.org_id == source_org_id,
        )
    )
    source_secrets = list(result.scalars().all())
    if not source_secrets:
        return 0, 0

    result = await session.execute(
        select(StoredCustomSecrets.secret_name).filter(
            StoredCustomSecrets.keycloak_user_id == user_id,
            StoredCustomSecrets.org_id == target_org_id,
        )
    )
    target_names = set(result.scalars().all())

    moved = 0
    conflicts = 0
    for secret in source_secrets:
        if secret.secret_name in target_names:
            conflicts += 1
            continue
        moved += 1
        if not dry_run:
            secret.org_id = target_org_id
    return moved, conflicts


async def _fetch_keys(
    session,
    user_id: str,
    org_id: UUID,
):
    result = await session.execute(
        select(ApiKey).filter(
            ApiKey.user_id == user_id,
            ApiKey.org_id == org_id,
        )
    )
    return list(result.scalars().all())


async def _fetch_key_names(
    session,
    user_id: str,
    org_id: UUID,
) -> set[str]:
    result = await session.execute(
        select(ApiKey.name).filter(
            ApiKey.user_id == user_id,
            ApiKey.org_id == org_id,
        )
    )
    names = {name for name in result.scalars().all() if name}
    return names


def _migrate_api_keys(
    keys: list[object],
    target_key_names: set[str],
    source_org_id: UUID,
    target_org_id: UUID,
    key_filter,
    dry_run: bool,
) -> tuple[int, int]:
    moved = 0
    conflicts = 0
    for key in keys:
        if key.org_id != source_org_id:
            continue
        if not key_filter(key):
            continue
        if key.name and key.name in target_key_names:
            conflicts += 1
            continue
        moved += 1
        if key.name:
            target_key_names.add(key.name)
        if not dry_run:
            key.org_id = target_org_id
    return moved, conflicts


def _is_user_key(key) -> bool:
    if key.name == 'MCP_API_KEY':
        return False
    if ApiKeyStore.is_system_key_name(key.name):
        return False
    return True


def _is_mcp_key(key) -> bool:
    return key.name == 'MCP_API_KEY'


def _is_automation_key(key) -> bool:
    system_name = ApiKeyStore.make_system_key_name('OPENHANDS_API_KEY')
    return key.name == system_name


async def _migrate_mcp_config(
    session,
    source_org_id: UUID,
    target_org_id: UUID,
    user_id: UUID,
    target_membership_exists: bool,
    dry_run: bool,
) -> str | None:
    result = await session.execute(
        select(OrgMember).filter(
            OrgMember.org_id == source_org_id,
            OrgMember.user_id == user_id,
        )
    )
    source_member = result.scalars().first()
    if not source_member:
        return None

    source_agent_settings = dict(source_member.agent_settings_diff or {})
    source_mcp = source_agent_settings.get('mcp_config')
    if not source_mcp:
        return None

    if not target_membership_exists:
        return 'no-target'

    result = await session.execute(
        select(OrgMember).filter(
            OrgMember.org_id == target_org_id,
            OrgMember.user_id == user_id,
        )
    )
    target_member = result.scalars().first()
    if not target_member:
        return 'migrated' if dry_run else 'no-target'

    target_agent_settings = dict(target_member.agent_settings_diff or {})
    if target_agent_settings.get('mcp_config'):
        return 'conflict'

    if not dry_run:
        target_member.agent_settings_diff = deep_merge_with_wholesale_keys(
            target_agent_settings,
            {'mcp_config': source_mcp},
        )
        source_agent_settings.pop('mcp_config', None)
        source_member.agent_settings_diff = source_agent_settings
    return 'migrated'


def _format_action(dry_run: bool, text: str) -> str:
    return f'Would {text}.' if dry_run else f'{text}.'
