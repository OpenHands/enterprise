"""Super-admin CRUD for runtime-api warm runtime configs.

Every endpoint requires ``Permission.MANAGE_WARM_RUNTIMES``, granted only to the
``superadmin`` super role. runtime-api stays the source of truth for the configs:
these routes proxy to it with the app's runtime-api key (see
``server.services.warm_runtime_admin_service``). The one thing stored here is
who may use a config: a config can be marked private and granted to orgs (see
``storage.sandbox_spec_access``).

A config's ``source`` is ``db`` when saved through runtime-api's API and
``file`` when it comes from the installer-managed ConfigMap. Saving a ``file``
config stores a database entry that takes precedence; deleting that entry
reverts to the ConfigMap. Only ``db`` configs can be deleted.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import Permission, require_permission
from server.services.warm_runtime_admin_service import (
    WarmRuntimeAdminError,
    warm_runtime_admin_service,
)
from storage import sandbox_spec_access_store
from storage.database import a_session_maker

warm_runtime_admin_router = APIRouter(prefix='/api/admin/warm-runtimes', tags=['Admin'])

NAME_PATTERN = r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$'

RuntimeName = Annotated[str, Field(pattern=NAME_PATTERN)]


class ResourceValues(BaseModel):
    model_config = ConfigDict(extra='forbid')

    cpu: str | None = None
    memory: str | None = None
    ephemeral_storage: str | None = None


class ResourceRequirements(BaseModel):
    model_config = ConfigDict(extra='forbid')

    requests: ResourceValues | None = None
    limits: ResourceValues | None = None


class WarmRuntimeFields(BaseModel):
    image: str = Field(min_length=1)
    working_dir: str = Field(min_length=1)
    command: list[str] = Field(default_factory=list)
    args: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    resources: ResourceRequirements | None = None
    idle_seconds: int | None = Field(
        default=None,
        gt=0,
        description='Idle timeout for runtimes started from this config.',
    )
    persistent_storage_size: str | None = None
    count: int | None = Field(default=None, ge=0)
    run_as_user: int | None = None
    run_as_group: int | None = None
    fs_group: int | None = None
    fuse_s3_mount: bool | None = None


class WarmRuntimeResponse(WarmRuntimeFields):
    name: str
    source: str | None = None
    is_private: bool = False


class WarmRuntimeListResponse(BaseModel):
    configs: list[WarmRuntimeResponse]


class CreateWarmRuntimeRequest(WarmRuntimeFields):
    name: RuntimeName


class AccessOrg(BaseModel):
    id: UUID
    name: str


class WarmRuntimeAccessResponse(BaseModel):
    is_private: bool
    orgs: list[AccessOrg]


class WarmRuntimeAccessUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    is_private: bool
    org_ids: list[UUID] = Field(
        default_factory=list,
        description='Orgs that may use the runtime. Ignored when it is not private.',
    )


def _to_http_error(exc: WarmRuntimeAdminError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _payload(fields: WarmRuntimeFields) -> dict[str, Any]:
    return fields.model_dump(exclude={'name'}, exclude_none=True)


async def _save(
    name: str, fields: WarmRuntimeFields, caller_user_id: str, operation: str
) -> WarmRuntimeResponse:
    try:
        saved = await warm_runtime_admin_service.save_config(name, _payload(fields))
    except WarmRuntimeAdminError as exc:
        raise _to_http_error(exc) from exc
    logger.info(
        'warm_runtimes:saved',
        extra={
            'caller_user_id': caller_user_id,
            'runtime_name': name,
            'operation': operation,
        },
    )
    return WarmRuntimeResponse.model_validate(saved)


async def _existing() -> dict[str, dict[str, Any]]:
    try:
        configs = await warm_runtime_admin_service.list_configs()
    except WarmRuntimeAdminError as exc:
        raise _to_http_error(exc) from exc
    return {config['name']: config for config in configs}


@warm_runtime_admin_router.get('')
async def list_warm_runtimes(
    _: str = Depends(require_permission(Permission.MANAGE_WARM_RUNTIMES)),
) -> WarmRuntimeListResponse:
    configs = await _existing()
    async with a_session_maker() as session:
        private = {
            rule.sandbox_name
            for rule in await sandbox_spec_access_store.list_rules(session)
            if rule.is_private
        }
    return WarmRuntimeListResponse(
        configs=[
            WarmRuntimeResponse.model_validate(
                {**config, 'is_private': name in private}
            )
            for name, config in configs.items()
        ]
    )


@warm_runtime_admin_router.post('', status_code=status.HTTP_201_CREATED)
async def create_warm_runtime(
    body: CreateWarmRuntimeRequest,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_WARM_RUNTIMES)),
) -> WarmRuntimeResponse:
    if body.name in await _existing():
        raise HTTPException(
            status.HTTP_409_CONFLICT, f'Warm runtime "{body.name}" already exists'
        )
    return await _save(body.name, body, caller_user_id, 'create')


@warm_runtime_admin_router.put('/{name}')
async def update_warm_runtime(
    name: RuntimeName,
    body: WarmRuntimeFields,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_WARM_RUNTIMES)),
) -> WarmRuntimeResponse:
    if name not in await _existing():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f'Warm runtime "{name}" not found'
        )
    return await _save(name, body, caller_user_id, 'update')


@warm_runtime_admin_router.delete('/{name}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_warm_runtime(
    name: RuntimeName,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_WARM_RUNTIMES)),
) -> Response:
    existing = (await _existing()).get(name)
    if existing is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f'Warm runtime "{name}" not found'
        )
    if existing.get('source') == 'file':
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f'Warm runtime "{name}" is defined in the runtime-api ConfigMap and '
            'cannot be deleted here',
        )
    try:
        await warm_runtime_admin_service.delete_config(name)
    except WarmRuntimeAdminError as exc:
        raise _to_http_error(exc) from exc
    # Deleting a saved override can bring back the ConfigMap runtime of the same
    # name, which must keep its access rule.
    if name not in await _existing():
        async with a_session_maker() as session:
            await sandbox_spec_access_store.delete_rule(session, name)
            await session.commit()
    logger.info(
        'warm_runtimes:deleted',
        extra={'caller_user_id': caller_user_id, 'runtime_name': name},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@warm_runtime_admin_router.get('/{name}/access')
async def get_warm_runtime_access(
    name: RuntimeName,
    _: str = Depends(require_permission(Permission.MANAGE_WARM_RUNTIMES)),
) -> WarmRuntimeAccessResponse:
    if name not in await _existing():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f'Warm runtime "{name}" not found'
        )
    async with a_session_maker() as session:
        rule = next(
            (
                rule
                for rule in await sandbox_spec_access_store.list_rules(session)
                if rule.sandbox_name == name
            ),
            None,
        )
        if rule is None or not rule.is_private:
            return WarmRuntimeAccessResponse(is_private=False, orgs=[])
        names = await sandbox_spec_access_store.get_org_names(session, rule.org_ids)
    return WarmRuntimeAccessResponse(
        is_private=True,
        orgs=sorted(
            (AccessOrg(id=org_id, name=org_name) for org_id, org_name in names.items()),
            key=lambda org: org.name.lower(),
        ),
    )


@warm_runtime_admin_router.put('/{name}/access')
async def update_warm_runtime_access(
    name: RuntimeName,
    body: WarmRuntimeAccessUpdate,
    caller_user_id: str = Depends(require_permission(Permission.MANAGE_WARM_RUNTIMES)),
) -> WarmRuntimeAccessResponse:
    if name not in await _existing():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f'Warm runtime "{name}" not found'
        )
    org_ids = set(body.org_ids) if body.is_private else set()
    async with a_session_maker() as session:
        names = await sandbox_spec_access_store.get_org_names(session, org_ids)
        unknown = org_ids - names.keys()
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                'Unknown organizations: ' + ', '.join(sorted(map(str, unknown))),
            )
        await sandbox_spec_access_store.set_rule(
            session, name, body.is_private, org_ids
        )
        await session.commit()
    logger.info(
        'warm_runtimes:access_updated',
        extra={
            'caller_user_id': caller_user_id,
            'runtime_name': name,
            'is_private': body.is_private,
            'org_count': len(org_ids),
        },
    )
    return WarmRuntimeAccessResponse(
        is_private=body.is_private,
        orgs=sorted(
            (AccessOrg(id=org_id, name=org_name) for org_id, org_name in names.items()),
            key=lambda org: org.name.lower(),
        ),
    )
