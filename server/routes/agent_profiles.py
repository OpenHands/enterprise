"""Organization Agent Profiles router (cloud / SaaS).

Serves the flat, member-facing ``/api/agent-profiles`` surface that
agent-canvas's backend-agnostic ``AgentProfilesClient`` calls (the same wire
contract as the local agent-server ``agent_profiles_router.py``). The org is
derived from the authenticated session (``EFFECTIVE_ORG_ID``) rather than the
path, because each member activates *their own* profile (#15044 §2); everything
else — the org-owned column, the locked org-row transaction, the
``VIEW``/``EDIT_ORG_SETTINGS`` permission model — mirrors the LLM-profile router
``org_profiles.py``.

Scopes (OHE-3456): every name-keyed endpoint takes a ``scope`` query parameter.
``org`` (the default, so pre-existing callers are unchanged) addresses the
org-owned collection on ``org.agent_profiles`` and keeps the admin-only
``EDIT_ORG_SETTINGS`` write permission. ``personal`` addresses the acting
member's own collection on ``org_member.agent_profiles`` — any member may manage
their personal profiles, so writes there require only membership
(``VIEW_ORG_SETTINGS``) and lock the member row instead of the org row. Reads
(``GET /{name}``, ``/{name}/materialize``) sent without a scope look in the org
collection first and fall back to the member's own, so clients that predate the
parameter can still read a personal profile by name. The list endpoint returns
both collections, each summary tagged with its scope, and ``activate`` accepts
an id from either.

Org-scope constraints (OHE-3088): an org profile launches for every member, so
a ``scope=org`` save must stand on org-scoped configuration alone — the
OpenHands agent kind only, no MCP server references (``null`` stays accepted
for the legacy seeded ``default``), and ``secret_refs`` drawn from the
organization's shared secrets. The name-collision guard is two-sided: a
personal save may not shadow an org name, and an org save or rename may not
take a name any member already uses personally.

Domain logic is **imported, never re-implemented** (the #3730 thin-boundary
contract): validation → ``validate_agent_profile``; id/revision stamping →
``save_profile_preserving_identity``; the dry-run resolve →
``resolve_agent_profile_dry_run``; the 422 redaction →
``safe_validation_error_detail``. Agent profiles are secret-free at rest
(software-agent-sdk#4017 dropped the last secret-bearing field, embedded
``skills``) — unlike ``org_profiles``, there is no api-key lift/mask on
activate, and GET/POST need no secret masking or restore-on-save at all.

Activation is **pointer-only**: it writes the per-member
``OrgMember.active_agent_profile_id`` and nothing else (the creation-time-only
contract, #15044 §3). Resolution into ``agent_settings`` happens at
conversation-start (``SaasSettingsStore.load``), not here.
"""

from __future__ import annotations

import contextlib
from typing import Annotated, Any, AsyncIterator
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    status,
)
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.settings.agent_profiles import (
    MAX_AGENT_PROFILES,
    AgentProfiles,
)
from openhands.app_server.user_auth import get_user_id
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.sdk.launch import LaunchRuntime, preview_launch
from openhands.sdk.profiles import (
    AgentProfile,
    AgentProfileDiagnostics,
    ProfileLimitExceeded,
    safe_validation_error_detail,
    save_profile_preserving_identity,
    validate_agent_profile,
)
from openhands.sdk.profiles.agent_profile_store import PROFILE_NAME_PATTERN
from server.auth.authorization import Permission, require_permission
from server.auth.org_context import EFFECTIVE_ORG_ID
from server.routes.org_models import OrgNotFoundError
from server.verified_models.default_profile import (
    get_openhands_default_model_name,
    materialize_default_llm_profile,
)
from storage.agent_profile_resolution import (
    AgentProfileScope,
    load_agent_profiles,
    load_llm_profiles,
    load_member_agent_profiles,
    member_mcp_config,
    org_launch_stores,
)
from storage.database import a_session_maker
from storage.org import Org
from storage.org_member import OrgMember
from storage.org_secrets_store import OrgSecretsStore
from storage.org_service import OrgService

router = APIRouter(prefix='/api/agent-profiles', tags=['Agent Profiles'])

