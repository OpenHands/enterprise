from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from server.auth.authorization import Permission, require_permission
from server.auth.org_context import EFFECTIVE_ORG_ID
from server.routes.org_migration_models import (
    OrgMigrationRequest,
    OrgMigrationResponse,
    OrgMigrationUserResult,
)
from server.services.org_migration_service import (
    migrate_users,
    normalize_types,
    resolve_org,
    resolve_users,
)

from openhands.app_server.utils.logger import openhands_logger as logger

org_migration_router = APIRouter(prefix='/api/organizations', tags=['Orgs'])


@org_migration_router.post('/migrations', response_model=OrgMigrationResponse)
async def migrate_org_data(
    payload: OrgMigrationRequest,
    target_org_id: UUID = EFFECTIVE_ORG_ID,
    user_id: str = Depends(require_permission(Permission.PROVISION_USER)),
) -> OrgMigrationResponse:
    logger.info(
        'org_migration_requested',
        extra={
            'user_id': user_id,
            'target_org_id': str(target_org_id),
            'dry_run': payload.dry_run,
            'source_mode': payload.source.mode,
        },
    )

    target_org = await resolve_org(target_org_id)
    if not target_org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Target org not found: {target_org_id}',
        )

    source_org_id = None
    if payload.source.mode == 'org':
        source_identifier = payload.source.org_id_or_name
        assert source_identifier is not None
        source_org = await resolve_org(source_identifier)
        if not source_org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f'Source org not found: {source_identifier}',
            )
        source_org_id = source_org.id

    try:
        types = normalize_types(payload.types)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    identifiers = payload.users or []
    users, missing = await resolve_users(identifiers, payload.all)
    results = await migrate_users(
        users=users,
        source_mode=payload.source.mode,
        source_org_id=source_org_id,
        target_org_id=target_org.id,
        types=types,
        dry_run=payload.dry_run,
    )

    return OrgMigrationResponse(
        dry_run=payload.dry_run,
        results=[OrgMigrationUserResult(**asdict(result)) for result in results],
        missing_identifiers=missing,
    )
