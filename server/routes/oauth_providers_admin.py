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
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, SecretStr

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import Permission, require_permission
from server.services.automation_event_service import invalidate_user_id_cache
from server.services.saml_metadata import (
    SamlIdpMetadata,
    SamlMetadataFetchError,
    fetch_saml_idp_metadata,
)
from storage.auth_provider import DEFAULT_AUTH_PROVIDER_COLOR, AuthProvider
from storage.auth_provider_details import (
    AuthProviderDetails,
    OAuthProviderDetails,
    PasswordProviderDetails,
    SamlProviderDetails,
    generate_sp_keypair,
)
from storage.auth_provider_store import AuthProviderStore
from storage.auth_provider_user import AuthProviderUser
from storage.auth_provider_user_store import AuthProviderUserStore
from storage.database import a_session_maker
from storage.oauth_token_store import OAuthTokenStore
from storage.user import User

oauth_provider_admin_router = APIRouter(
    prefix='/api/admin/oauth-providers', tags=['Admin']
)


class AuthProviderResponse(BaseModel):
    """An ``auth_providers`` row, with any secret in ``details`` masked."""

    id: int
    kind: str
    title: str
    is_idp: bool
    icon: str | None
    color: str
    enabled: bool
    allow_registration: bool
    allow_match_by_email: bool
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
    icon: str | None = Field(
        default=None, description="Raw SVG markup for this provider's login button."
    )
    color: str = Field(
        default=DEFAULT_AUTH_PROVIDER_COLOR,
        description="CSS color string for this provider's login button.",
    )
    enabled: bool = Field(
        default=False,
        description=(
            'Whether this provider may be used for a new login or link. New '
            'rows start disabled so a half-configured provider (e.g. a preset '
            'with placeholder URLs) never goes live before an admin finishes '
            'it.'
        ),
    )
    allow_registration: bool = Field(
        default=True,
        description=(
            'Whether a brand-new user may be created the first time someone '
            'authenticates through this provider, or only an already-linked '
            'account may sign in through it.'
        ),
    )
    allow_match_by_email: bool = Field(
        default=True,
        description=(
            'Initial value of User.allow_match_by_email for a brand-new user '
            'created by a login through this provider -- see '
            'server.auth.provider_identity.resolve_or_create_user.'
        ),
    )
    details: AuthProviderDetails


class AuthProviderUserResponse(BaseModel):
    """One ``auth_provider_users`` row, joined with the linked ``User.email``.

    Backs the admin "User Links" table (``GET /{provider_id}/users``).
    """

    id: int
    user_id: str
    email: str | None
    external_subject_id: str
    external_email: str | None


class AuthProviderUserPage(BaseModel):
    """Paginated response for ``GET /{provider_id}/users``."""

    items: list[AuthProviderUserResponse]
    total: int
    current_page: int = 1
    per_page: int = 20


class UpdateAuthProviderUserRequest(BaseModel):
    """Re-point a link row to a different, already-existing user."""

    user_id: UUID


class UpdateAuthProviderRequest(BaseModel):
    """Partial update: only fields explicitly set in the request are changed.

    ``details`` is replaced as a whole blob when present (it may change
    ``kind`` -- e.g. reconfiguring a row from OAuth2 to SAML). The one
    exception: when the row stays OAuth2, any ``details`` field the request
    leaves out keeps its existing value. Omit ``details`` entirely to leave
    the existing configuration, including its secrets, untouched.
    """

    title: str | None = Field(default=None, min_length=1)
    is_idp: bool | None = None
    icon: str | None = None
    color: str | None = None
    enabled: bool | None = None
    allow_registration: bool | None = None
    allow_match_by_email: bool | None = None
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


def _carry_forward_unset_oauth_fields(
    details: AuthProviderDetails | None, existing_details: AuthProviderDetails | None
) -> None:
    """Keep the existing value of every OAuth field the request left out.

    Mutates ``details`` in place so a client that doesn't know about a field
    (e.g. ``legacy_keycloak_type``) can't silently reset it by editing the
    row. Fields sent explicitly -- including as ``null`` -- are respected.
    No-op unless both old and new ``details`` are ``OAuthProviderDetails``.
    """
    if not (
        isinstance(details, OAuthProviderDetails)
        and isinstance(existing_details, OAuthProviderDetails)
    ):
        return
    for name in type(details).model_fields:
        if name not in details.model_fields_set:
            setattr(details, name, getattr(existing_details, name))


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
        icon=body.icon,
        color=body.color,
        enabled=body.enabled,
        allow_registration=body.allow_registration,
        allow_match_by_email=body.allow_match_by_email,
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
        _carry_forward_unset_oauth_fields(body.details, existing.details)
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


