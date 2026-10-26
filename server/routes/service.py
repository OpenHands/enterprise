"""
Service API routes for internal service-to-service communication.

This module provides endpoints for trusted internal services (e.g., automations service)
to perform privileged operations like creating API keys on behalf of users.

Authentication is via a shared secret (X-Service-API-Key header) configured
through the AUTOMATIONS_SERVICE_KEY environment variable.
"""

import os
from typing import Literal, cast
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, field_validator

from openhands.app_server.utils.auth import secrets_equal
from openhands.app_server.utils.logger import openhands_logger as logger
from storage.api_key_store import ApiKeyStore
from storage.org_member_store import OrgMemberStore
from storage.role_store import RoleStore
from storage.user_store import UserStore

# Environment variable for the service API key
AUTOMATIONS_SERVICE_KEY = os.getenv('AUTOMATIONS_SERVICE_KEY', '').strip()

service_router = APIRouter(prefix='/api/service', tags=['Service'])


class CreateUserApiKeyRequest(BaseModel):
    """Request model for creating an API key on behalf of a user."""

    name: str  # Required - used to identify the key

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('name is required and cannot be empty')
        return v.strip()


class CreateUserApiKeyResponse(BaseModel):
    """Response model for created API key."""

    key: str
    user_id: str
    org_id: str
    name: str


MembershipStatus = Literal['active', 'inactive', 'invited', 'disabled']


class OrgMemberInfoResponse(BaseModel):
    """Response model for the org membership endpoint."""

    user_id: str
    org_id: str
    account_type: Literal['human', 'service']
    role: str
    # ``disabled`` when the user is disabled or pending deletion; otherwise the
    # membership status (``active``, ``inactive`` or ``invited``).
    status: MembershipStatus


class ServiceInfoResponse(BaseModel):
    """Response model for service info endpoint."""

    service: str
    authenticated: bool


async def validate_service_api_key(
    x_service_api_key: str | None = Header(default=None, alias='X-Service-API-Key'),
) -> str:
    """
    Validate the service API key from the request header.

    Args:
        x_service_api_key: The service API key from the X-Service-API-Key header

    Returns:
        str: Service identifier for audit logging

    Raises:
        HTTPException: 401 if key is missing or invalid
        HTTPException: 503 if service auth is not configured
    """
    if not AUTOMATIONS_SERVICE_KEY:
        logger.warning(
            'Service authentication not configured (AUTOMATIONS_SERVICE_KEY not set)'
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Service authentication not configured',
        )

    if not x_service_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='X-Service-API-Key header is required',
        )

    if not secrets_equal(x_service_api_key, AUTOMATIONS_SERVICE_KEY):
        logger.warning('Invalid service API key attempted')
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid service API key',
        )

    return 'automations-service'


@service_router.get('/health')
async def service_health() -> dict:
    """Health check endpoint for the service API.

    This endpoint does not require authentication and can be used
    to verify the service routes are accessible.
    """
    return {
        'status': 'ok',
        'service_auth_configured': bool(AUTOMATIONS_SERVICE_KEY),
    }