ProfileName = Annotated[
    str, Path(min_length=1, max_length=64, pattern=PROFILE_NAME_PATTERN)
]
ProfileId = Annotated[str, Path(min_length=1, max_length=128)]
# Endpoints default this to the plain string ``'org'`` (not ``Query('org')``) so
# handlers called directly, as the unit tests do, see a str rather than a
# FieldInfo.
ScopeQuery = Annotated[
    AgentProfileScope,
    Query(
        description=(
            "Collection the profile name addresses: 'org' (the organization's "
            "shared, admin-managed profiles) or 'personal' (the acting "
            "member's own profiles)."
        )
    ),
]
# Reads accept no scope: the org collection is tried first, then the member's.
OptionalScopeQuery = Annotated[
    AgentProfileScope | None,
    Query(
        description=(
            "Collection the profile name addresses: 'org' or 'personal'. "
            "Omitted: the organization's profile of that name, else the acting "
            "member's own."
        )
    ),
]


# ── Request/Response Models (mirror the local agent_profiles_router contract) ──


class AgentProfileInfo(BaseModel):
    """Summary projection of a stored profile (no secret instantiation)."""

    id: str | None = None
    name: str
    agent_kind: str = 'openhands'
    revision: int | None = None
    llm_profile_ref: str | None = None
    mcp_server_refs: list[str] | None = None
    scope: AgentProfileScope = 'org'


class AgentProfileListResponse(BaseModel):
    profiles: list[AgentProfileInfo]
    active_agent_profile_id: str | None = None


class AgentProfileDetailResponse(BaseModel):
    name: str
    # The stored profile, secret-free at rest. Typed as the SDK discriminated
    # union (not ``dict``) so the OpenAPI schema matches the ``AgentProfile``
    # the ts-client consumes.
    profile: AgentProfile
    scope: AgentProfileScope = 'org'


class AgentProfileMutationResponse(BaseModel):
    name: str
    message: str


class ActivateAgentProfileResponse(BaseModel):
    id: str
    message: str
    # Always False: activation is pointer-only by contract. agent_settings is
    # resolved at conversation-start, not on activate.
    agent_settings_applied: bool = False


class RenameAgentProfileRequest(BaseModel):
    new_name: str = Field(
        ..., min_length=1, max_length=64, pattern=PROFILE_NAME_PATTERN
    )


# ── Helpers ────────────────────────────────────────────────────────────────


async def _get_org(org_id: UUID, user_id: str) -> Org:
    """Load the org, raising 404 if the user is not a member / it is missing."""
    try:
        return await OrgService.get_org_by_id(org_id=org_id, user_id=user_id)
    except OrgNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


async def _get_member(
    session: AsyncSession, org_id: UUID, user_id: str
) -> OrgMember | None:
    result = await session.execute(
        select(OrgMember).filter(
            OrgMember.org_id == org_id, OrgMember.user_id == UUID(user_id)
        )
    )
    return result.scalars().first()


async def _get_org_and_member(
    org_id: UUID, user_id: str
) -> tuple[Org, OrgMember | None]:
    """Fetch the org (raising 404 if missing) and the acting member's row.

    The member is read in its own short-lived session — matches the org read,
    which resolves through ``OrgService.get_org_by_id`` rather than this
    module's session directly.
    """
    org = await _get_org(org_id, user_id)
    async with a_session_maker() as session:
        member = await _get_member(session, org_id, user_id)
    return org, member


@contextlib.asynccontextmanager
async def _agent_profiles_transaction(
    org_id: UUID, user_id: str
) -> AsyncIterator[tuple[AsyncSession, Org, AgentProfiles]]:
    """Yield ``(session, org, agent_profiles)`` for a single locked mutation.

    Mirrors ``org_profiles._org_profiles_transaction``: ``SELECT ... FOR UPDATE``
    on the org row so concurrent profile mutations serialize at the DB level.
    The caller mutates ``agent_profiles`` in place (and may also write the acting
    member's ``active_agent_profile_id`` via the same session); on normal exit
    the helper serializes the collection back onto the org row and commits. This
    DB row lock *is* the store's ``lock()`` — hence the in-memory model's
    re-entrant no-op ``lock()``.

    The collection is written back ONLY when the caller actually changed it.
    """
    await _get_org(org_id, user_id)
    async with a_session_maker() as session:
        result = await session.execute(
            select(Org).filter(Org.id == org_id).with_for_update()
        )
        org = result.scalars().first()
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f'Organization {org_id} not found',
            )
        profiles = load_agent_profiles(org)
        # The EncryptedJSON column is the at-rest boundary. expose_secrets=True
        # is now a no-op for AgentProfile (secret-free since #4017), kept for
        # parity with the write-back pattern the LLM-profile router shares.
        before = profiles.model_dump(mode='json', context={'expose_secrets': True})
        yield session, org, profiles
        after = profiles.model_dump(mode='json', context={'expose_secrets': True})
        if after != before:
            org.agent_profiles = after
        await session.commit()


