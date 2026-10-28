"""Organization Model Router (meta-profile) routes.

An organization keeps its Model Routers (SDK meta-profiles) in
``org.meta_profiles`` and activates at most one of them. The active router is
applied to every conversation launch in the org by ``SaasSettingsStore.load``,
which copies it and the LLM profiles it routes to inline into the launch
settings (OHE-3508).

Permission model (mirrors ``org_profiles``):
- list / get: VIEW_ORG_SETTINGS -- every member can see the routers.
- save / delete / activate / deactivate: EDIT_ORG_SETTINGS -- owners/admins.

Every route answers 404 while the ``enable_model_router`` feature flag is off.
"""

import contextlib
from typing import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.settings.meta_profiles import (
    OrgMetaProfiles,
    referenced_llm_profile_names,
)
from openhands.sdk.llm.llm_profile_store import PROFILE_NAME_PATTERN
from openhands.sdk.llm.meta_profile_store import MetaProfile
from server.constants import is_model_router_enabled
from server.routes.org_models import OrgNotFoundError
from server.routes.org_profiles import (
    _load_profiles_with_live_default,
    _resolve_provider_connection,
)
from storage.agent_profile_resolution import load_meta_profiles
from storage.database import a_session_maker
from storage.org import Org
from storage.org_service import OrgService

from ..auth.authorization import Permission, require_permission

MAX_META_PROFILES = 50


def require_model_router_enabled() -> None:
    """Hide the whole feature while the ``enable_model_router`` flag is off."""
    if not is_model_router_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Model Router is not enabled on this deployment.',
        )


router = APIRouter(
    tags=['Organization Model Routers'],
    dependencies=[Depends(require_model_router_enabled)],
)


# ── Request/Response Models ────────────────────────────────────────────────
# Same shapes as the agent-server ``/api/meta-profiles`` routes, which the
# Agent Canvas cloud client already expects at this path.


class MetaProfileInfo(BaseModel):
    name: str
    classifier_model: str | None
    num_classes: int


class MetaProfileListResponse(BaseModel):
    meta_profiles: list[MetaProfileInfo]
    active_meta_profile: str | None = None
    run_at_conversation_start: bool = False


class MetaProfileDetailResponse(BaseModel):
    name: str
    config: MetaProfile


class MetaProfileMutationResponse(BaseModel):
    name: str
    message: str


class ActivateMetaProfileResponse(BaseModel):
    name: str
    message: str
    applied: bool = True


class MetaProfileSettingsUpdateRequest(BaseModel):
    """Org-wide router behaviour that is not part of any single router."""

    run_at_conversation_start: bool


# ── Helper Functions ────────────────────────────────────────────────────────


async def _get_org(org_id: UUID, user_id: str) -> Org:
    """Get org, raising 404 if not found."""
    try:
        return await OrgService.get_org_by_id(org_id=org_id, user_id=user_id)
    except OrgNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@contextlib.asynccontextmanager
async def _org_meta_profiles_transaction(
    org_id: UUID, user_id: str
) -> AsyncIterator[tuple[AsyncSession, Org, OrgMetaProfiles]]:
    """Yield ``(session, org, routers)`` for a single locked mutation.

    Wraps read -> mutate -> write in one session with ``SELECT ... FOR UPDATE``
    so concurrent router mutations serialize at the database level (same
    contract as ``org_profiles._org_profiles_transaction``). The caller mutates
    ``routers`` in place; on normal exit the helper serializes it back onto the
    org row and commits. Exceptions skip the commit.
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
        routers = load_meta_profiles(org)
        yield session, org, routers
        org.meta_profiles = routers.model_dump(mode='json')
        await session.commit()


async def _validate_llm_profile_references(
    org: Org, config: MetaProfile, session: AsyncSession
) -> None:
    """422 unless every LLM profile the router names exists and is resolvable.

    Checks the classifier and the structured class targets against the org's
    LLM profiles (including the live ``Default``). A dangling provider
    connection on one of them is rejected here too, before the router can be
    activated into an unusable configuration.
    """
    profiles = await _load_profiles_with_live_default(org, session)
    for llm_name in sorted(referenced_llm_profile_names(config)):
        llm = profiles.get(llm_name)
        if llm is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Model Router references missing LLM profile '{llm_name}'.",
            )
        _resolve_provider_connection(org, llm)


def _list_response(routers: OrgMetaProfiles) -> MetaProfileListResponse:
    return MetaProfileListResponse(
        meta_profiles=[MetaProfileInfo(**item) for item in routers.summaries()],
        active_meta_profile=routers.active,
        run_at_conversation_start=routers.run_at_conversation_start,
    )


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get('/{org_id}/meta-profiles', response_model=MetaProfileListResponse)
async def list_meta_profiles(
    org_id: UUID,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> MetaProfileListResponse:
    """List the organization's Model Routers and which one is active."""
    org = await _get_org(org_id, user_id)
    return _list_response(load_meta_profiles(org))


