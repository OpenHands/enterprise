from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select

from openhands.app_server.utils.jsonpatch_compat import deep_merge_with_wholesale_keys

MIGRATION_TYPES = ('all', 'secrets', 'keys', 'mcp', 'automations')


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
class EnterpriseDeps:
    OrgStore: Any
    UserStore: Any
    OrgMemberStore: Any
    RoleStore: Any
    OrgService: Any
    ApiKeyStore: Any
    ApiKey: Any
    StoredCustomSecrets: Any
    OrgMember: Any
    a_session_maker: Callable[..., Any]


def register_org_commands(subparsers: argparse._SubParsersAction) -> None:
    org_parser = subparsers.add_parser('org', help='Organization operations')
    org_subparsers = org_parser.add_subparsers(dest='org_command')

    migrate_parser = org_subparsers.add_parser(
        'migrate', help='Migrate personal or org data into another org.'
    )
    source_group = migrate_parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        '--from-personal',
        action='store_true',
        help="Use each user's personal org as the migration source.",
    )
    source_group.add_argument(
        '--from',
        dest='source_org',
        help='Source org ID or name for all users.',
    )
    migrate_parser.add_argument(
        '--to',
        dest='target_org',
        required=True,
        help='Target org ID or name.',
    )
    migrate_parser.add_argument(
        '--all',
        action='store_true',
        help='Migrate all users.',
    )
    migrate_parser.add_argument(
        '--file',
        dest='user_file',
        help='Path to file containing user IDs/emails (one per line).',
    )
    migrate_parser.add_argument(
        'user_identifiers',
        nargs='*',
        help='User IDs (UUID) or emails.',
    )
    migrate_parser.add_argument(
        '--type',
        dest='migration_type',
        choices=MIGRATION_TYPES,
        default='all',
        help='Data type to migrate (default: all).',
    )
    migrate_parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show planned changes without writing to the database.',
    )
    migrate_parser.set_defaults(func=run_migrate)


async def run_migrate(args: argparse.Namespace) -> int:
    if not _ensure_enterprise_path():
        print('Enterprise package not found; org migration requires enterprise code.')
        return 1

    deps = _load_enterprise_deps()

    validation_error = _validate_user_selection(args)
    if validation_error:
        print(validation_error)
        return 1

    target_org = await _resolve_org(deps, args.target_org)
    if not target_org:
        print(f'Target org not found: {args.target_org}')
        return 1

    source_org = None
    if args.source_org:
        source_org = await _resolve_org(deps, args.source_org)
        if not source_org:
            print(f'Source org not found: {args.source_org}')
            return 1

    if not args.from_personal and source_org is None:
        print('Source org is required when not using --from-personal.')
        return 1

    try:
        identifiers = _load_identifiers(args)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    users, missing = await _resolve_users(deps, identifiers, args.all)
    for identifier in missing:
        print(f'User not found: {identifier}')

    if not users:
        print('No users to migrate.')
        return 1

    types = _normalize_types(args.migration_type)
    exit_code = 0
    for user in users:
        if args.from_personal:
            source_org_id = user.id
        else:
            assert source_org is not None
            source_org_id = source_org.id
        result = await _migrate_user(
            deps,
            user,
            source_org_id,
            target_org.id,
            types,
            args.dry_run,
        )
        _print_result(result, args.dry_run)
        if result.errors:
            exit_code = 1
    return exit_code


def _ensure_enterprise_path() -> bool:
    repo_root = Path(__file__).resolve().parents[2]
    enterprise_dir = repo_root / 'enterprise'
    if not enterprise_dir.exists():
        return False
    enterprise_path = str(enterprise_dir)
    if enterprise_path not in sys.path:
        sys.path.insert(0, enterprise_path)
    if not os.getenv('OPENHANDS_CONFIG_CLS'):
        os.environ['OPENHANDS_CONFIG_CLS'] = 'server.config.SaaSServerConfig'
    return True


def _load_enterprise_deps() -> EnterpriseDeps:
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

    return EnterpriseDeps(
        OrgStore=OrgStore,
        UserStore=UserStore,
        OrgMemberStore=OrgMemberStore,
        RoleStore=RoleStore,
        OrgService=OrgService,
        ApiKeyStore=ApiKeyStore,
        ApiKey=ApiKey,
        StoredCustomSecrets=StoredCustomSecrets,
        OrgMember=OrgMember,
        a_session_maker=a_session_maker,
    )