def require_scoped_write_permission():
    """Write permission for a name-keyed endpoint, chosen by its ``scope``.

    ``personal`` writes touch only the acting member's own row, so membership
    (``VIEW_ORG_SETTINGS``, held by every role) is enough; ``org`` writes keep
    the admin-only ``EDIT_ORG_SETTINGS``. Delegates to ``require_permission``
    so effective-org resolution and the super-role fallback stay in one place.
    """

    async def checker(
        request: Request,
        scope: ScopeQuery = 'org',
        user_id: str | None = Depends(get_user_id),
    ) -> str:
        permission = (
            Permission.VIEW_ORG_SETTINGS
            if scope == 'personal'
            else Permission.EDIT_ORG_SETTINGS
        )
        return await require_permission(permission)(request, None, user_id)

    return checker


@contextlib.asynccontextmanager
async def _member_agent_profiles_transaction(
    org_id: UUID, user_id: str
) -> AsyncIterator[tuple[AsyncSession, Org, OrgMember, AgentProfiles]]:
    """Yield ``(session, org, member, personal_profiles)`` for one locked mutation.

    The personal counterpart of ``_agent_profiles_transaction``: ``SELECT ...
    FOR UPDATE`` on the acting member's ``org_member`` row (never the org row,
    so members' edits contend neither with each other nor with admins), with the
    same write-back-only-when-changed contract. ``org`` is an unlocked read for
    callers that need the shared collection (the name-collision guard,
    ``activate``'s id lookup).
    """
    org = await _get_org(org_id, user_id)
    async with a_session_maker() as session:
        result = await session.execute(
            select(OrgMember)
            .filter(OrgMember.org_id == org_id, OrgMember.user_id == UUID(user_id))
            .with_for_update()
        )
        member = result.scalars().first()
        if member is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Organization membership not found',
            )
        profiles = load_member_agent_profiles(member)
        before = profiles.model_dump(mode='json', context={'expose_secrets': True})
        yield session, org, member, profiles
        after = profiles.model_dump(mode='json', context={'expose_secrets': True})
        if after != before:
            member.agent_profiles = after
        await session.commit()


@contextlib.asynccontextmanager
async def _scoped_transaction(
    scope: AgentProfileScope, org_id: UUID, user_id: str
) -> AsyncIterator[tuple[AsyncSession, Org, OrgMember | None, AgentProfiles]]:
    """One locked mutation on the collection ``scope`` addresses.

    ``personal`` locks the acting member's row and yields it; ``org`` locks the
    org row and yields ``None`` for the member.
    """
    if scope == 'personal':
        async with _member_agent_profiles_transaction(org_id, user_id) as (
            session,
            org,
            member,
            profiles,
        ):
            yield session, org, member, profiles
    else:
        async with _agent_profiles_transaction(org_id, user_id) as (
            session,
            org,
            profiles,
        ):
            yield session, org, None, profiles


def _scoped_collection(
    scope: AgentProfileScope, org: Org, member: OrgMember | None
) -> AgentProfiles:
    """The collection ``scope`` addresses, for reads (no lock, no write-back)."""
    if scope == 'personal':
        return load_member_agent_profiles(member) if member else AgentProfiles()
    return load_agent_profiles(org)


def _load_named_profile(
    scope: AgentProfileScope | None, org: Org, member: OrgMember | None, name: str
) -> tuple[AgentProfile, AgentProfileScope]:
    """Resolve ``name`` for a read: one collection when ``scope`` is given,
    else the org's profile of that name with the member's own as fallback."""
    candidates: tuple[AgentProfileScope, ...] = (
        (scope,) if scope is not None else ('org', 'personal')
    )
    for candidate in candidates:
        try:
            return _scoped_collection(candidate, org, member).load(name), candidate
        except FileNotFoundError:
            continue
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Agent profile '{name}' not found",
    )


def _name_exists(profiles: AgentProfiles, name: str) -> bool:
    try:
        profiles.load(name)
    except FileNotFoundError:
        return False
    return True


def _reject_org_owned_name(org: Org, personal: AgentProfiles, name: str) -> None:
    """409 when the org collection owns ``name`` and this member does not.

    Keeps a personal profile from shadowing an org profile of the same name;
    the personal half of the guard, ``_reject_member_owned_name`` is the org
    half.
    """
    if _name_exists(personal, name):
        return
    if _name_exists(load_agent_profiles(org), name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent profile '{name}' already exists in this organization",
        )


