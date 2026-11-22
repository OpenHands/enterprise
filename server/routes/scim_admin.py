"""Admin API for an organization's SCIM setup (``/api/organizations/{org_id}/scim``).

Registered only when ``SCIM_ENABLED`` is set (``saas_server.py``), next to the
``/scim/v2`` routes these credentials authenticate.

Every route needs ``PROVISION_USER`` in ``{org_id}`` (org owner or admin, or an
instance Super Admin), and answers ``400`` for a personal workspace.

Who may bind a provider. ``auth_providers`` rows are instance-wide: nothing
says which organization a provider's directory speaks for. Binding one to an
organization lets that directory's emails link to existing accounts and
pre-create the accounts its logins resolve to, so it is an instance decision:

* The first credential for an organization and provider needs
  ``MANAGE_OAUTH_PROVIDERS``, the Super Admin permission that already governs
  those rows.
* An org owner or admin may create further credentials only for a provider
  the organization has an active credential for, which is what a rotation
  needs. Revoking the last one ends that, until a Super Admin binds again.

A token is returned once, by the ``POST`` that creates it. Only its SHA-256 is
stored, and no route returns that.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, StringConstraints

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import (
    Permission,
    get_user_super_role,
    has_permission,
    require_permission,
)
from server.auth.org_context import REJECT_X_ORG_ID_PATH_MISMATCH
from server.services.scim_user_service import is_scim_provider
from server.utils.url_utils import get_web_url
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import SamlProviderDetails
from storage.auth_provider_store import AuthProviderStore
from storage.default_org_service import is_personal_workspace_org
from storage.org_store import OrgStore
from storage.scim import ScimCredential
from storage.scim_store import (
    ScimCredentialLimitError,
    ScimProviderNotBoundError,
    ScimStore,
)

scim_admin_router = APIRouter(
    prefix='/api/organizations',
    tags=['SCIM'],
    dependencies=[REJECT_X_ORG_ID_PATH_MISMATCH],
)

SCIM_ADMIN = Depends(require_permission(Permission.PROVISION_USER))

_NOT_BOUND = (
    'Only a Super Admin can connect this organization to an SSO provider it has '
    'no active SCIM credential for'
)


class ScimProviderOption(BaseModel):
    id: int
    title: str
    protocol: str
    # Whether the organization has an active credential for this provider.
    bound: bool
    # False means SCIM can neither link nor create accounts through it.
    allow_match_by_email: bool


class ScimSetupResponse(BaseModel):
    base_url: str
    # Whether the caller may bind a provider the organization is not bound to.
    can_bind_provider: bool
    # The providers the caller may create a credential for.
    providers: list[ScimProviderOption]


class ScimCredentialResponse(BaseModel):
    id: UUID
    name: str
    auth_provider_id: int
    auth_provider_title: str | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ScimCredentialPage(BaseModel):
    items: list[ScimCredentialResponse]


class ScimCredentialCreate(BaseModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    auth_provider_id: int


class ScimCredentialCreated(ScimCredentialResponse):
    # Shown once: the server keeps only a hash.
    token: str


async def _require_team_org(org_id: UUID) -> None:
    org = await OrgStore.get_org_by_id(org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Organization not found')
    if is_personal_workspace_org(org):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            'SCIM is available for team organizations only',
        )


async def _can_bind_provider(user_id: str) -> bool:
    role = await get_user_super_role(user_id)
    return bool(
        role and has_permission(role, Permission.MANAGE_OAUTH_PROVIDERS, is_super=True)
    )


def _protocol(provider: AuthProvider) -> str:
    return 'saml' if isinstance(provider.details, SamlProviderDetails) else 'oidc'


def _credential(
    credential: ScimCredential, titles: dict[int, str]
) -> ScimCredentialResponse:
    return ScimCredentialResponse(
        id=credential.id,
        name=credential.name,
        auth_provider_id=credential.auth_provider_id,
        auth_provider_title=titles.get(credential.auth_provider_id),
        created_at=credential.created_at,
        last_used_at=credential.last_used_at,
        revoked_at=credential.revoked_at,
    )


@scim_admin_router.get('/{org_id}/scim', response_model=ScimSetupResponse)
async def get_scim_setup(
    org_id: UUID, request: Request, user_id: str = SCIM_ADMIN
) -> ScimSetupResponse:
    await _require_team_org(org_id)
    can_bind = await _can_bind_provider(user_id)
    bound = await ScimStore.bound_provider_ids(org_id)
    providers = [
        ScimProviderOption(
            id=provider.id,
            title=provider.title,
            protocol=_protocol(provider),
            bound=provider.id in bound,
            allow_match_by_email=provider.allow_match_by_email,
        )
        for provider in await AuthProviderStore().get_idp_providers()
        if is_scim_provider(provider) and (can_bind or provider.id in bound)
    ]
    return ScimSetupResponse(
        base_url=f'{get_web_url(request)}/scim/v2',
        can_bind_provider=can_bind,
        providers=providers,
    )


@scim_admin_router.get('/{org_id}/scim/credentials', response_model=ScimCredentialPage)
async def list_scim_credentials(
    org_id: UUID, _: str = SCIM_ADMIN
) -> ScimCredentialPage:
    await _require_team_org(org_id)
    credentials = await ScimStore.list_credentials(org_id)
    titles = {
        provider.id: provider.title for provider in await AuthProviderStore().list_all()
    }
    return ScimCredentialPage(
        items=[_credential(credential, titles) for credential in credentials]
    )


@scim_admin_router.post(
    '/{org_id}/scim/credentials',
    response_model=ScimCredentialCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_scim_credential(
    org_id: UUID,
    body: ScimCredentialCreate,
    response: Response,
    user_id: str = SCIM_ADMIN,
) -> ScimCredentialCreated:
    await _require_team_org(org_id)
    can_bind = await _can_bind_provider(user_id)
    if not can_bind and body.auth_provider_id not in (
        await ScimStore.bound_provider_ids(org_id)
    ):
        # Decided before the provider is looked up, so the answer is the same
        # for a provider that does not exist.
        raise HTTPException(status.HTTP_403_FORBIDDEN, _NOT_BOUND)
    provider = await AuthProviderStore().get_by_id(body.auth_provider_id)
    if provider is None or not is_scim_provider(provider):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            'SCIM can only be bound to an enabled corporate SSO provider',
        )
    try:
        credential, token = await ScimStore.mint_credential(
            org_id, provider.id, body.name, require_binding=not can_bind
        )
    except ScimProviderNotBoundError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, _NOT_BOUND) from exc
    except ScimCredentialLimitError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            'This organization has the maximum number of active SCIM '
            'credentials; revoke one first',
        ) from exc
    logger.info(
        'scim:credential_created',
        extra={
            'org_id': str(org_id),
            'credential_id': str(credential.id),
            'auth_provider_id': provider.id,
            'user_id': user_id,
        },
    )
    response.headers['Cache-Control'] = 'no-store'
    return ScimCredentialCreated(
        **_credential(credential, {provider.id: provider.title}).model_dump(),
        token=token,
    )


@scim_admin_router.delete(
    '/{org_id}/scim/credentials/{credential_id}',
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_scim_credential(
    org_id: UUID, credential_id: UUID, user_id: str = SCIM_ADMIN
) -> None:
    await _require_team_org(org_id)
    credential = await ScimStore.revoke_credential(org_id, credential_id)
    if credential is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'SCIM credential not found')
    logger.info(
        'scim:credential_revoked',
        extra={
            'org_id': str(org_id),
            'credential_id': str(credential_id),
            'user_id': user_id,
        },
    )