def _validate_user_selection(args: argparse.Namespace) -> str | None:
    if args.all:
        if args.user_file or args.user_identifiers:
            return 'Use --all by itself; do not combine with --file or identifiers.'
        return None
    if args.user_file and args.user_identifiers:
        return 'Use --file by itself; do not combine with identifiers.'
    if not args.user_file and not args.user_identifiers:
        return 'Provide --all, --file, or user identifiers.'
    return None


def _load_identifiers(args: argparse.Namespace) -> list[str]:
    identifiers: list[str] = []
    if args.user_file:
        identifiers.extend(_load_identifiers_from_file(args.user_file))
    identifiers.extend(args.user_identifiers or [])
    return _dedupe_list(identifiers)


def _load_identifiers_from_file(path: str) -> list[str]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f'User file not found: {path}')
    identifiers = []
    for line in file_path.read_text().splitlines():
        value = line.strip()
        if not value or value.startswith('#'):
            continue
        identifiers.append(value)
    return identifiers


def _dedupe_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize_types(migration_type: str) -> set[str]:
    if migration_type == 'all':
        return {'secrets', 'keys', 'mcp', 'automations'}
    return {migration_type}


def _try_parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return None


async def _resolve_org(deps: EnterpriseDeps, identifier: str):
    org_id = _try_parse_uuid(identifier)
    if org_id:
        return await deps.OrgStore.get_org_by_id(org_id)
    return await deps.OrgStore.get_org_by_name(identifier)


async def _resolve_users(
    deps: EnterpriseDeps, identifiers: list[str], use_all: bool
) -> tuple[list[Any], list[str]]:
    if use_all:
        return await deps.UserStore.list_users(), []

    users: list[Any] = []
    missing: list[str] = []
    seen_ids: set[str] = set()
    for identifier in identifiers:
        user = None
        user_id = _try_parse_uuid(identifier)
        if user_id:
            user = await deps.UserStore.get_user_by_id(str(user_id))
        else:
            user = await deps.UserStore.get_user_by_email(identifier)
        if not user:
            missing.append(identifier)
            continue
        user_key = str(user.id)
        if user_key in seen_ids:
            continue
        seen_ids.add(user_key)
        users.append(user)
    return users, missing