async def _reject_member_owned_name(
    session: AsyncSession, org_id: UUID, name: str
) -> None:
    """409 when any member's personal collection already uses ``name``.

    The org half of the collision guard: an org profile is listed to every
    member next to their own, and the automation service rejects a merged list
    with duplicate names, so an admin may not publish a name a member already
    uses. Only names are compared; nothing else about a personal profile leaves
    the scan.
    """
    members = (
        (
            await session.execute(
                select(OrgMember).where(
                    OrgMember.org_id == org_id,
                    OrgMember.agent_profiles.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if any(
        _name_exists(load_member_agent_profiles(member), name) for member in members
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Agent profile '{name}' already exists in a member's personal profiles"
            ),
        )


async def _enforce_org_profile_constraints(profile: AgentProfile, org_id: UUID) -> None:
    """422 unless ``profile`` runs on org-scoped configuration alone.

    An org profile launches for every member, so it may not lean on what the
    authoring admin has configured personally: ACP backends are per-member
    installs, MCP servers are per-member settings (``None`` stays accepted for
    the legacy seeded ``default``; an explicit list must be empty), and
    ``secret_refs`` may only name org-shared secrets, which is what the launch
    path resolves an org profile's explicit list against (OHE-3753).
    """
    if profile.agent_kind != 'openhands':
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='Organization agent profiles must use the OpenHands agent',
        )
    if profile.mcp_server_refs:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='Organization agent profiles cannot reference MCP servers',
        )
    if profile.secret_refs:
        store = await OrgSecretsStore.get_instance(org_id)
        shared = {secret.name for secret in await store.list_shared()}
        unknown = sorted(set(profile.secret_refs) - shared)
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    'Organization agent profiles can only reference '
                    'organization-shared secrets; not shared: ' + ', '.join(unknown)
                ),
            )


def _validate_profile_body(name: str, body: dict[str, Any]) -> AgentProfile:
    try:
        return validate_agent_profile({**body, 'name': name})
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=safe_validation_error_detail(e),
        ) from e
    except Exception:
        # SkillValidationError / schema errors are client errors, never a
        # 500; stay generic — these messages can embed the input.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='Invalid agent profile',
        )


def _save_with_limit(profiles: AgentProfiles, profile: AgentProfile) -> None:
    try:
        save_profile_preserving_identity(
            profiles, profile, max_profiles=MAX_AGENT_PROFILES
        )
    except ProfileLimitExceeded:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f'Agent profile limit reached ({MAX_AGENT_PROFILES}). '
                'Delete a profile before saving a new one.'
            ),
        )

    except FileExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent profile '{profile.name}' already exists",
        )


def _delete_by_name(profiles: AgentProfiles, name: str) -> str | None:
    """Delete ``name`` (idempotent) and return the removed id, if any."""
    # Capture the id (if present) before deleting so the pointer clear still
    # runs; ``profiles.delete`` is itself a no-op when absent.
    deleted_id: str | None = None
    with contextlib.suppress(FileNotFoundError):
        deleted_id = str(profiles.load(name).id)
    profiles.delete(name)
    return deleted_id


