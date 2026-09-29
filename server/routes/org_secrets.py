"""API routes for organization-shared custom secrets.

Org-shared secrets are usable by all members of the org in conversations
and automations, but only Admins/Owners (``MANAGE_ORG_SECRETS``) can
create, edit, or delete them. The secret value is write-once: it can be
set on creation and rotated only by deleting and re-creating the secret —
no endpoint returns the raw value.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, SecretStr

from openhands.app_server.secrets.secrets_models import (
    CustomSecretPage,
)
from openhands.app_server.utils.env_var_validation import validate_env_var_name
from openhands.app_server.utils.models import EditResponse
from server.auth.authorization import Permission, require_permission
from storage.org_secrets_store import (
    OrgSecretAlreadyExistsError,
    OrgSecretNotFoundError,
    OrgSecretsStore,
)

org_secrets_router = APIRouter(
    prefix='/api/organizations',
    tags=['Org Secrets'],
)


class OrgSecretCreate(BaseModel):
    """Request body for creating an org-shared secret."""

    name: str
    value: SecretStr
    description: str | None = None

    def validate_name(self) -> None:
        validate_env_var_name(self.name, field_name='secret name')


class OrgSecretUpdate(BaseModel):
    """Request body for updating an org-shared secret (name/description only).

    The secret value is never accepted on update — the write-once,
    never-read guarantee means only metadata can change after creation.
    To rotate the value, delete and re-create the secret.
    """

    name: str | None = None
    description: str | None = None


@org_secrets_router.get(
    '/{org_id}/secrets',
    response_model=CustomSecretPage,
)
async def list_org_secrets(
    org_id: UUID,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> CustomSecretPage:
    """List org-shared secrets (names + descriptions only).

    Available to all org members. Returns ``scope=organization`` for each
    item so the UI can badge them. Secret values are never included.
    """
    store = await OrgSecretsStore.get_instance(org_id)
    items = await store.list_shared()
    return CustomSecretPage(items=items, next_page_id=None)


@org_secrets_router.post(
    '/{org_id}/secrets',
    status_code=status.HTTP_201_CREATED,
    response_model=EditResponse,
)
async def create_org_secret(
    org_id: UUID,
    body: OrgSecretCreate,
    user_id: str = Depends(require_permission(Permission.MANAGE_ORG_SECRETS)),
) -> EditResponse:
    """Create an org-shared secret.

    Admin/Owner only. The value is encrypted at rest and cannot be read
    back by anyone — including the creator.
    """
    body.validate_name()
    store = await OrgSecretsStore.get_instance(org_id)
    try:
        await store.create_shared(
            name=body.name,
            value=body.value.get_secret_value(),
            description=body.description,
            created_by_user_id=user_id,
        )
    except OrgSecretAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return EditResponse(message='Org-shared secret created successfully')


@org_secrets_router.put(
    '/{org_id}/secrets/{secret_name}',
    response_model=EditResponse,
)
async def update_org_secret(
    org_id: UUID,
    secret_name: str,
    body: OrgSecretUpdate,
    user_id: str = Depends(require_permission(Permission.MANAGE_ORG_SECRETS)),
) -> EditResponse:
    """Update an org-shared secret's name and/or description.

    The value is never modified. Admin/Owner only.
    """
    if body.name is not None:
        validate_env_var_name(body.name, field_name='secret name')

    store = await OrgSecretsStore.get_instance(org_id)
    try:
        await store.update_shared(
            current_name=secret_name,
            new_name=body.name,
            description=body.description,
        )
    except OrgSecretNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except OrgSecretAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return EditResponse(message='Org-shared secret updated successfully')


@org_secrets_router.delete(
    '/{org_id}/secrets/{secret_name}',
    response_model=EditResponse,
)
async def delete_org_secret(
    org_id: UUID,
    secret_name: str,
    user_id: str = Depends(require_permission(Permission.MANAGE_ORG_SECRETS)),
) -> EditResponse:
    """Delete an org-shared secret. Admin/Owner only."""
    store = await OrgSecretsStore.get_instance(org_id)
    try:
        await store.delete_shared(secret_name)
    except OrgSecretNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return EditResponse(message='Org-shared secret deleted successfully')
