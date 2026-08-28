from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

MIGRATION_TYPES = ('secrets', 'keys', 'mcp', 'automations')
MIGRATION_CHOICES = ('all', *MIGRATION_TYPES)


def _load_migration_service() -> Any:
    from server.services import org_migration_service

    return org_migration_service


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
        choices=MIGRATION_CHOICES,
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

    migration_service = _load_migration_service()

    validation_error = _validate_user_selection(args)
    if validation_error:
        print(validation_error)
        return 1

    target_org = await migration_service.resolve_org(args.target_org)
    if not target_org:
        print(f'Target org not found: {args.target_org}')
        return 1

    source_org = None
    if args.source_org:
        source_org = await migration_service.resolve_org(args.source_org)
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

    users, missing = await migration_service.resolve_users(identifiers, args.all)
    for identifier in missing:
        print(f'User not found: {identifier}')

    if not users:
        print('No users to migrate.')
        return 1

    types = _normalize_types(args.migration_type)
    results = await migration_service.migrate_users(
        users=users,
        source_mode='personal' if args.from_personal else 'org',
        source_org_id=source_org.id if source_org else None,
        target_org_id=target_org.id,
        types=types,
        dry_run=args.dry_run,
    )
    exit_code = 0
    for result in results:
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
        return set(MIGRATION_TYPES)
    return {migration_type}


def _print_result(result: Any, _dry_run: bool) -> None:
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