@router.get('/{org_id}/meta-profiles/{name}', response_model=MetaProfileDetailResponse)
async def get_meta_profile(
    org_id: UUID,
    name: str = Path(..., pattern=PROFILE_NAME_PATTERN),
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> MetaProfileDetailResponse:
    """Get one Model Router's configuration."""
    org = await _get_org(org_id, user_id)
    config = load_meta_profiles(org).get(name)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model Router '{name}' not found",
        )
    return MetaProfileDetailResponse(name=name, config=config)


@router.post(
    '/{org_id}/meta-profiles/{name}',
    response_model=MetaProfileMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def save_meta_profile(
    org_id: UUID,
    body: MetaProfile,
    name: str = Path(..., pattern=PROFILE_NAME_PATTERN),
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
) -> MetaProfileMutationResponse:
    """Create or overwrite a Model Router.

    The first router an organization creates becomes the active one and turns
    "Run on first message" on, matching the Agent Canvas behaviour, so adding a
    router is enough to start routing.
    """
    if not body.classes and not body.prompt_template:
        # Mirrors the agent-server save route: a router with no destinations
        # can never route, so refuse to store it.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='A Model Router needs at least one class or a prompt template.',
        )
    async with _org_meta_profiles_transaction(org_id, user_id) as (
        session,
        org,
        routers,
    ):
        if name not in routers.profiles and len(routers.profiles) >= MAX_META_PROFILES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'Model Router limit reached ({MAX_META_PROFILES}).',
            )
        await _validate_llm_profile_references(org, body, session)
        first_router = not routers.profiles
        routers.profiles[name] = body
        if first_router:
            routers.active = name
            routers.run_at_conversation_start = True
    return MetaProfileMutationResponse(
        name=name, message=f"Model Router '{name}' saved"
    )


@router.delete(
    '/{org_id}/meta-profiles/{name}', response_model=MetaProfileMutationResponse
)
async def delete_meta_profile(
    org_id: UUID,
    name: str = Path(..., pattern=PROFILE_NAME_PATTERN),
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
) -> MetaProfileMutationResponse:
    """Delete a Model Router (idempotent). Deleting the active one deactivates it."""
    async with _org_meta_profiles_transaction(org_id, user_id) as (
        _session,
        _org,
        routers,
    ):
        routers.profiles.pop(name, None)
        if routers.active == name:
            routers.active = None
    return MetaProfileMutationResponse(
        name=name, message=f"Model Router '{name}' deleted"
    )


@router.post(
    '/{org_id}/meta-profiles/{name}/activate',
    response_model=ActivateMetaProfileResponse,
)
async def activate_meta_profile(
    org_id: UUID,
    name: str = Path(..., pattern=PROFILE_NAME_PATTERN),
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
) -> ActivateMetaProfileResponse:
    """Make ``name`` the organization's active Model Router.

    Org-wide: every conversation launched in the organization by an OpenHands
    agent carries this router from its next start.
    """
    async with _org_meta_profiles_transaction(org_id, user_id) as (
        _session,
        _org,
        routers,
    ):
        if name not in routers.profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model Router '{name}' not found",
            )
        routers.active = name
    return ActivateMetaProfileResponse(
        name=name, message=f"Model Router '{name}' activated"
    )


@router.post(
    '/{org_id}/meta-profiles/{name}/deactivate',
    response_model=MetaProfileMutationResponse,
)
async def deactivate_meta_profile(
    org_id: UUID,
    name: str = Path(..., pattern=PROFILE_NAME_PATTERN),
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
) -> MetaProfileMutationResponse:
    """Stop applying ``name`` to launches without deleting it."""
    async with _org_meta_profiles_transaction(org_id, user_id) as (
        _session,
        _org,
        routers,
    ):
        if name not in routers.profiles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model Router '{name}' not found",
            )
        if routers.active != name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Model Router '{name}' is not the active router",
            )
        routers.active = None
    return MetaProfileMutationResponse(
        name=name, message=f"Model Router '{name}' deactivated"
    )


@router.patch('/{org_id}/meta-profiles', response_model=MetaProfileListResponse)
async def update_meta_profile_settings(
    org_id: UUID,
    body: MetaProfileSettingsUpdateRequest,
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
) -> MetaProfileListResponse:
    """Set the org-wide "Run on first message" behaviour.

    When on, every launch that carries the active router is also instructed to
    call ``route_task_to_model`` on the first user message (see
    ``LiveStatusAppConversationService``). The instruction is skipped for a
    launch that explicitly picks an LLM profile.
    """
    async with _org_meta_profiles_transaction(org_id, user_id) as (
        _session,
        _org,
        routers,
    ):
        routers.run_at_conversation_start = body.run_at_conversation_start
        response = _list_response(routers)
    return response
