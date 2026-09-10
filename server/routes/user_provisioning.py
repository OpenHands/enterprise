"""Provision accounts through the shared Enterprise account lifecycle."""

import secrets
import string
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator

from openhands.app_server.user_auth import get_user_auth
from server.auth.authorization import Permission, require_permission
from server.auth.contracts import AuthenticationUnavailable, Principal
from server.auth.http import AuthValidationRoute
from server.auth.mode import is_keycloak_enabled
from server.auth.org_context import EFFECTIVE_ORG_ID
from server.auth.user_management import (
    AccountConflict,
    AccountPermissionError,
    EnterpriseUserManagementService,
)
from storage.org_store import OrgStore

user_provisioning_router = APIRouter(
    prefix='/api/organizations', tags=['Orgs'], route_class=AuthValidationRoute
)
ProvisionedRoleName = Literal['member', 'admin', 'owner']
DEFAULT_PROVISIONED_ROLE: ProvisionedRoleName = 'member'
_GENERATED_PASSWORD_LENGTH = 24


def _generate_password(length: int = _GENERATED_PASSWORD_LENGTH) -> str:
    # Retain compatibility with legacy realm composition requirements.
    alphabet = string.ascii_letters + string.digits + '!@#$%^&*-_=+'
    for _ in range(100):
        value = ''.join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in value)
            and any(c.isupper() for c in value)
            and any(c.isdigit() for c in value)
            and any(c in '!@#$%^&*-_=+' for c in value)
        ):
            return value
    raise RuntimeError('Could not generate an initial password')


class ProvisionUserRequest(BaseModel):
    """Create or reconcile an administrator-provisioned account."""

    email: EmailStr
    password: SecretStr | None = Field(
        default=None,
        repr=False,
        description='Initial password; generated when omitted and ignored for existing accounts. Local accounts require 15–1024 characters and must change it at first login. Keycloak accounts retain the configured realm policy.',
    )
    api_key_name: str | None = Field(default=None, max_length=255)
    role: ProvisionedRoleName = DEFAULT_PROVISIONED_ROLE
    reissue_api_key: bool = False

    @field_validator('password')
    @classmethod
    def validate_password_length(cls, password: SecretStr | None):
        if password is not None:
            minimum, maximum = (8, 256) if is_keycloak_enabled() else (15, 1024)
            if not minimum <= len(password.get_secret_value()) <= maximum:
                raise ValueError('Invalid initial password length')
        return password


# Outcome of the route. ``created`` is True only on a true first-time
# create (case a). On idempotent re-provisions (case b/c) it is False
# and the response status code is 200 OK rather than 201 Created.
ProvisionAction = Literal['created', 'added_to_org', 'reprovisioned']


class ProvisionUserResponse(BaseModel):
    """The initial password is returned only for a newly created account."""

    email: str
    password: str | None = Field(default=None, repr=False)
    api_key: str = Field(repr=False)
    user_id: str
    org_id: str
    role: ProvisionedRoleName = DEFAULT_PROVISIONED_ROLE
    created: bool = True
    action: ProvisionAction = 'created'


@user_provisioning_router.post(
    '/provision-user',
    response_model=ProvisionUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def provision_user(
    body: ProvisionUserRequest,
    response: Response,
    request: Request,
    caller_user_id: str = Depends(require_permission(Permission.PROVISION_USER)),
    target_org_id: UUID = EFFECTIVE_ORG_ID,
) -> ProvisionUserResponse:
    org = await OrgStore.get_org_by_id(target_org_id)
    if org is None:
        raise HTTPException(404, 'Target organization not found')
    # Personal workspace owners cannot turn account provisioning into signup.
    from storage.user_store import UserStore

    if await UserStore.get_user_by_id(str(target_org_id)) is not None:
        raise HTTPException(403, 'Cannot provision users into a personal workspace')
    principal = getattr(await get_user_auth(request), 'principal', None)
    if not isinstance(principal, Principal) or str(principal.user_id) != caller_user_id:
        raise HTTPException(401, 'Authentication required')
    accounts = EnterpriseUserManagementService()
    password = body.password or SecretStr(_generate_password())
    try:
        user, created, added, api_key = await accounts.provision(
            str(body.email),
            password,
            actor=principal,
            organization_id=target_org_id,
            role_name=body.role,
            api_key_name=body.api_key_name or 'Initial API Key',
            reissue_api_key=body.reissue_api_key,
        )
    except AccountPermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except AccountConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except AuthenticationUnavailable as exc:
        raise HTTPException(
            503, 'Account provisioning is incomplete. Retry the request.'
        ) from exc
    action: ProvisionAction = (
        'created' if created else 'added_to_org' if added else 'reprovisioned'
    )
    response.status_code = 201 if created else 200
    return ProvisionUserResponse(
        email=str(body.email).strip().lower(),
        password=password.get_secret_value() if created else None,
        api_key=api_key,
        user_id=str(user.id),
        org_id=str(target_org_id),
        role=body.role,
        created=created,
        action=action,
    )
