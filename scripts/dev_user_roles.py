#!/usr/bin/env python3
"""
Local development helper for swapping a single user's roles.

Designed for manual QA of org-scoped permissions and the instance-level
super-admin role. It reads a target user id from a **private** environment
variable so you never have to paste a Keycloak ``sub`` onto the command
line or into shell history.

What it does
------------
1. Ensures the target user exists. If not, it creates a minimal user row
   plus the user's personal org and an ``owner`` org_member row (mirrors
   the identity-preservation invariant in ``UserStore.create_user``:
   ``User.id == Org.id == UUID(<keycloak sub>)``).
2. Ensures a shared test organization (``Dev Test Org`` by default) exists
   and that the target user is a member of it. This is the org whose
   membership role gets swapped.
3. Swaps the user's role in the shared org between ``admin`` and ``member``,
   and grants / revokes the instance-level ``superadmin`` role
   (``user.role_id`` -> the ``admin`` role row).

The org-scoped swap and the super-admin grant/revoke go through the real
``OrgMemberStore.update_user_role_in_org`` / ``UserStore.grant_super_admin``
/ ``UserStore.revoke_super_admin`` code paths -- the exact paths you are
testing -- rather than hand-rolled SQL.

Prerequisites
-------------
* A migrated local PostgreSQL (``make local-db``).
* The app's encryption key available. The script resolves this exactly as
  the enterprise config does: ``get_global_config()`` -> ``JwtServiceInjector``
  -> ``get_default_encryption_keys(persistence_dir)``, which reads
  ``.openhands-state/.keys`` (or whatever ``FILE_STORE_PATH`` / ``OH_PERSISTENCE_DIR``
  points at). You do **not** need to set ``JWT_SECRET`` manually -- the same
  ``.keys`` file your ``launch.json``-launched server uses is picked up here.
* ``.env`` populated (``cp .env.template .env && edit``). The script calls
  ``load_dotenv()`` at startup, mirroring ``saas_server.py``, so it picks up
  ``DB_*``, ``DEV_USER_ID``, etc. automatically -- no manual ``source`` needed.
* The target user's id in ``DEV_USER_ID`` (see ``.env.template``), e.g.::

      # in .env (gitignored):
      DEV_USER_ID=<paste-your-keycloak-sub>

Usage
-----
::

      # show current state
      uv run python scripts/dev_user_roles.py status

      # make the user an org admin in the shared test org
      uv run python scripts/dev_user_roles.py org-admin

      # make the user a regular org member
      uv run python scripts/dev_user_roles.py org-member

      # grant instance-level superadmin (user.role_id -> admin role)
      uv run python scripts/dev_user_roles.py superadmin

      # revoke superadmin (refuses if it would remove the last one)
      uv run python scripts/dev_user_roles.py no-superadmin

      # point at a different org
      uv run python scripts/dev_user_roles.py status --org-name "My Other Org"

Notes
-----
* This script is for local development only. It writes directly to the
  database via the app's store layer; do not run it against a shared or
  production environment.
* ``superadmin`` here means the instance-level super role
  (``user.role_id`` referencing the ``admin`` role row), distinct from any
  org-scoped ``admin`` membership. See ``server/auth/authorization.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Allow running as a standalone script (``uv run python scripts/...``) without
# the repo root being on PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy import select

from openhands.app_server.config import get_global_config
from storage.database import a_session_maker
from storage.org import Org
from storage.org_member import OrgMember
from storage.org_member_store import OrgMemberStore
from storage.org_store import OrgStore
from storage.role import Role
from storage.role_store import RoleStore
from storage.user import User
from storage.user_store import SuperAdminRevokeResult, UserStore

DEFAULT_USER_ID_ENV = 'DEV_USER_ID'
DEFAULT_ORG_NAME = 'Dev Test Org'
SUPER_ADMIN_ROLE_NAME = 'admin'  # role row referenced by user.role_id


# --------------------------------------------------------------------------- #
# Bootstrapping the app config (DB engine, JWT/encryption keys).
# --------------------------------------------------------------------------- #


def _ensure_config_bootstrapped() -> None:
    """Load ``.env`` and touch ``get_global_config()`` to init DB/JWT injectors.

    ``load_dotenv()`` mirrors ``saas_server.py``: it picks up ``DB_*``,
    ``DEV_USER_ID``, etc. from ``.env`` automatically when run from a
    terminal (VS Code's launch.json already loads ``envFile``, but a bare
    ``uv run`` invocation does not).

    ``DB_HOST`` is the one DB field without a built-in default in
    ``DbSessionInjector`` (port/name/user/password all default), so we
    fall back to ``localhost`` -- matching ``.vscode/launch.json`` -- so
    the script works out-of-the-box against a ``make local-db`` Postgres
    without duplicating DB config in ``.env``. Any value in ``.env`` or
    the environment wins. The store layer lazily resolves the global
    config on first use; calling it up front surfaces configuration
    errors with a clear traceback before any DB work starts.
    """
    load_dotenv()
    os.environ.setdefault('DB_HOST', 'localhost')
    get_global_config()


# --------------------------------------------------------------------------- #
# Lookup helpers.
# --------------------------------------------------------------------------- #


async def _get_role(name: str) -> Role:
    role = await RoleStore.get_role_by_name(name)
    if role is None:
        raise RuntimeError(
            f'Role {name!r} not found. Run `make local-db` (which applies '
            'migrations) to seed the owner/admin/member role rows.'
        )
    return role


async def _get_user(user_id: uuid.UUID) -> Optional[User]:
    async with a_session_maker() as session:
        result = await session.execute(select(User).filter(User.id == user_id))
        return result.scalars().first()


async def _get_org_by_name(name: str) -> Optional[Org]:
    return await OrgStore.get_org_by_name(name)


# --------------------------------------------------------------------------- #
# Idempotent setup: ensure user + shared org + membership exist.
# --------------------------------------------------------------------------- #


@dataclass
class SetupResult:
    user: User
    personal_org: Org
    shared_org: Org


async def ensure_setup(user_id: uuid.UUID, shared_org_name: str) -> SetupResult:
    """Ensure the user, their personal org, and the shared test org exist.

    Mirrors ``UserStore.create_user``'s identity invariant
    (``User.id == Org.id == UUID(<keycloak sub>)``) but skips the LiteLLM /
    default-settings side effects, which are not needed for role-swapping QA.
    """
    owner_role = await _get_role('owner')

    async with a_session_maker() as session:
        # --- personal org (id == user_id, matching create_user) --------------
        personal_org = await session.get(Org, user_id)
        if personal_org is None:
            personal_org = Org(
                id=user_id,
                name=f'user_{user_id}_org',
                v1_enabled=True,
            )
            session.add(personal_org)
            await session.flush()

        # --- user row --------------------------------------------------------
        user = await _get_user(user_id)
        if user is None:
            user = User(
                id=user_id,
                current_org_id=personal_org.id,
                email=f'dev-{user_id}@example.test',
                email_verified=True,
            )
            session.add(user)
            await session.flush()

        # --- owner membership in personal org -------------------------------
        existing = await session.execute(
            select(OrgMember).filter(
                OrgMember.org_id == personal_org.id,
                OrgMember.user_id == user.id,
            )
        )
        if existing.scalars().first() is None:
            session.add(
                OrgMember(
                    org_id=personal_org.id,
                    user_id=user.id,
                    role_id=owner_role.id,
                    _llm_api_key='',  # encrypted empty; local-only
                    status='active',
                )
            )
        await session.commit()
        await session.refresh(user)

    # --- shared test org ----------------------------------------------------
    shared_org = await _get_org_by_name(shared_org_name)
    if shared_org is None:
        shared_org = await OrgStore.create_org(
            kwargs={
                'name': shared_org_name,
                'v1_enabled': True,
            }
        )

    # membership in the shared org (defaults to member)
    member_role = await _get_role('member')
    membership = await OrgMemberStore.get_org_member(shared_org.id, user.id)
    if membership is None:
        await OrgMemberStore.add_user_to_org(
            org_id=shared_org.id,
            user_id=user.id,
            role_id=member_role.id,
            llm_api_key='',
            status='active',
        )

    return SetupResult(user=user, personal_org=personal_org, shared_org=shared_org)


# --------------------------------------------------------------------------- #
# Role swaps.
# --------------------------------------------------------------------------- #


async def set_org_role(user_id: uuid.UUID, org_id: uuid.UUID, role_name: str) -> None:
    role = await _get_role(role_name)
    updated = await OrgMemberStore.update_user_role_in_org(
        org_id=org_id, user_id=user_id, role_id=role.id
    )
    if updated is None:
        raise RuntimeError(
            f'User {user_id} is not a member of org {org_id}; run `status` first.'
        )


async def grant_superadmin(user_id: uuid.UUID) -> None:
    user = await UserStore.grant_super_admin(str(user_id))
    if user is None:
        raise RuntimeError(f'User {user_id} not found; cannot grant superadmin.')


async def revoke_superadmin(user_id: uuid.UUID) -> None:
    result = await UserStore.revoke_super_admin(str(user_id))
    if result == SuperAdminRevokeResult.LAST_SUPER_ADMIN:
        raise RuntimeError(
            'Refusing to revoke: this is the last superadmin. Grant it to '
            'another user first if you need to demote this one.'
        )
    if result == SuperAdminRevokeResult.NOT_SUPER_ADMIN:
        # Not an error for the user's intent (they wanted no-superadmin).
        return
    if result == SuperAdminRevokeResult.NOT_FOUND:
        raise RuntimeError(f'User {user_id} not found; cannot revoke superadmin.')


# --------------------------------------------------------------------------- #
# Status reporting.
# --------------------------------------------------------------------------- #


async def print_status(user_id: uuid.UUID, shared_org_name: str) -> None:
    setup = await ensure_setup(user_id, shared_org_name)
    user = await _get_user(user_id)
    assert user is not None

    # Super-admin?
    admin_role = await _get_role(SUPER_ADMIN_ROLE_NAME)
    is_superadmin = user.role_id == admin_role.id

    # Membership in shared org.
    membership = await OrgMemberStore.get_org_member(setup.shared_org.id, user.id)
    if membership is None:
        shared_role = '<none>'
    else:
        m_role = await RoleStore.get_role_by_id(membership.role_id)
        shared_role = m_role.name if m_role else f'role_id={membership.role_id}'

    # All org memberships.
    all_memberships = await OrgMemberStore.get_user_orgs(user.id)

    print(f'User            : {user.id}')
    print(f'Email           : {user.email or "<unset>"}')
    print(f'Current org     : {user.current_org_id}')
    print(f'Superadmin      : {"YES" if is_superadmin else "no"}')
    print(f'Shared org      : {setup.shared_org.name} ({setup.shared_org.id})')
    print(f'  role in shared: {shared_role}')
    print(f'Personal org    : {setup.personal_org.name} ({setup.personal_org.id})')
    print(f'All memberships : {len(all_memberships)}')
    for m in all_memberships:
        r = await RoleStore.get_role_by_id(m.role_id)
        print(f'  - org {m.org_id} : {r.name if r else "?"} (status={m.status})')


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #

COMMANDS = {
    'status',
    'org-admin',
    'org-member',
    'superadmin',
    'no-superadmin',
}


def _resolve_user_id(env_var: str) -> uuid.UUID:
    raw = os.getenv(env_var, '').strip()
    if not raw:
        print(
            f'ERROR: target user id not provided. Add it to your .env:\n'
            f'  {env_var}=<your-keycloak-sub>   # in .env (gitignored)\n'
            f'or export it in this shell:\n'
            f'  read -rs {env_var}  # paste the Keycloak sub, press enter\n'
            f'  export {env_var}',
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        return uuid.UUID(raw)
    except ValueError:
        print(f'ERROR: {env_var}={raw!r} is not a valid UUID.', file=sys.stderr)
        sys.exit(2)


async def _run(args: argparse.Namespace) -> int:
    _ensure_config_bootstrapped()
    user_id = _resolve_user_id(args.user_id_env)

    if args.command == 'status':
        await print_status(user_id, args.org_name)
        return 0

    setup = await ensure_setup(user_id, args.org_name)

    if args.command == 'org-admin':
        await set_org_role(user_id, setup.shared_org.id, 'admin')
        print(f'✓ {user_id} is now an org admin in {setup.shared_org.name!r}.')
    elif args.command == 'org-member':
        await set_org_role(user_id, setup.shared_org.id, 'member')
        print(f'✓ {user_id} is now a regular org member in {setup.shared_org.name!r}.')
    elif args.command == 'superadmin':
        await grant_superadmin(user_id)
        print(f'✓ {user_id} is now a superadmin (user.role_id -> admin role).')
    elif args.command == 'no-superadmin':
        await revoke_superadmin(user_id)
        print(f'✓ {user_id} superadmin role revoked (user.role_id cleared).')

    await print_status(user_id, args.org_name)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            'Swap a local dev user between org-admin, org-member, and '
            'instance-level superadmin roles. Reads the target user id from a '
            'private env var (default DEV_USER_ID).'
        ),
    )
    parser.add_argument(
        'command',
        choices=sorted(COMMANDS),
        help=(
            "'status' prints the current state; 'org-admin'/'org-member' swap "
            "the user's role in the shared test org; 'superadmin'/'no-superadmin' "
            'grant/revoke the instance-level super-admin role.'
        ),
    )
    parser.add_argument(
        '--user-id-env',
        default=DEFAULT_USER_ID_ENV,
        help=f'Env var holding the target user UUID (default: {DEFAULT_USER_ID_ENV}).',
    )
    parser.add_argument(
        '--org-name',
        default=DEFAULT_ORG_NAME,
        help=f'Name of the shared test org (default: {DEFAULT_ORG_NAME!r}).',
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == '__main__':
    raise SystemExit(main())
