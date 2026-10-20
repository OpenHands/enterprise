"""Conditional atomic replacement of organization-shared secret values.

The existing metadata PUT remains metadata-only. Version tokens are random UUIDs,
not hashes of secret values. No endpoint here returns a secret value.
"""

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from server.auth.authorization import Permission, require_permission
from storage.org_secrets_store import (
    OrgSecretConflictError,
    OrgSecretNotFoundError,
    OrgSecretsStore,
)


class SecretValueRoute(APIRoute):
    """Avoid reflecting raw credential inputs or error contexts in HTTP 422s."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise HTTPException(
                    422,
                    detail=[
                        {
                            'type': error['type'],
                            'loc': ['body']
                            if error['type'] == 'extra_forbidden'
                            else error['loc'],
                            'msg': error['msg'],
                        }
                        for error in exc.errors()
                    ],
                ) from None

        return handle


class OrgSecretValueReplacement(BaseModel):
    """Write-only value plus an optimistic version and a unique attempt ID."""

    model_config = ConfigDict(extra='forbid')
    value: SecretStr
    expected_version: UUID
    request_id: UUID

    @field_validator('value')
    @classmethod
    def validate_utf8(cls, value: SecretStr) -> SecretStr:
        try:
            value.get_secret_value().encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('Secret value must be valid UTF-8 text') from None
        return value


class OrgSecretValueVersion(BaseModel):
    version: UUID


org_secret_values_router = APIRouter(route_class=SecretValueRoute)


@org_secret_values_router.get(
    '/{org_id}/secrets/{secret_name}/version', response_model=OrgSecretValueVersion
)
async def get_org_secret_value_version(
    org_id: UUID,
    secret_name: str,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> OrgSecretValueVersion:
    """Read the current revision, without reading or revealing its value."""
    store = await OrgSecretsStore.get_instance(org_id)
    try:
        return OrgSecretValueVersion(version=await store.get_value_version(secret_name))
    except OrgSecretNotFoundError:
        raise HTTPException(404, detail='Shared secret not found') from None
    except OrgSecretConflictError:
        raise HTTPException(
            409, detail='Shared secret name is ambiguous; reconcile before replacement'
        ) from None


@org_secret_values_router.put(
    '/{org_id}/secrets/{secret_name}/value', response_model=OrgSecretValueVersion
)
async def replace_org_secret_value(
    org_id: UUID,
    secret_name: str,
    body: OrgSecretValueReplacement,
    user_id: str = Depends(require_permission(Permission.MANAGE_ORG_SECRETS)),
) -> OrgSecretValueVersion:
    """Replace the value if its version still matches, without deleting the row.

    Retry with identical fields after a lost response. The latest matching
    receipt returns the same revision; stale or inconsistent retries return
    409 with no write. After a later replacement, reconcile rather than retrying
    an old operation with the newly read version. Running conversations can
    retain earlier snapshots; this is not a live-refresh or revocation API.
    """
    store = await OrgSecretsStore.get_instance(org_id)
    try:
        version = await store.replace_shared_value(
            secret_name,
            body.value.get_secret_value(),
            body.expected_version,
            body.request_id,
        )
    except OrgSecretNotFoundError:
        raise HTTPException(404, detail='Shared secret not found') from None
    except OrgSecretConflictError:
        raise HTTPException(
            409,
            detail='Secret changed or request conflicts; read the current version and reconcile',
        ) from None
    return OrgSecretValueVersion(version=version)
