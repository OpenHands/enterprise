from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from openhands.app_server.secrets.secrets_models import CustomSecretScope
from openhands.app_server.settings.llm_profiles import LLMProfiles
from server.auth.authorization import (
    Permission,
    authorize_permission,
    require_permission,
)
from server.auth.org_context import EFFECTIVE_ORG_ID
from server.constants import LITE_LLM_API_URL, is_model_router_enabled
from server.routes.agent_profiles import AgentProfileInfo
from server.routes.org_models import OrgNotFoundError
from server.routes.org_profiles import _load_profiles_with_live_default
from storage.agent_profile_resolution import (
    load_agent_profiles,
    load_member_agent_profiles,
    load_meta_profiles,
)
from storage.database import a_session_maker
from storage.mcp_config import coerce_persisted_mcp_config
from storage.org import Org
from storage.org_mcp_servers import org_managed_server_names
from storage.org_mcp_servers_store import OrgMcpServersStore
from storage.org_member import OrgMember
from storage.org_secrets_store import OrgSecretsStore
from storage.org_service import OrgService
from storage.saas_secrets_store import SaasSecretsStore, _resolve_unique_name
from storage.user import User
from storage.user_store import UserStore

router = APIRouter(prefix='/api/v1/agent-profile', tags=['Agent Profile Options'])

ResourceScope = Literal['personal', 'organization']


class SelectableResource(BaseModel):
    name: str
    scope: ResourceScope
    description: str | None = None
    source_name: str | None = None
    writable: bool = False
    deletable: bool = False


class LlmProfileOption(BaseModel):
    name: str
    scope: ResourceScope
    model: str | None = None
    base_url: str | None = None
    api_key_set: bool = False
    provider_connection_id: str | None = None
    writable: bool = False
    deletable: bool = False


class McpServerOption(BaseModel):
    name: str
    scope: ResourceScope
    description: str | None = None
    managed_by: Literal['user', 'org'] | None = None
    connected: bool | None = None
    writable: bool = False
    deletable: bool = False


class MetaProfileOption(BaseModel):
    name: str
    scope: Literal['organization'] = 'organization'
    classifier_model: str | None = None
    num_classes: int = 0
    active: bool = False
    writable: bool = False
    deletable: bool = False


class AgentProfileOptionsCapabilities(BaseModel):
    can_manage_personal_agent_profiles: bool = True
    can_manage_org_agent_profiles: bool = False
    can_manage_personal_secrets: bool = True
    can_manage_org_secrets: bool = False
    can_manage_personal_mcp: bool = True
    can_manage_org_mcp: bool = False
    can_manage_org_llm_profiles: bool = False
    can_manage_model_routers: bool = False


class AgentProfileOptionsResponse(BaseModel):
    agent_profiles: list[AgentProfileInfo]
    active_agent_profile_id: str | None = None
    llm_profiles: list[LlmProfileOption]
    secrets: list[SelectableResource]
    mcp_servers: list[McpServerOption]
    org_mcp_server_names: list[str]
    org_managed_mcp_server_names: list[str]
    model_routers: list[MetaProfileOption]
    capabilities: AgentProfileOptionsCapabilities


async def _get_org_and_member(org_id: UUID, user_id: str) -> tuple[Org, OrgMember]:
    try:
        org = await OrgService.get_org_by_id(org_id=org_id, user_id=user_id)
    except OrgNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    async with a_session_maker() as session:
        result = await session.execute(
            select(OrgMember).filter(
                OrgMember.org_id == org_id,
                OrgMember.user_id == UUID(user_id),
            )
        )
        member = result.scalars().first()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Organization membership not found',
        )
    return org, member


async def _can(request: Request, user_id: str, permission: Permission) -> bool:
    try:
        await authorize_permission(request, user_id, permission)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            return False
        raise
    return True


def _personal_llm_profiles(user: User | None) -> LLMProfiles:
    if user is None or user.llm_profiles is None:
        return LLMProfiles()
    try:
        return LLMProfiles.model_validate(user.llm_profiles)
    except Exception:
        return LLMProfiles()


def _llm_options(
    *,
    personal: LLMProfiles,
    organization: LLMProfiles,
    can_manage_org: bool,
) -> list[LlmProfileOption]:
    options: list[LlmProfileOption] = []
    for summary in personal.summaries(managed_proxy_url=LITE_LLM_API_URL):
        options.append(
            LlmProfileOption(
                scope='personal',
                writable=True,
                deletable=True,
                **summary,
            )
        )
    personal_names = {option.name for option in options}
    for summary in organization.summaries(managed_proxy_url=LITE_LLM_API_URL):
        if summary['name'] in personal_names:
            continue
        options.append(
            LlmProfileOption(
                scope='organization',
                writable=can_manage_org,
                deletable=can_manage_org,
                **summary,
            )
        )
    return options


