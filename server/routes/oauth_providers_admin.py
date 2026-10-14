"""Admin REST API for the ``auth_providers`` table.

Exposes CRUD for login/integration provider configuration rows -- login IDPs
(``is_idp=True``) and git providers (``is_idp=False``), see
``storage.auth_provider`` for the row shape and ``storage.auth_provider_details``
for the ``details`` discriminated union. Every endpoint requires the
``Permission.MANAGE_OAUTH_PROVIDERS`` permission, which -- like
``MANAGE_SUPER_ADMINS`` and ``MANAGE_FEATURE_FLAGS`` -- is granted only to
the ``superadmin`` super role (see ``server.auth.authorization``): no
org-scoped role can reach these routes, so an org owner cannot reconfigure
instance-wide login/OAuth endpoints.

Secret fields inside ``details`` (``OAuthProviderDetails.client_secret``,
``SamlProviderDetails.sp_private_key``) are write-only: responses never echo
the real value -- or its encrypted-at-rest form -- back. This falls out of
the ``details`` models' own ``field_serializer``s (see
``storage.auth_provider_details``) rather than any masking logic here --
FastAPI serializes the response with no ``for_storage`` context, so a
configured secret comes back as pydantic's masked ``SecretStr`` placeholder
and an unset one comes back ``null``; both are distinguishable, so there is
no separate ``has_client_secret`` flag to maintain. To clear a configured
secret, send the field as ``""`` on update; omit ``details`` entirely to
leave the whole blob untouched.

**Integrated/password IDP invariant.** At most one row may have
``details.kind == 'PasswordProviderDetails'``, and that row must always have
``is_idp=True`` -- see ``_validate_password_idp_invariant`` and migration
186 (which carries forward the matching DB-level unique index and check
constraint migration 185 added to the old flat schema; this validation
exists only to turn a would-be ``IntegrityError`` into a clean
``400``/``409``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, SecretStr

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import Permission, require_permission
from server.services.saml_metadata import (
    SamlIdpMetadata,
    SamlMetadataFetchError,
    fetch_saml_idp_metadata,
)
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import (
    AuthProviderDetails,
    PasswordProviderDetails,
    SamlProviderDetails,
    generate_sp_keypair,
)
from storage.auth_provider_store import AuthProviderStore
from storage.auth_provider_user_store import AuthProviderUserStore

oauth_provider_admin_router = APIRouter(
    prefix='/api/admin/oauth-providers', tags=['Admin']
)


class AuthProviderResponse(BaseModel):
    """An ``auth_providers`` row, with any secret in ``details`` masked."""

    id: int
    kind: str
    title: str
    is_idp: bool
    details: AuthProviderDetails
    created_at: datetime
    updated_at: datetime


class CreateAuthProviderRequest(BaseModel):
    """Fields required to create a new ``auth_providers`` row.

    ``kind`` is not a separate field here -- it is derived from
    ``details.kind`` (``AuthProvider._sync_kind``), itself always
    ``type(details).__name__`` (``DiscriminatedUnionMixin``). Send
    ``{"kind": "OAuthProviderDetails", ...}`` /
    ``{"kind": "PasswordProviderDetails"}`` / ``{"kind":
    "SamlProviderDetails", ...}`` for ``details``.
    """

    title: str = Field(..., min_length=1)
    is_idp: bool = Field(
        default=False, description='True for login IDPs, False for git providers.'
    )
    details: AuthProviderDetails


class UpdateAuthProviderRequest(BaseModel):
    """Partial update: only fields explicitly set in the request are changed.

    ``details`` is replaced as a whole blob when present (it may change
    ``kind`` -- e.g. reconfiguring a row from OAuth2 to SAML -- so there is
    no field-by-field merge). Omit it entirely to leave the existing
    configuration, including its secrets, untouched.
    """

    title: str | None = Field(default=None, min_length=1)
    is_idp: bool | None = None
    details: AuthProviderDetails | None = None


async def _validate_password_idp_invariant(
    store: AuthProviderStore,
    details: AuthProviderDetails,
    is_idp: bool,
    *,
    exclude_id: int | None,
) -> None:
    """Enforce "at most one integrated/password IDP row, and it is an IDP".

    No-op for every ``details`` kind except ``PasswordProviderDetails``.
    Mirrors the DB-level guard migration 186 carries forward onto
    ``auth_providers`` (``uq_auth_providers_one_password_provider`` /
    ``ck_auth_providers_password_provider_is_idp``), so a violating request
    gets a clean ``400``/``409`` instead of a raw ``IntegrityError``.

    ``exclude_id`` is the row being updated in place (so updating the
    existing password-IDP row isn't mistaken for a duplicate of itself);
    pass ``None`` on create, where no row can be excluded.
    """
    if not isinstance(details, PasswordProviderDetails):
        return
    if not is_idp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='The integrated/password IDP provider must have is_idp=True',
        )
    existing = await store.get_first_by_kind(PasswordProviderDetails.__name__)
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                'An integrated/password IDP provider already exists '
                f'(id={existing.id}); only one is allowed'
            ),
        )


def _ensure_sp_keypair(
    details: AuthProviderDetails | None, existing_details: AuthProviderDetails | None
) -> None:
    """Auto-generate the SAML SP signing key+cert pair the first time it's needed.

    Admins never submit ``sp_private_key``/``sp_x509_cert`` (there is no form
    field for either -- the form only ever displays ``sp_x509_cert`` back),
    so both are always unset on the incoming ``details``. Mutates ``details``
    in place:

    * If the row already has a key pair (``existing_details`` is itself
      ``SamlProviderDetails`` with ``sp_private_key`` set), carry both fields
      forward together -- otherwise every unrelated edit (e.g. changing the
      SSO URL) would silently mint a new key and invalidate whatever the
      admin configured at the IdP. The key and its cert are generated as one
      unit and must never be replaced independently (a cert for a different
      key is not just stale, it's meaningless).
    * Otherwise (brand new row, or a row just switched to the SAML kind)
      generate a fresh pair.

    No-op for every ``details`` kind except ``SamlProviderDetails``, and for
    a ``SamlProviderDetails`` that somehow already carries a key (defensive;
    not reachable through the current form, which has no field for it).
    """
    if not isinstance(details, SamlProviderDetails) or details.sp_private_key:
        return
    if (
        isinstance(existing_details, SamlProviderDetails)
        and existing_details.sp_private_key
    ):
        details.sp_private_key = existing_details.sp_private_key
        details.sp_x509_cert = existing_details.sp_x509_cert
        return
    private_key_pem, cert_pem = generate_sp_keypair()
    details.sp_private_key = SecretStr(private_key_pem)
    details.sp_x509_cert = cert_pem


@oauth_provider_admin_router.get('', response_model=list[AuthProviderResponse])
async def list_oauth_providers(
    _: str = Depends(require_permission(Permission.MANAGE_OAUTH_PROVIDERS)),
) -> list[AuthProvider]:
    """List every configured login/integration provider row."""
    return await AuthProviderStore().list_all()


class SamlMetadataPreviewRequest(BaseModel):
    metadata_url: str = Field(..., min_length=1)


@oauth_provider_admin_router.post(
    '/saml-metadata-preview', response_model=SamlIdpMetadata
)
async def preview_saml_metadata(
    body: SamlMetadataPreviewRequest,
    _: str = Depends(require_permission(Permission.MANAGE_OAUTH_PROVIDERS)),
) -> SamlIdpMetadata:
    """Fetch and parse a SAML IdP's metadata document server-side.

    Backs the SAML form's "Metadata URL" field: instead of an admin copying
    the entity ID, SSO/SLO URLs, and signing certificate out of the IdP's
    metadata XML by hand, the frontend calls this with the metadata URL and
    prefills the rest of the form from the response. Does not persist
    anything -- the admin still saves the provider via the regular
    create/update endpoints.

    Registered ahead of ``GET /{provider_id}`` so this static path can't be
    shadowed by the dynamic one (Starlette falls back to it on a method
    mismatch regardless of order, but a literal path should still win first).
    """
    try:
        return await fetch_saml_idp_metadata(body.metadata_url)
    except SamlMetadataFetchError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@oauth_provider_admin_router.get('/{provider_id}', response_model=AuthProviderResponse)
async def get_oauth_provider(
    provider_id: int,
    _: str = Depends(require_permission(Permission.MANAGE_OAUTH_PROVIDERS)),
) -> AuthProvider:
    """Get a single login/integration provider row."""
    provider = await AuthProviderStore().get_by_id(provider_id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='OAuth provider not found'
        )
    return provider


@oauth_provider_admin_router.post(
    '', response_model=AuthProviderResponse, status_code=status.HTTP_201_CREATED
)
async def create_oauth_provider(
    body: CreateAuthProviderRequest,
    caller_user_id: str = Depends(
        require_permission(Permission.MANAGE_OAUTH_PROVIDERS)
    ),
) -> AuthProvider:
    """Create a new login/integration provider row."""
    store = AuthProviderStore()
    await _validate_password_idp_invariant(
        store, body.details, body.is_idp, exclude_id=None
    )
    _ensure_sp_keypair(body.details, existing_details=None)
    provider = AuthProvider(
        title=body.title,
        is_idp=body.is_idp,
        details=body.details,
    )
    created = await store.create(provider)
    logger.info(
        'oauth_providers_admin:create',
        extra={
            'caller_user_id': caller_user_id,
            'provider_id': created.id,
            'kind': created.kind,
        },
    )
    return created


@oauth_provider_admin_router.patch(
    '/{provider_id}', response_model=AuthProviderResponse
)
async def update_oauth_provider(
    provider_id: int,
    body: UpdateAuthProviderRequest,
    caller_user_id: str = Depends(
        require_permission(Permission.MANAGE_OAUTH_PROVIDERS)
    ),
) -> AuthProvider:
    """Patch an existing login/integration provider row.

    Only fields explicitly set in the request body are modified.
    """
    store = AuthProviderStore()
    existing = await store.get_by_id(provider_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='OAuth provider not found'
        )

    fields = body.model_dump(exclude_unset=True, exclude={'details'})
    if 'details' in body.model_fields_set:
        fields['details'] = body.details
        _ensure_sp_keypair(body.details, existing_details=existing.details)

    resulting_details = fields.get('details', existing.details)
    resulting_is_idp = fields.get('is_idp', existing.is_idp)
    await _validate_password_idp_invariant(
        store, resulting_details, resulting_is_idp, exclude_id=provider_id
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
    return updated


@oauth_provider_admin_router.delete(
    '/{provider_id}', status_code=status.HTTP_204_NO_CONTENT
)
async def delete_oauth_provider(
    provider_id: int,
    caller_user_id: str = Depends(
        require_permission(Permission.MANAGE_OAUTH_PROVIDERS)
    ),
) -> None:
    """Delete a login/integration provider row.

    Refuses with ``409 Conflict`` if the caller themselves signed in (or
    linked a git identity) through this provider: ``auth_providers`` rows
    cascade-delete their ``auth_provider_users``/``oauth_tokens`` rows, so
    deleting the provider the caller is currently authenticated through
    would sever their own login/connection -- possibly locking them out of
    the instance entirely. Ask another super admin to delete it instead.
    """
    provider = await AuthProviderStore().get_by_id(provider_id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='OAuth provider not found'
        )
    caller_link = await AuthProviderUserStore().get_by_user(
        UUID(caller_user_id), provider_id
    )
    if caller_link is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Cannot delete the OAuth provider you are currently '
            'authenticated with',
        )
    await AuthProviderStore().delete(provider_id)
    logger.info(
        'oauth_providers_admin:delete',
        extra={
            'caller_user_id': caller_user_id,
            'provider_id': provider_id,
            'kind': provider.kind,
        },
    )