def _to_auth_provider_user_response(
    link: AuthProviderUser, email: str | None
) -> AuthProviderUserResponse:
    return AuthProviderUserResponse(
        id=link.id,
        user_id=str(link.user_id),
        email=email,
        external_subject_id=link.external_subject_id,
        external_email=link.external_email,
    )


@oauth_provider_admin_router.get(
    '/{provider_id}/users', response_model=AuthProviderUserPage
)
async def list_auth_provider_users(
    provider_id: int,
    page_id: Annotated[
        str | None,
        Query(title='Optional offset (as a string) from a previous page'),
    ] = None,
    limit: Annotated[
        int, Query(title='The max number of results in the page', gt=0, le=100)
    ] = 20,
    q: Annotated[
        str | None,
        Query(
            title=(
                'Filter by partial, case-insensitive match on the linked '
                "user's email, external email, or external subject id"
            ),
            min_length=1,
            max_length=255,
        ),
    ] = None,
    _: str = Depends(require_permission(Permission.MANAGE_OAUTH_PROVIDERS)),
) -> AuthProviderUserPage:
    """List the "User Links" (``auth_provider_users``) for one provider.

    Powers ``/super-admin/auth-providers/{provider_id}/users``: the table a
    super admin uses to re-point an identity bound to the wrong account --
    see ``update_auth_provider_user`` for the re-pointing itself.
    """
    if await AuthProviderStore().get_by_id(provider_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='OAuth provider not found'
        )

    offset = 0
    if page_id is not None:
        try:
            offset = int(page_id)
            if offset < 0:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Invalid page_id format',
            )

    store = AuthProviderUserStore()
    rows = await store.list_by_provider(
        provider_id, offset=offset, limit=limit, filter_text=q
    )
    total = await store.count_by_provider(provider_id, filter_text=q)
    return AuthProviderUserPage(
        items=[
            _to_auth_provider_user_response(link, user.email) for link, user in rows
        ],
        total=total,
        current_page=(offset // limit) + 1,
        per_page=limit,
    )


@oauth_provider_admin_router.patch(
    '/{provider_id}/users/{link_id}', response_model=AuthProviderUserResponse
)
async def update_auth_provider_user(
    provider_id: int,
    link_id: int,
    body: UpdateAuthProviderUserRequest,
    caller_user_id: str = Depends(
        require_permission(Permission.MANAGE_OAUTH_PROVIDERS)
    ),
) -> AuthProviderUserResponse:
    """Re-point a "User Link" to a different, already-existing user.

    Changes only ``user_id`` on the ``auth_provider_users`` row -- not
    ``external_email`` -- so the identity (``external_subject_id``) keeps
    describing the same external account while it resolves to a different
    internal ``User``. Takes effect on that identity's next login;
    ``resolve_or_create_user`` (``server.auth.provider_identity``) looks the
    link up by ``(provider, sub)`` and never touches it otherwise. Existing
    sessions for either user are not revoked.

    Also deletes the *previous* user's ``oauth_tokens`` row for this
    provider -- same cleanup ``oauth_v2_unlink`` does -- so they don't keep
    a token for an identity they no longer own.
    """
    provider = await AuthProviderStore().get_by_id(provider_id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='OAuth provider not found'
        )

    link_store = AuthProviderUserStore()
    link = await link_store.get_by_id(link_id)
    if link is None or link.oauth_provider_id != provider_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='User link not found'
        )

    async with a_session_maker() as session:
        target_user = await session.get(User, body.user_id)
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Target user not found'
        )

    # ``AuthProviderUserStore.get_by_user()`` uses ``.one_or_none()`` and
    # would raise ``MultipleResultsFound`` the next time it's called for
    # this user+provider (e.g. from the unlink route or the provider-delete
    # guard) if a second link were allowed to point at them -- the DB itself
    # permits it (the only unique constraint is on
    # ``(oauth_provider_id, external_subject_id)``).
    existing_link_for_target = await link_store.get_by_user(body.user_id, provider_id)
    if existing_link_for_target is not None and existing_link_for_target.id != link_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='The target user already has a link on this provider',
        )

    old_user_id = link.user_id
    updated = await link_store.update_user(link_id, body.user_id)
    if updated is None:
        # Deleted between the fetch above and this update -- safety net.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='User link not found'
        )

    token_store = OAuthTokenStore(user_id=old_user_id, oauth_provider_id=provider_id)
    await token_store.delete_tokens()
    if isinstance(provider.details, OAuthProviderDetails):
        await invalidate_user_id_cache(
            provider.details.provider_type, updated.external_subject_id
        )

    logger.info(
        'oauth_providers_admin:update_user_link',
        extra={
            'caller_user_id': caller_user_id,
            'provider_id': provider_id,
            'link_id': link_id,
            'old_user_id': str(old_user_id),
            'new_user_id': str(body.user_id),
        },
    )
    return _to_auth_provider_user_response(updated, target_user.email)
