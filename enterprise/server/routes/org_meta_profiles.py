"""Organization Model Router (meta-profile) CRUD and activation."""

import contextlib
from typing import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field, ValidationError
from server.routes.org_models import OrgNotFoundError
from server.routes.org_profiles import _load_profiles, _resolve_provider_connection
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from storage.database import a_session_maker
from storage.org import Org
from storage.org_service import OrgService

from openhands.sdk.llm.llm_profile_store import PROFILE_NAME_PATTERN
from openhands.sdk.llm.meta_profile_store import MetaProfile

from ..auth.authorization import Permission, require_permission

router = APIRouter(tags=["Organization Model Routers"])
MAX_META_PROFILES = 50


class OrgMetaProfiles(BaseModel):
    profiles: dict[str, MetaProfile] = Field(default_factory=dict)
    active: str | None = None


class MetaProfileInfo(BaseModel):
    name: str
    classifier_model: str
    default_model: str
    num_classes: int


class MetaProfileListResponse(BaseModel):
    meta_profiles: list[MetaProfileInfo]
    active_meta_profile: str | None = None


class MetaProfileDetailResponse(BaseModel):
    name: str
    config: MetaProfile


class MetaProfileMutationResponse(BaseModel):
    name: str
    message: str


class ActivateMetaProfileResponse(BaseModel):
    name: str
    message: str


def _load_meta_profiles(org: Org) -> OrgMetaProfiles:
    if org.meta_profiles is None:
        return OrgMetaProfiles()
    try:
        return OrgMetaProfiles.model_validate(org.meta_profiles)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stored organization Model Router data is invalid.",
        ) from exc


async def _get_org(org_id: UUID, user_id: str) -> Org:
    try:
        return await OrgService.get_org_by_id(org_id=org_id, user_id=user_id)
    except OrgNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@contextlib.asynccontextmanager
async def _org_meta_profiles_transaction(
    org_id: UUID, user_id: str
) -> AsyncIterator[tuple[AsyncSession, Org, OrgMetaProfiles]]:
    await _get_org(org_id, user_id)
    async with a_session_maker() as session:
        result = await session.execute(
            select(Org).filter(Org.id == org_id).with_for_update()
        )
        org = result.scalars().first()
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Organization {org_id} not found",
            )
        meta_profiles = _load_meta_profiles(org)
        yield session, org, meta_profiles
        org.meta_profiles = meta_profiles.model_dump(mode="json")
        await session.commit()


def _apply_active_router(
    org: Org, name: str | None, config: MetaProfile | None
) -> None:
    settings = dict(org.agent_settings or {})
    if name is None:
        settings["enable_classify_and_switch_llm_tool"] = False
        settings["active_meta_profile"] = None
        settings["meta_profile"] = None
    else:
        settings["enable_classify_and_switch_llm_tool"] = True
        settings["active_meta_profile"] = name
        settings["meta_profile"] = config.model_dump(mode="json") if config else None
    org.agent_settings = settings


def _validate_profile_references(org: Org, config: MetaProfile) -> None:
    profiles = _load_profiles(org)
    referenced_names = {
        config.classifier_model,
        config.default_model,
        *(item.model for item in config.classes),
    }
    for name in sorted(referenced_names):
        llm = profiles.get(name)
        if llm is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Model Router references missing LLM profile '{name}'.",
            )
        # Also reject a dangling shared-provider reference while the org row is
        # locked, before the router can become an unusable active configuration.
        _resolve_provider_connection(org, llm)


@router.get("/{org_id}/meta-profiles", response_model=MetaProfileListResponse)
async def list_meta_profiles(
    org_id: UUID,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> MetaProfileListResponse:
    profiles = _load_meta_profiles(await _get_org(org_id, user_id))
    return MetaProfileListResponse(
        meta_profiles=[
            MetaProfileInfo(
                name=name,
                classifier_model=config.classifier_model,
                default_model=config.default_model,
                num_classes=len(config.classes),
            )
            for name, config in sorted(profiles.profiles.items())
        ],
        active_meta_profile=profiles.active,
    )


@router.get("/{org_id}/meta-profiles/{name}", response_model=MetaProfileDetailResponse)
async def get_meta_profile(
    org_id: UUID,
    name: str = Path(..., pattern=PROFILE_NAME_PATTERN),
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> MetaProfileDetailResponse:
    profiles = _load_meta_profiles(await _get_org(org_id, user_id))
    config = profiles.profiles.get(name)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Meta-profile '{name}' not found")
    return MetaProfileDetailResponse(name=name, config=config)


@router.post(
    "/{org_id}/meta-profiles/{name}",
    response_model=MetaProfileMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def save_meta_profile(
    org_id: UUID,
    body: MetaProfile,
    name: str = Path(..., pattern=PROFILE_NAME_PATTERN),
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
) -> MetaProfileMutationResponse:
    async with _org_meta_profiles_transaction(org_id, user_id) as (
        _session,
        org,
        profiles,
    ):
        if (
            name not in profiles.profiles
            and len(profiles.profiles) >= MAX_META_PROFILES
        ):
            raise HTTPException(
                status_code=409, detail="Meta-profile limit reached (50)."
            )
        _validate_profile_references(org, body)
        profiles.profiles[name] = body
        if profiles.active == name:
            _apply_active_router(org, name, body)
    return MetaProfileMutationResponse(
        name=name, message=f"Meta-profile '{name}' saved"
    )


@router.delete(
    "/{org_id}/meta-profiles/{name}", response_model=MetaProfileMutationResponse
)
async def delete_meta_profile(
    org_id: UUID,
    name: str = Path(..., pattern=PROFILE_NAME_PATTERN),
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
) -> MetaProfileMutationResponse:
    async with _org_meta_profiles_transaction(org_id, user_id) as (
        _session,
        org,
        profiles,
    ):
        profiles.profiles.pop(name, None)
        if profiles.active == name:
            profiles.active = None
            _apply_active_router(org, None, None)
    return MetaProfileMutationResponse(
        name=name, message=f"Meta-profile '{name}' deleted"
    )


@router.post(
    "/{org_id}/meta-profiles/{name}/activate",
    response_model=ActivateMetaProfileResponse,
)
async def activate_meta_profile(
    org_id: UUID,
    name: str = Path(..., pattern=PROFILE_NAME_PATTERN),
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
) -> ActivateMetaProfileResponse:
    async with _org_meta_profiles_transaction(org_id, user_id) as (
        _session,
        org,
        profiles,
    ):
        config = profiles.profiles.get(name)
        if config is None:
            raise HTTPException(
                status_code=404, detail=f"Meta-profile '{name}' not found"
            )
        profiles.active = name
        _apply_active_router(org, name, config)
    return ActivateMetaProfileResponse(
        name=name, message=f"Meta-profile '{name}' activated"
    )
