"""Admin REST API for the ``oauth_providers`` table.

Exposes CRUD for OAuth/OIDC provider configuration rows -- login IDPs
(``is_idp=True``) and git providers (``is_idp=False``), see
``storage.oauth_provider`` for the row shape. Every endpoint requires the
``Permission.MANAGE_OAUTH_PROVIDERS`` permission, which -- like
``MANAGE_SUPER_ADMINS`` and ``MANAGE_FEATURE_FLAGS`` -- is granted only to
the ``superadmin`` super role (see ``server.auth.authorization``): no
org-scoped role can reach these routes, so an org owner cannot reconfigure
instance-wide OAuth/OIDC endpoints.

``client_secret`` is write-only: responses never echo the secret back, only
whether one is configured (``has_client_secret``), mirroring how the
dashboard already treats other secrets (API keys, LLM keys). To clear a
configured secret, send ``client_secret: ""`` on update; omit the field
entirely to leave it untouched.

**Integrated/password IDP invariant.** At most one row may have
``provider_category == INTEGRATED_IDP_CATEGORY``, and that row must always
have ``is_idp=True`` -- see ``_validate_integrated_idp_invariant`` and
migration 185 (which adds the matching DB-level unique index and check
constraint; this validation exists only to turn a would-be ``IntegrityError``
into a clean ``400``/``409``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import Permission, require_permission
from storage.oauth_provider import (
    DEFAULT_PERMITTED_DRIFT_SECONDS,
    INTEGRATED_IDP_CATEGORY,
    OAuthProvider,
)
from storage.oauth_provider_store import OAuthProviderStore
from storage.oauth_provider_user_store import OAuthProviderUserStore
from storage.oauth_token_store import wrap_token

oauth_provider_admin_router = APIRouter(
    prefix='/api/admin/oauth-providers', tags=['Admin']
)


class OAuthProviderResponse(BaseModel):
    """An ``oauth_providers`` row, with ``client_secret`` masked."""

    id: int
    provider_category: str
    display_name: str
    is_idp: bool
    client_id: str
    has_client_secret: bool
    authorization_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    scopes: list[str] | None = None
    permitted_drift_seconds: int
    created_at: datetime
    updated_at: datetime


class CreateOAuthProviderRequest(BaseModel):
    """Fields required to create a new ``oauth_providers`` row."""

    provider_category: str = Field(
        ...,
        min_length=1,
        description=(
            "Reuses the ProviderType enum values ('github', 'gitlab', "
            "'bitbucket', 'bitbucket_data_center', 'forgejo', "
            "'azure_devops', 'enterprise_sso'), plus 'integrated_idp' for "
            'the built-in password IDP.'
        ),
    )
    display_name: str = Field(..., min_length=1)
    is_idp: bool = Field(
        default=False, description='True for login IDPs, False for git providers.'
    )
    client_id: str = Field(..., min_length=1)
    client_secret: str | None = Field(
        default=None, description='Stored encrypted at rest; never read back.'
    )
    authorization_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    scopes: list[str] | None = None
    permitted_drift_seconds: int = Field(default=DEFAULT_PERMITTED_DRIFT_SECONDS, ge=0)


class UpdateOAuthProviderRequest(BaseModel):
    """Partial update: only fields explicitly set in the request are changed.

    Send ``client_secret: ""`` to clear an existing secret; omit the field
    to leave it untouched.
    """

    provider_category: str | None = Field(default=None, min_length=1)
    display_name: str | None = Field(default=None, min_length=1)
    is_idp: bool | None = None
    client_id: str | None = Field(default=None, min_length=1)
    client_secret: str | None = None
    authorization_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    scopes: list[str] | None = None
    permitted_drift_seconds: int | None = Field(default=None, ge=0)


async def _validate_integrated_idp_invariant(
    store: OAuthProviderStore,
    provider_category: str,
    is_idp: bool,
    *,
    exclude_id: int | None,
) -> None:
    """Enforce "at most one integrated/password IDP row, and it is an IDP".

    No-op for every other ``provider_category``. Mirrors the DB-level guard
    migration 185 adds (``uq_oauth_providers_one_integrated_idp`` /
    ``ck_oauth_providers_integrated_idp_is_idp``), so a violating request gets
    a clean ``400``/``409`` instead of a raw ``IntegrityError``.

    ``exclude_id`` is the row being updated in place (so updating the
    existing integrated-IDP row isn't mistaken for a duplicate of itself);
    pass ``None`` on create, where no row can be excluded.
    """
    if provider_category != INTEGRATED_IDP_CATEGORY:
        return
    if not is_idp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                'The integrated/password IDP provider '
                f'({INTEGRATED_IDP_CATEGORY!r}) must have is_idp=True'
            ),
        )
    existing = await store.get_first_by_category(INTEGRATED_IDP_CATEGORY)
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                'An integrated/password IDP provider already exists '
                f'(id={existing.id}); only one is allowed'
            ),
        )


def _to_response(provider: OAuthProvider) -> OAuthProviderResponse:
    return OAuthProviderResponse(
        id=provider.id,
        provider_category=provider.provider_category,
        display_name=provider.display_name,
        is_idp=provider.is_idp,
        client_id=provider.client_id,
        has_client_secret=bool(provider.client_secret),
        authorization_url=provider.authorization_url,
        token_url=provider.token_url,
        userinfo_url=provider.userinfo_url,
        scopes=provider.scopes,
        permitted_drift_seconds=provider.permitted_drift_seconds,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


@oauth_provider_admin_router.get('', response_model=list[OAuthProviderResponse])
async def list_oauth_providers(
    _: str = Depends(require_permission(Permission.MANAGE_OAUTH_PROVIDERS)),
) -> list[OAuthProviderResponse]:
    """List every configured OAuth/OIDC provider row."""
    providers = await OAuthProviderStore().list_all()
    return [_to_response(p) for p in providers]


@oauth_provider_admin_router.get('/{provider_id}', response_model=OAuthProviderResponse)
async def get_oauth_provider(
    provider_id: int,
    _: str = Depends(require_permission(Permission.MANAGE_OAUTH_PROVIDERS)),
) -> OAuthProviderResponse:
    """Get a single OAuth/OIDC provider row."""
    provider = await OAuthProviderStore().get_by_id(provider_id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='OAuth provider not found'
        )
    return _to_response(provider)


@oauth_provider_admin_router.post(
    '', response_model=OAuthProviderResponse, status_code=status.HTTP_201_CREATED
)
async def create_oauth_provider(
    body: CreateOAuthProviderRequest,
    caller_user_id: str = Depends(
        require_permission(Permission.MANAGE_OAUTH_PROVIDERS)
    ),
) -> OAuthProviderResponse:
    """Create a new OAuth/OIDC provider row."""
    store = OAuthProviderStore()
    await _validate_integrated_idp_invariant(
        store, body.provider_category, body.is_idp, exclude_id=None
    )
    provider = OAuthProvider(
        provider_category=body.provider_category,
        display_name=body.display_name,
        is_idp=body.is_idp,
        client_id=body.client_id,
        client_secret=wrap_token(body.client_secret) if body.client_secret else None,
        authorization_url=body.authorization_url,
        token_url=body.token_url,
        userinfo_url=body.userinfo_url,
        scopes=body.scopes,
        permitted_drift_seconds=body.permitted_drift_seconds,
    )
    created = await store.create(provider)
    logger.info(
        'oauth_providers_admin:create',
        extra={
            'caller_user_id': caller_user_id,
            'provider_id': created.id,
            'provider_category': created.provider_category,
        },
    )
    return _to_response(created)


@oauth_provider_admin_router.patch(
    '/{provider_id}', response_model=OAuthProviderResponse
)
async def update_oauth_provider(
    provider_id: int,
    body: UpdateOAuthProviderRequest,
    caller_user_id: str = Depends(
        require_permission(Permission.MANAGE_OAUTH_PROVIDERS)
    ),
) -> OAuthProviderResponse:
    """Patch an existing OAuth/OIDC provider row.

    Only fields explicitly set in the request body are modified.
    """
    store = OAuthProviderStore()
    existing = await store.get_by_id(provider_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='OAuth provider not found'
        )

    fields = body.model_dump(exclude_unset=True)
    if 'client_secret' in fields:
        secret = fields['client_secret']
        fields['client_secret'] = wrap_token(secret) if secret else None

    resulting_category = fields.get('provider_category', existing.provider_category)
    resulting_is_idp = fields.get('is_idp', existing.is_idp)
    await _validate_integrated_idp_invariant(
        store, resulting_category, resulting_is_idp, exclude_id=provider_id
    )

    updated = await store.update(provider_id, **fields)
    if updated is None:
        # Deleted between the fetch above and this update -- safety net, not
        # expected in practice since this endpoint is not re-entrant per row.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='OAuth provider not found'
        )
    logger.info(
        'oauth_providers_admin:update',
        extra={'caller_user_id': caller_user_id, 'provider_id': provider_id},
    )
    return _to_response(updated)


@oauth_provider_admin_router.delete(
    '/{provider_id}', status_code=status.HTTP_204_NO_CONTENT
)
async def delete_oauth_provider(
    provider_id: int,
    caller_user_id: str = Depends(
        require_permission(Permission.MANAGE_OAUTH_PROVIDERS)
    ),
) -> None:
    """Delete an OAuth/OIDC provider row.

    Refuses with ``409 Conflict`` if the caller themselves signed in (or
    linked a git identity) through this provider: ``oauth_providers`` rows
    cascade-delete their ``oauth_provider_users``/``oauth_tokens`` rows, so
    deleting the provider the caller is currently authenticated through
    would sever their own login/connection -- possibly locking them out of
    the instance entirely. Ask another super admin to delete it instead.
    """
    provider = await OAuthProviderStore().get_by_id(provider_id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='OAuth provider not found'
        )
    caller_link = await OAuthProviderUserStore().get_by_user(
        UUID(caller_user_id), provider_id
    )
    if caller_link is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Cannot delete the OAuth provider you are currently '
            'authenticated with',
        )
    await OAuthProviderStore().delete(provider_id)
    logger.info(
        'oauth_providers_admin:delete',
        extra={
            'caller_user_id': caller_user_id,
            'provider_id': provider_id,
            'provider_category': provider.provider_category,
        },
    )