async def _migrate_user(
    deps: EnterpriseDeps,
    user: Any,
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

    source_org = await deps.OrgStore.get_org_by_id(source_org_id)
    if not source_org:
        result.errors.append(f'Source org not found: {source_org_id}')
        return result

    target_org = await deps.OrgStore.get_org_by_id(target_org_id)
    if not target_org:
        result.errors.append(f'Target org not found: {target_org_id}')
        return result

    source_member = await deps.OrgMemberStore.get_org_member(source_org_id, user.id)
    if not source_member:
        result.errors.append('User is not a member of the source org.')
        return result

    target_member = await deps.OrgMemberStore.get_org_member(target_org_id, user.id)
    if not target_member:
        created_member = await _ensure_target_membership(
            deps, user, target_org_id, dry_run, result
        )
        target_member = created_member or target_member

    user_id = str(user.id)

    async with deps.a_session_maker() as session:
        changed = False

        if 'secrets' in types:
            moved, conflicts = await _migrate_custom_secrets(
                deps,
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
            source_keys = await _fetch_keys(deps, session, user_id, source_org_id)
            target_key_names = await _fetch_key_names(
                deps, session, user_id, target_org_id
            )

            if 'keys' in types:
                moved, conflicts = _migrate_api_keys(
                    deps,
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
                        f'skipped {conflicts} API key(s) due to name conflicts '
                        'in target org'
                    )
                changed = changed or moved > 0

            if 'automations' in types:
                moved, conflicts = _migrate_api_keys(
                    deps,
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
                        f'skipped {conflicts} automation key(s) due to name conflicts '
                        'in target org'
                    )
                changed = changed or moved > 0

            if 'mcp' in types:
                moved, conflicts = _migrate_api_keys(
                    deps,
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
                        f'skipped {conflicts} MCP key(s) due to name conflicts '
                        'in target org'
                    )
                changed = changed or moved > 0

        if 'mcp' in types:
            mcp_result = await _migrate_mcp_config(
                deps,
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
    deps: EnterpriseDeps,
    user: Any,
    target_org_id: UUID,
    dry_run: bool,
    result: MigrationResult,
):
    if dry_run:
        result.actions.append(
            _format_action(dry_run, 'create target org membership (role: member)')
        )
        return None

    role = await deps.RoleStore.get_role_by_name('member')
    if role is None:
        result.errors.append('Role "member" not found; cannot add user to org.')
        return None

    settings = await deps.OrgService.create_litellm_integration(
        target_org_id, str(user.id)
    )
    llm_key = ''
    llm_secret = settings.agent_settings.llm.api_key
    if llm_secret:
        llm_key = llm_secret.get_secret_value()  # type: ignore[union-attr]

    await deps.OrgMemberStore.add_user_to_org(
        org_id=target_org_id,
        user_id=user.id,
        role_id=role.id,
        llm_api_key=llm_key,
        status='active',
        agent_settings_diff={},
        conversation_settings_diff={},
    )
    result.actions.append('Created target org membership (role: member).')
    return await deps.OrgMemberStore.get_org_member(target_org_id, user.id)


async def _migrate_custom_secrets(
    deps: EnterpriseDeps,
    session,
    user_id: str,
    source_org_id: UUID,
    target_org_id: UUID,
    dry_run: bool,
) -> tuple[int, int]:
    result = await session.execute(
        select(deps.StoredCustomSecrets).filter(
            deps.StoredCustomSecrets.keycloak_user_id == user_id,
            deps.StoredCustomSecrets.org_id == source_org_id,
        )
    )
    source_secrets = list(result.scalars().all())
    if not source_secrets:
        return 0, 0

    result = await session.execute(
        select(deps.StoredCustomSecrets.secret_name).filter(
            deps.StoredCustomSecrets.keycloak_user_id == user_id,
            deps.StoredCustomSecrets.org_id == target_org_id,
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
    deps: EnterpriseDeps,
    session,
    user_id: str,
    org_id: UUID,
):
    result = await session.execute(
        select(deps.ApiKey).filter(
            deps.ApiKey.user_id == user_id,
            deps.ApiKey.org_id == org_id,
        )
    )
    return list(result.scalars().all())


async def _fetch_key_names(
    deps: EnterpriseDeps,
    session,
    user_id: str,
    org_id: UUID,
) -> set[str]:
    result = await session.execute(
        select(deps.ApiKey.name).filter(
            deps.ApiKey.user_id == user_id,
            deps.ApiKey.org_id == org_id,
        )
    )
    names = {name for name in result.scalars().all() if name}
    return names


def _migrate_api_keys(
    deps: EnterpriseDeps,
    keys: list[Any],
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
        if not key_filter(deps, key):
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


def _is_user_key(deps: EnterpriseDeps, key) -> bool:
    if key.name == 'MCP_API_KEY':
        return False
    if deps.ApiKeyStore.is_system_key_name(key.name):
        return False
    return True


def _is_mcp_key(deps: EnterpriseDeps, key) -> bool:
    return key.name == 'MCP_API_KEY'


def _is_automation_key(deps: EnterpriseDeps, key) -> bool:
    system_name = deps.ApiKeyStore.make_system_key_name('OPENHANDS_API_KEY')
    return key.name == system_name


async def _migrate_mcp_config(
    deps: EnterpriseDeps,
    session,
    source_org_id: UUID,
    target_org_id: UUID,
    user_id: UUID,
    target_membership_exists: bool,
    dry_run: bool,
) -> str | None:
    result = await session.execute(
        select(deps.OrgMember).filter(
            deps.OrgMember.org_id == source_org_id,
            deps.OrgMember.user_id == user_id,
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
        select(deps.OrgMember).filter(
            deps.OrgMember.org_id == target_org_id,
            deps.OrgMember.user_id == user_id,
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


def _print_result(result: MigrationResult, dry_run: bool) -> None:
    status = 'ERROR' if result.errors else 'OK'
    header = f'[{status}] user {result.user_id}'
    if result.email:
        header += f' ({result.email})'
    print(header)

    for action in result.actions:
        print(f'  - {action}')
    for warning in result.warnings:
        print(f'  ! {warning}')
    for error in result.errors:
        print(f'  x {error}')