async def _secret_options(
    *,
    user_id: str,
    org_id: UUID,
    can_manage_org: bool,
) -> list[SelectableResource]:
    from storage.encrypt_utils import get_jwt_service

    personal = await SaasSecretsStore(
        user_id,
        get_jwt_service(),
        effective_org_id=org_id,
    ).list_personal()
    shared = await (await OrgSecretsStore.get_instance(org_id)).list_shared()

    options: list[SelectableResource] = []
    taken: set[str] = set()
    for name, description in sorted(personal):
        effective_name = _resolve_unique_name(name, taken)
        options.append(
            SelectableResource(
                name=effective_name,
                source_name=name,
                description=description,
                scope=CustomSecretScope.PERSONAL.value,
                writable=True,
                deletable=True,
            )
        )
        taken.add(effective_name)
    for secret in sorted(shared, key=lambda item: item.name):
        effective_name = _resolve_unique_name(secret.name, taken)
        options.append(
            SelectableResource(
                name=effective_name,
                source_name=secret.name,
                description=secret.description,
                scope=CustomSecretScope.ORGANIZATION.value,
                writable=can_manage_org,
                deletable=can_manage_org,
            )
        )
        taken.add(effective_name)
    return options


def _member_mcp_options(member: OrgMember) -> list[McpServerOption]:
    raw = member.effective_mcp_config
    if not raw:
        return []
    try:
        config = coerce_persisted_mcp_config(raw)
    except Exception:
        return []
    return [
        McpServerOption(
            name=name,
            scope='personal',
            description=str(server.transport) if server.transport else None,
            managed_by='user',
            writable=True,
            deletable=True,
        )
        for name, server in sorted(config.items())
    ]


async def _mcp_options(
    *,
    org_id: UUID,
    member: OrgMember,
    can_manage_org: bool,
) -> tuple[list[McpServerOption], list[str], list[str]]:
    options = _member_mcp_options(member)
    taken = {option.name for option in options}
    listings = await OrgMcpServersStore(org_id).list()
    org_names = sorted(listings)
    org_managed_names = sorted(org_managed_server_names(listings))
    connected_names = await OrgMcpServersStore(org_id).member_connected_names(
        member.user_id
    )
    for name, entry in sorted(listings.items()):
        if name in taken:
            continue
        options.append(
            McpServerOption(
                name=name,
                scope='organization',
                description=str(entry.server.url) if entry.server.url else None,
                managed_by=entry.managed_by,
                connected=(name in connected_names)
                if entry.managed_by == 'user'
                else None,
                writable=can_manage_org,
                deletable=can_manage_org,
            )
        )
    return options, org_names, org_managed_names


def _meta_profile_options(org: Org, can_manage: bool) -> list[MetaProfileOption]:
    if not is_model_router_enabled():
        return []
    routers = load_meta_profiles(org)
    return [
        MetaProfileOption(
            name=item['name'],
            classifier_model=item.get('classifier_model'),
            num_classes=item.get('num_classes') or 0,
            active=item['name'] == routers.active,
            writable=can_manage,
            deletable=can_manage,
        )
        for item in routers.summaries()
    ]


@router.get('/options', response_model=AgentProfileOptionsResponse)
async def get_agent_profile_options(
    request: Request,
    effective_org_id: UUID = EFFECTIVE_ORG_ID,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> AgentProfileOptionsResponse:
    org, member = await _get_org_and_member(effective_org_id, user_id)
    user = await UserStore.get_user_by_id(user_id)

    can_edit_org = await _can(request, user_id, Permission.EDIT_ORG_SETTINGS)
    can_manage_org_secrets = await _can(request, user_id, Permission.MANAGE_ORG_SECRETS)
    can_manage_org_mcp = await _can(request, user_id, Permission.MANAGE_ORG_MCP)

    personal_profiles = load_member_agent_profiles(member)
    org_profiles = load_agent_profiles(org)
    active_id = member.active_agent_profile_id or org_profiles.active
    agent_profiles = [
        *(
            AgentProfileInfo(**summary, scope='personal')
            for summary in personal_profiles.list_summaries()
        ),
        *(
            AgentProfileInfo(**summary, scope='org')
            for summary in org_profiles.list_summaries()
        ),
    ]

    organization_llm_profiles = await _load_profiles_with_live_default(org)
    personal_llm_profiles = _personal_llm_profiles(user)
    mcp_servers, org_mcp_names, org_managed_mcp_names = await _mcp_options(
        org_id=effective_org_id,
        member=member,
        can_manage_org=can_manage_org_mcp,
    )

    return AgentProfileOptionsResponse(
        agent_profiles=agent_profiles,
        active_agent_profile_id=active_id,
        llm_profiles=_llm_options(
            personal=personal_llm_profiles,
            organization=organization_llm_profiles,
            can_manage_org=can_edit_org,
        ),
        secrets=await _secret_options(
            user_id=user_id,
            org_id=effective_org_id,
            can_manage_org=can_manage_org_secrets,
        ),
        mcp_servers=mcp_servers,
        org_mcp_server_names=org_mcp_names,
        org_managed_mcp_server_names=org_managed_mcp_names,
        model_routers=_meta_profile_options(org, can_edit_org),
        capabilities=AgentProfileOptionsCapabilities(
            can_manage_org_agent_profiles=can_edit_org,
            can_manage_org_secrets=can_manage_org_secrets,
            can_manage_org_mcp=can_manage_org_mcp,
            can_manage_org_llm_profiles=can_edit_org,
            can_manage_model_routers=can_edit_org and is_model_router_enabled(),
        ),
    )