def _rename(profiles: AgentProfiles, name: str, new_name: str) -> None:
    try:
        profiles.rename(name, new_name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent profile '{name}' not found",
        )
    except FileExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent profile '{new_name}' already exists",
        )


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get('', response_model=AgentProfileListResponse)
async def list_agent_profiles(
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> AgentProfileListResponse:
    """List agent profiles and the effective active pointer for this member.

    Returns the member's personal profiles followed by the org's shared ones,
    each summary tagged with its ``scope``. No implicit writes: an org with no
    profiles yet just returns an empty list. Profile creation is always an
    explicit ``save_agent_profile`` call.
    """
    org, member = await _get_org_and_member(effective_org_id, user_id)
    profiles = load_agent_profiles(org)
    personal = _scoped_collection('personal', org, member)
    member_active = member.active_agent_profile_id if member is not None else None
    active_id = member_active or profiles.active

    return AgentProfileListResponse(
        profiles=[
            *(
                AgentProfileInfo(**s, scope='personal')
                for s in personal.list_summaries()
            ),
            *(AgentProfileInfo(**s, scope='org') for s in profiles.list_summaries()),
        ],
        active_agent_profile_id=active_id,
    )


@router.get('/{name}', response_model=AgentProfileDetailResponse)
async def get_agent_profile(
    name: ProfileName,
    scope: OptionalScopeQuery = None,
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> AgentProfileDetailResponse:
    """Get a stored profile — secret-free at rest, nothing to mask.

    Unlike the LLM-profile GET in ``org_profiles``, there is no
    ``X-Expose-Secrets`` handling here at all: the profile carries only
    references (``llm_profile_ref``, ``mcp_server_refs``) and a
    ``disabled_skills`` deny-list of names.
    """
    org, member = await _get_org_and_member(effective_org_id, user_id)
    profile, found_scope = _load_named_profile(scope, org, member, name)
    return AgentProfileDetailResponse(name=name, profile=profile, scope=found_scope)


@router.post(
    '/{name}',
    response_model=AgentProfileMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def save_agent_profile(
    name: ProfileName,
    body: dict[str, Any],
    scope: ScopeQuery = 'org',
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
    user_id: str = Depends(require_scoped_write_permission()),
) -> AgentProfileMutationResponse:
    """Create or update an agent profile under ``name`` (path name is authoritative).

    Server-managed id/revision: overwrite keeps the namesake's id and bumps
    revision; create mints a fresh id (``save_profile_preserving_identity``).
    Returns 422 on invalid payloads (secret-safe detail) and, for ``scope=org``,
    on a profile that breaks the org-scope constraints (ACP kind, MCP server
    references, secrets that are not org-shared). 409 when creating a new
    profile would exceed ``MAX_AGENT_PROFILES`` (per collection) or when the
    other side already owns ``name`` (the org collection for a personal save; a
    member's personal collection for an org save).
    """
    # Validation needs no row lock, and the secret-name lookup opens its own
    # session, so both run before the collection is locked.
    profile = _validate_profile_body(name, body)
    if scope == 'org':
        await _enforce_org_profile_constraints(profile, effective_org_id)
    async with _scoped_transaction(scope, effective_org_id, user_id) as (
        session,
        org,
        _member,
        profiles,
    ):
        if scope == 'personal':
            _reject_org_owned_name(org, profiles, name)
        elif not _name_exists(profiles, name):
            await _reject_member_owned_name(session, effective_org_id, name)
        _save_with_limit(profiles, profile)

    logger.info("Saved %s agent profile '%s' for org %s", scope, name, effective_org_id)
    return AgentProfileMutationResponse(
        name=name, message=f"Agent profile '{name}' saved"
    )


@router.delete('/{name}', response_model=AgentProfileMutationResponse)
async def delete_agent_profile(
    name: ProfileName,
    scope: ScopeQuery = 'org',
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
    user_id: str = Depends(require_scoped_write_permission()),
) -> AgentProfileMutationResponse:
    """Delete a profile (idempotent).

    ``scope=org`` clears every org member's pointer to it; ``scope=personal``
    removes it from the acting member's collection and clears only their own
    pointer. A missing name resolves 200, matching the ts-client
    ``AgentProfilesClient`` contract and the local agent-server
    ``delete_profile`` (canvas's delete mutation has no 404 branch).
    """
    async with _scoped_transaction(scope, effective_org_id, user_id) as (
        session,
        _org,
        member,
        profiles,
    ):
        deleted_id = _delete_by_name(profiles, name)
        if deleted_id is None:
            pass
        elif member is not None:
            # Personal: only the owner can point at it.
            if member.active_agent_profile_id == deleted_id:
                member.active_agent_profile_id = None
        else:
            # Clear the pointer for every member who had this profile active,
            # not just the acting member — activation is per-member, so any
            # other member could be pointing at the now-deleted id.
            await session.execute(
                update(OrgMember)
                .where(
                    OrgMember.org_id == effective_org_id,
                    OrgMember.active_agent_profile_id == deleted_id,
                )
                .values(active_agent_profile_id=None)
            )

    logger.info(
        "Deleted %s agent profile '%s' for org %s", scope, name, effective_org_id
    )
    return AgentProfileMutationResponse(
        name=name, message=f"Agent profile '{name}' deleted"
    )


@router.post('/{name}/rename', response_model=AgentProfileMutationResponse)
async def rename_agent_profile(
    name: ProfileName,
    request: RenameAgentProfileRequest = Body(...),
    scope: ScopeQuery = 'org',
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
    user_id: str = Depends(require_scoped_write_permission()),
) -> AgentProfileMutationResponse:
    """Rename a profile atomically.

    The stable id is preserved, so active pointers (keyed on id) survive
    untouched. The new name must not be owned by the other side either (409):
    the org collection for ``scope=personal``, any member's personal collection
    for ``scope=org``.
    """
    async with _scoped_transaction(scope, effective_org_id, user_id) as (
        session,
        org,
        _member,
        profiles,
    ):
        # 404 for a missing source before any 409 about the target.
        if not _name_exists(profiles, name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent profile '{name}' not found",
            )
        if scope == 'personal':
            _reject_org_owned_name(org, profiles, request.new_name)
        else:
            await _reject_member_owned_name(session, effective_org_id, request.new_name)
        _rename(profiles, name, request.new_name)

    return AgentProfileMutationResponse(
        name=request.new_name,
        message=f"Agent profile renamed from '{name}' to '{request.new_name}'",
    )


@router.post('/{profile_id}/activate', response_model=ActivateAgentProfileResponse)
async def activate_agent_profile(
    profile_id: ProfileId,
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> ActivateAgentProfileResponse:
    """Activate a profile for the calling member — pointer only.

    Writes the per-member ``OrgMember.active_agent_profile_id`` and nothing else
    (no ``agent_settings`` materialization; neither collection is touched —
    the transaction helper skips the write-back when nothing mutated; #15044
    §3). The id may belong to the member's personal collection or the org's.
    Member-facing, so it requires only ``VIEW_ORG_SETTINGS`` (each member picks
    their own active profile). 404 if no stored profile has that id.
    """
    not_found = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Agent profile with id '{profile_id}' not found",
    )
    org, member = await _get_org_and_member(effective_org_id, user_id)
    # Lock the row that owns the id, so a concurrent delete of that profile
    # (which clears pointers under the same lock) serializes with this write.
    if profile_id in _scoped_collection('personal', org, member).profiles:
        async with _member_agent_profiles_transaction(effective_org_id, user_id) as (
            _session,
            _org,
            locked_member,
            personal,
        ):
            if profile_id not in personal.profiles:
                raise not_found
            locked_member.active_agent_profile_id = profile_id
    else:
        async with _agent_profiles_transaction(effective_org_id, user_id) as (
            session,
            _org,
            profiles,
        ):
            if profile_id not in profiles.profiles:
                raise not_found
            member_row = await _get_member(session, effective_org_id, user_id)
            if member_row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail='Organization membership not found',
                )
            member_row.active_agent_profile_id = profile_id

    logger.info(
        "Activated agent profile id '%s' for member in org %s",
        profile_id,
        effective_org_id,
    )
    return ActivateAgentProfileResponse(
        id=profile_id, message=f"Agent profile '{profile_id}' activated"
    )


@router.post('/{name}/materialize', response_model=AgentProfileDiagnostics)
async def materialize_agent_profile(
    name: ProfileName,
    scope: OptionalScopeQuery = None,
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> AgentProfileDiagnostics:
    """Dry-run resolve a profile's LLM/MCP references into a diagnostics report.

    Dangling refs are reported in the body (``valid=False``), not raised; the
    only error status is 404 (unknown profile name). ``resolved_settings`` is
    redacted. Delegates entirely to ``resolve_agent_profile_dry_run``.
    """
    org, member = await _get_org_and_member(effective_org_id, user_id)
    profile, _found_scope = _load_named_profile(scope, org, member, name)

    mcp_config = member_mcp_config(member) if member is not None else {}
    async with a_session_maker() as session:
        model_name = await get_openhands_default_model_name(session)
    stores = org_launch_stores(
        org,
        mcp_config,
        llm_profiles=materialize_default_llm_profile(
            load_llm_profiles(org), model_name
        ),
        fallback_api_key=member.llm_api_key if member is not None else None,
    )

    try:
        return preview_launch(
            profile,
            stores,
            LaunchRuntime(
                browser_available=True, acp_skill_sourcing='openhands_managed'
            ),
            check_usable=False,
        )
    except Exception as exc:
        # The dry-run is contractually total, but SDK contract drift (e.g. a
        # new required kwarg raising TypeError) must surface as an invalid
        # diagnostics report, not a 500.
        logger.warning(
            "Agent profile dry-run resolve failed for '%s' in org %s: %s",
            name,
            effective_org_id,
            type(exc).__name__,
        )
        return AgentProfileDiagnostics(
            agent_kind=profile.agent_kind,
            valid=False,
            errors=[
                'The profile could not be resolved; repair its configuration or retry.'
            ],
        )