@service_router.post('/users/{user_id}/orgs/{org_id}/api-keys')
async def get_or_create_api_key_for_user(
    user_id: str,
    org_id: UUID,
    request: CreateUserApiKeyRequest,
    x_service_api_key: str | None = Header(default=None, alias='X-Service-API-Key'),
) -> CreateUserApiKeyResponse:
    """
    Get or create an API key for a user on behalf of the automations service.

    If a key with the given name already exists for the user/org and is not expired,
    returns the existing key. Otherwise, creates a new key.

    The created/returned keys are system keys and are:
    - Not visible to the user in their API keys list
    - Not deletable by the user
    - Never expire

    Args:
        user_id: The user ID
        org_id: The organization ID
        request: Request body containing name (required)
        x_service_api_key: Service API key header for authentication

    Returns:
        CreateUserApiKeyResponse: The API key and metadata

    Raises:
        HTTPException: 401 if service key is invalid
        HTTPException: 404 if user not found
        HTTPException: 403 if user is not a member of the specified org
    """
    # Validate service API key
    service_id = await validate_service_api_key(x_service_api_key)

    # Verify user exists. A user_id that is not a UUID cannot match a user.
    user_uuid: UUID | None
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        user_uuid = None
    user = await UserStore.get_user_by_id(user_id) if user_uuid is not None else None
    if user_uuid is None or not user:
        logger.warning(
            'Service attempted to create key for non-existent user',
            extra={'user_id': user_id},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'User {user_id} not found',
        )

    # Verify user is a member of the specified org
    org_member = await OrgMemberStore.get_org_member(org_id, user_uuid)
    if not org_member:
        logger.warning(
            'Service attempted to create key for user not in org',
            extra={
                'user_id': user_id,
                'org_id': str(org_id),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f'User {user_id} is not a member of org {org_id}',
        )

    # Get or create the system API key
    api_key_store = ApiKeyStore.get_instance()

    try:
        # Canonical form: bearer auth and member removal match keys to
        # org_member rows by str(UUID).
        api_key = await api_key_store.get_or_create_system_api_key(
            user_id=str(user_uuid),
            org_id=org_id,
            name=request.name,
        )
    except Exception as e:
        logger.exception(
            'Failed to get or create system API key',
            extra={
                'user_id': user_id,
                'org_id': str(org_id),
            },
            stack_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to get or create API key',
        ) from e

    logger.info(
        'Service created API key for user',
        extra={
            'service_id': service_id,
            'user_id': user_id,
            'org_id': str(org_id),
            'key_name': request.name,
        },
    )

    return CreateUserApiKeyResponse(
        key=api_key,
        user_id=user_id,
        org_id=str(org_id),
        name=request.name,
    )


@service_router.get('/users/{user_id}/orgs/{org_id}/membership')
async def get_org_member_info(
    user_id: str,
    org_id: UUID,
    x_service_api_key: str | None = Header(default=None, alias='X-Service-API-Key'),
) -> OrgMemberInfoResponse:
    """
    Report whether a user is a member of an org, and what kind of account it is.

    The automations service calls this to tell service accounts from people and
    to check that a member is usable before it acts for them.

    Request:
        GET /api/service/users/{user_id}/orgs/{org_id}/membership
        Header ``X-Service-API-Key``: the shared service key.

    Response contract (200 JSON):
        user_id: str, the user id as given.
        org_id: str, the org id.
        account_type: ``"human"`` or ``"service"``.
        role: str, the org role name (``owner``, ``admin``, ``member`` or
            ``service_account``).
        status: ``"disabled"`` when the user is disabled or pending deletion.
            Otherwise the membership status: ``"active"``, ``"inactive"``
            (suspended) or ``"invited"``. A membership without a status is
            ``"active"``.

    Errors:
        401: the service key is missing or wrong.
        404: the user does not exist, ``user_id`` is not a UUID, or the user
            has no membership in the org. A client treats 404 as "not a member".
        503: service authentication is not configured.
    """
    await validate_service_api_key(x_service_api_key)

    # A user_id that is not a UUID cannot match a user.
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        user_uuid = None
    user = await UserStore.get_user_by_id(user_id) if user_uuid is not None else None
    org_member = (
        await OrgMemberStore.get_org_member(org_id, user_uuid)
        if user is not None and user_uuid is not None
        else None
    )
    if user is None or org_member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'User {user_id} is not a member of org {org_id}',
        )

    role = await RoleStore.get_role_by_id(org_member.role_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Membership role not found',
        )

    return OrgMemberInfoResponse(
        user_id=str(user_uuid),
        org_id=str(org_id),
        account_type='service' if user.account_type == 'service' else 'human',
        role=role.name,
        status='disabled'
        if user.is_disabled or user.deletion_pending
        # Pydantic rejects a stored value outside the Literal.
        else cast(MembershipStatus, org_member.status or 'active'),
    )


@service_router.delete('/users/{user_id}/orgs/{org_id}/api-keys/{key_name}')
async def delete_user_api_key(
    user_id: str,
    org_id: UUID,
    key_name: str,
    x_service_api_key: str | None = Header(default=None, alias='X-Service-API-Key'),
) -> dict:
    """
    Delete a system API key created by the service.

    This endpoint allows the automations service to clean up API keys
    it previously created for users.

    Args:
        user_id: The user ID
        org_id: The organization ID
        key_name: The name of the key to delete (without __SYSTEM__: prefix)
        x_service_api_key: Service API key header for authentication

    Returns:
        dict: Success message

    Raises:
        HTTPException: 401 if service key is invalid
        HTTPException: 404 if key not found
    """
    # Validate service API key
    service_id = await validate_service_api_key(x_service_api_key)

    api_key_store = ApiKeyStore.get_instance()

    # Match the canonical str(UUID) that the mint endpoint stores.
    try:
        stored_user_id = str(UUID(user_id))
    except ValueError:
        stored_user_id = user_id

    # Delete the key by name (wrap with system key prefix since service creates system keys)
    system_key_name = api_key_store.make_system_key_name(key_name)
    success = await api_key_store.delete_api_key_by_name(
        user_id=stored_user_id,
        org_id=org_id,
        name=system_key_name,
        allow_system=True,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'API key with name "{key_name}" not found for user {user_id} in org {org_id}',
        )

    logger.info(
        'Service deleted API key for user',
        extra={
            'service_id': service_id,
            'user_id': user_id,
            'org_id': str(org_id),
            'key_name': key_name,
        },
    )

    return {'message': 'API key deleted successfully'}
