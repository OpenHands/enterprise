"""Development-only harness for OHE-3773. Not for merge.

Serves the organization Agent Canvas Apps API from this checkout against a
migrated Postgres, so the feature can be exercised from a running Agent Canvas
without a full install. Below the HTTP layer everything is the code the server
runs: the instance switch, the permission checks, the store, the Git fetch,
the feature flag admin API and the web-client config.

Only sign-in is replaced. The ``X-Harness-User`` header names the caller, one
of two users seeded into one organization:

- ``super-admin``: an instance Super Admin who is also a member of the org
- ``member``: a plain member of the org

Usage:

    docker run -d --rm --name ohe-3773-pg -e POSTGRES_PASSWORD=postgres \
        -p 127.0.0.1:55439:5432 postgres:16
    export DB_HOST=127.0.0.1 DB_PORT=55439 DB_USER=postgres DB_PASS=postgres \
        DB_NAME=postgres
    DB_DRIVER=pg8000 WEB_HOST= uv run python -m alembic upgrade head
    PYTHONPATH=. uv run python .pr/ohe-3773/apps_harness.py --port 18773
"""

import argparse
from uuid import UUID

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from sqlalchemy import select

from openhands.app_server.user_auth import get_user_id, reject_service_account
from openhands.app_server.web_client.web_client_router import (
    router as web_client_router,
)
from server.constants import ORG_SETTINGS_VERSION
from server.routes.feature_flags import feature_flag_router
from server.routes.org_canvas_apps import (
    org_canvas_apps_router,
    user_context_dependency,
)
from server.routes.orgs import org_router
from storage.database import session_maker
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User

ORG_ID = UUID('0e377300-0000-4000-8000-000000000001')
USERS = {
    'super-admin': UUID('0e377300-0000-4000-8000-0000000000a1'),
    'member': UUID('0e377300-0000-4000-8000-0000000000b2'),
}


class _NoGitConnections:
    """A caller with no Git provider connected: public repositories only."""

    def __init__(self, user_id: str):
        self._user_id = user_id

    async def get_user_id(self) -> str:
        return self._user_id

    async def get_provider_tokens(self, as_env_vars: bool = False):
        return None


def seed() -> None:
    """Create the organization and its two members once."""
    with session_maker() as session:
        if session.get(Org, ORG_ID) is not None:
            return
        roles = {role.name: role.id for role in session.scalars(select(Role))}
        session.add(
            Org(id=ORG_ID, name='Acme Engineering', org_version=ORG_SETTINGS_VERSION)
        )
        session.flush()
        for name, user_id in USERS.items():
            session.add(
                User(
                    id=user_id,
                    current_org_id=ORG_ID,
                    email=f'{name}@acme.example',
                    role_id=roles['admin'] if name == 'super-admin' else None,
                )
            )
            session.flush()
            session.add(
                OrgMember(
                    org_id=ORG_ID,
                    user_id=user_id,
                    role_id=roles['member'],
                    llm_api_key='unused',
                    status='active',
                )
            )
        session.commit()


async def harness_user(x_harness_user: str = Header()) -> str:
    if x_harness_user not in USERS:
        raise HTTPException(status_code=401, detail='Unknown X-Harness-User')
    return str(USERS[x_harness_user])


async def harness_user_context(x_harness_user: str = Header()) -> _NoGitConnections:
    return _NoGitConnections(await harness_user(x_harness_user))


def build_app() -> FastAPI:
    app = FastAPI(title='OHE-3773 Apps harness')
    app.include_router(org_canvas_apps_router)
    app.include_router(org_router)
    app.include_router(feature_flag_router)
    app.include_router(web_client_router, prefix='/api/v1')
    app.dependency_overrides[get_user_id] = harness_user
    app.dependency_overrides[user_context_dependency.dependency] = harness_user_context
    app.dependency_overrides[reject_service_account] = lambda: None
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18773)
    args = parser.parse_args()
    seed()
    print(f'organization: {ORG_ID}')
    uvicorn.run(build_app(), host='127.0.0.1', port=args.port, log_level='warning')
