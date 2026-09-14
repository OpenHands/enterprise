"""SAAS-specific extensions for the /api/v1/users endpoints.

This module provides SAAS-specific implementations that extend the OSS
user endpoints with organization context (org_id, org_name, role, permissions).
"""

import logging
from uuid import UUID

from fastapi import (
    APIRouter,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from openhands.app_server.config import (
    depends_user_context,
    resolve_provider_llm_base_url,
)
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.sandbox.session_auth import validate_session_key_ownership
from openhands.app_server.settings.llm_profiles import resolve_profile_llm
from openhands.app_server.settings.provider_connections import (
    ProviderConnectionNotFoundError,
    ProviderConnections,
)
from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.user.auth_user_context import AuthUserContext
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.dependencies import get_dependencies
from openhands.app_server.utils.litellm_integration import (
    LiteLLMIntegrationDisabled,
    is_litellm_enabled,
    is_managed_llm,
    validate_agent_llms,
)
from server.auth import authorization
from server.auth.auth_config import ENABLE_KEYCLOAK
from server.auth.saas_user_auth import SaasUserAuth
from server.auth.token_manager import TokenManager
from server.constants import LITE_LLM_API_URL
from server.models.user_models import GitOrganizationsResponse, SaasUserInfo
from server.routes.org_models import OrgNotFoundError
from server.routes.org_provider_connections import (
    _load_connections as load_provider_connections,
)
from storage.org_service import OrgService

_logger = logging.getLogger(__name__)

saas_users_v1_router = APIRouter(
    prefix='/api/v1/users', tags=['User'], dependencies=get_dependencies()
)
user_dependency = depends_user_context()
token_manager = TokenManager()


class _SDKCompatLLM(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    model: str | None = None
    base_url: str | None = None
    api_key: str | dict[str, JsonValue] | None = Field(default=None, repr=False)


class _SDKCompatAgentSettings(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    llm: _SDKCompatLLM | None = None
    mcp_config: dict[str, JsonValue] | None = Field(default=None, repr=False)


class _SDKCompatFields(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    agent_settings: _SDKCompatAgentSettings | None = None
    has_password: bool | None = None


_USER_WIRE: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(
    dict[str, JsonValue], config=ConfigDict(hide_input_in_errors=True)
)


def _inject_sdk_compat_fields(
    content: dict[str, JsonValue], *, include_api_key: bool
) -> None:
    """Inject flat top-level convenience fields for the SDK.

    The SDK's ``get_llm()`` and ``get_mcp_config()`` read ``llm_model``,
    ``llm_api_key``, ``llm_base_url``, and ``mcp_config`` from the top
    level of the ``/api/v1/users/me`` response. These values live inside
    the nested ``agent_settings`` structure, so we mirror them at the top
    level for backward compatibility.

    The canonical representation is ``agent_settings``; these flat fields
    exist solely for SDK backward compatibility.
    """
    projection = _SDKCompatFields.model_validate(content)
    agent_settings = projection.agent_settings or _SDKCompatAgentSettings()
    if projection.has_password is None:
        # Native account capabilities do not alter Keycloak response shape.
        content.pop('has_password', None)
        content.pop('authentication_methods', None)
    llm = agent_settings.llm or _SDKCompatLLM()
    content['llm_model'] = llm.model
    content['llm_base_url'] = resolve_provider_llm_base_url(llm.model, llm.base_url)
    if include_api_key:
        content['llm_api_key'] = llm.api_key
    content['mcp_config'] = agent_settings.mcp_config


def _resolve_exposed_llm_profiles(user_info: SaasUserInfo) -> None:
    """Overlay each saved LLM profile with its effective runtime config.

    SaaS profiles never persist a real api_key: the org-profiles routes lift
    real keys into the encrypted member column and store a masked placeholder,
    which the LLM validator nulls at load. The SDK's
    ``OpenHandsCloudWorkspace.get_llm(profile_name=...)`` builds an LLM
    verbatim from this response's ``llm_profiles``, so without this overlay a
    profile-pinned run (e.g. SaaS automations) calls the LiteLLM proxy with no
    credentials. Mirrors ``_seed_sandbox_profiles`` and the ``switch_profile``
    endpoint: resolve the managed/provider-default ``base_url``, force
    streaming, and fall back to the user's effective settings key for keyless
    profiles; BYOR profiles with real keys keep their own key.
    """
    settings_llm = user_info.agent_settings.llm
    fallback_api_key = settings_llm.api_key
    profiles = user_info.llm_profiles.profiles
    for name, profile_llm in list(profiles.items()):
        if not is_litellm_enabled() and is_managed_llm(
            profile_llm.model, profile_llm.base_url
        ):
            del profiles[name]
            continue
        profiles[name] = resolve_profile_llm(
            profile_llm,
            managed_proxy_url=LITE_LLM_API_URL,
            fallback_api_key=fallback_api_key,
            fallback_llm=settings_llm,
        )


async def _load_provider_connections_for_user(
    user_info: SaasUserInfo, user_context: UserContext
) -> ProviderConnections:
    if not user_info.org_id:
        return ProviderConnections()
    try:
        org_id = UUID(user_info.org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f'Invalid organization id: {user_info.org_id}',
        ) from exc

    user_id = await user_context.get_user_id()
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail='Not authenticated')

    try:
        org = await OrgService.get_org_by_id(org_id=org_id, user_id=user_id)
    except OrgNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return load_provider_connections(org)


def _provider_connection_422(exc: ProviderConnectionNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=(
            f"Profile references provider connection '{exc.connection_id}', "
            'which does not exist. Update the profile or recreate the connection.'
        ),
    )


@saas_users_v1_router.get('/me', response_model=SaasUserInfo)
async def get_current_user_saas(
    user_context: UserContext = user_dependency,
    expose_secrets: bool = Query(
        default=False,
        description='If true, return unmasked secret values (e.g. llm_api_key). '
        'Requires a valid X-Session-API-Key header for an active sandbox '
        'owned by the authenticated user.',
    ),
    x_session_api_key: str | None = Header(default=None),
) -> JSONResponse:
    """Get the current authenticated user with SAAS-specific org info.

    Returns user settings along with organization context:
    - org_id: Current organization ID
    - org_name: Current organization name
    - role: User's role in the organization
    - permissions: List of permission strings for the role
    """
    # Get base user info from the context
    base_user_info = await user_context.get_user_info()
    if base_user_info is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail='Not authenticated')

    # Build SAAS user info from base settings
    user_info_data = _USER_WIRE.validate_python(
        base_user_info.model_dump(mode='json', context={'expose_secrets': True})
    )

    # Add org info if available (from SaasUserAuth)
    org_info = await _get_org_info_from_context(user_context)
    if org_info:
        user_info_data.update(org_info)

    if isinstance(user_context, AuthUserContext) and isinstance(
        user_context.user_auth, SaasUserAuth
    ):
        super_role = await authorization.get_user_super_role(
            user_context.user_auth.user_id
        )
        user_info_data['global_permissions'] = (
            [
                permission
                for permission in sorted(
                    permission.value
                    for permission in authorization.get_super_role_permissions(
                        super_role.name
                    )
                )
            ]
            if super_role
            else []
        )

    if not ENABLE_KEYCLOAK:
        from server.services.native_auth_service import get_native_auth_service

        native_user_id = await user_context.get_user_id()
        if native_user_id is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail='Not authenticated'
            )
        user_info_data.update(
            _USER_WIRE.validate_python(
                await get_native_auth_service().profile_metadata(UUID(native_user_id))
            )
        )

    user_info = SaasUserInfo.model_validate(user_info_data)
    if not is_litellm_enabled():
        llm = user_info.agent_settings.llm
        if is_managed_llm(llm.model, llm.base_url):
            # /settings retains the historical choice for editing. The SDK
            # projection must never export that choice or its managed key.
            setup_llm = Settings().agent_settings.llm.model_copy(
                update={'api_key': None}
            )
            user_info.agent_settings = user_info.agent_settings.model_copy(
                update={'llm': setup_llm}
            )
        profiles = user_info.llm_profiles
        profiles.profiles = {
            name: llm
            for name, llm in profiles.profiles.items()
            if not is_managed_llm(llm.model, llm.base_url)
        }
        if profiles.active not in profiles.profiles:
            profiles.active = None

    if expose_secrets:
        await validate_session_key_ownership(user_context, x_session_api_key)
        try:
            validate_agent_llms(user_info.agent_settings)
            if user_info.title_llm_profile and not user_info.llm_profiles.has(
                user_info.title_llm_profile
            ):
                raise LiteLLMIntegrationDisabled(
                    'The selected title LLM profile is unavailable. Select a direct provider profile.'
                )
            _resolve_exposed_llm_profiles(user_info)
        except LiteLLMIntegrationDisabled as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        content = _USER_WIRE.validate_python(
            user_info.model_dump(mode='json', context={'expose_secrets': True})
        )
        _inject_sdk_compat_fields(content, include_api_key=True)
        return JSONResponse(content=content)

    content = _USER_WIRE.validate_python(user_info.model_dump(mode='json'))
    _inject_sdk_compat_fields(content, include_api_key=False)
    return JSONResponse(content=content)


@saas_users_v1_router.get('/git-organizations')
async def get_current_user_git_organizations(
    user_context: UserContext = user_dependency,
) -> GitOrganizationsResponse:
    """Return the Git organizations, groups, or workspaces the user belongs to.

    on their active provider.

    In SAAS mode users sign in with one provider at a time, so the response
    reflects that single provider.
    """
    provider_tokens = await user_context.get_provider_tokens()
    if not provider_tokens:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,  # 403 not 401 to avoid frontend logout
            detail='Git provider token required.',
        )

    client = await user_context.get_provider_handler()
    provider_tokens = client.provider_tokens
    provider = next(iter(provider_tokens))
    if not ENABLE_KEYCLOAK:
        from server.auth.native_git_config import git_config

        credential = provider_tokens[provider]
        if credential.host != git_config(provider.value).host:
            raise HTTPException(
                409,
                'Git organization integrations require the configured default provider host',
            )
    if provider == ProviderType.GITHUB:
        orgs = await client.get_github_organizations()
    elif provider == ProviderType.GITLAB:
        orgs = await client.get_gitlab_groups()
    elif provider == ProviderType.BITBUCKET:
        orgs = await client.get_bitbucket_workspaces()
    elif provider == ProviderType.BITBUCKET_DATA_CENTER:
        orgs = await client.get_bitbucket_dc_projects()
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {provider.value} doesn't support git organizations",
        )

    return GitOrganizationsResponse(provider=provider, organizations=orgs)


@saas_users_v1_router.delete(
    '/git-providers/{provider}', status_code=status.HTTP_204_NO_CONTENT
)
async def disconnect_git_provider(
    provider: ProviderType,
    user_context: UserContext = user_dependency,
) -> Response:
    """Disconnect a git provider linked to the user's Keycloak account.

    Removes the Keycloak federated identity and the stored provider tokens, so
    the provider shows as not connected until the user links it again from
    Settings > Integrations.
    """
    if provider == ProviderType.ENTERPRISE_SSO:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {provider.value} can't be disconnected",
        )

    user_id = await user_context.get_user_id()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='User is not authenticated',
        )

    await token_manager.unlink_idp(user_id, provider)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _get_org_info_from_context(
    user_context: UserContext,
) -> dict[str, JsonValue] | None:
    """Extract org info from the user context if available.

    This works by checking if the underlying user_auth is a SaasUserAuth
    instance that has the get_org_info method.
    """
    # Check if this is an AuthUserContext with a SaasUserAuth
    if isinstance(user_context, AuthUserContext):
        user_auth = user_context.user_auth
        if isinstance(user_auth, SaasUserAuth):
            org_info = await user_auth.get_org_info()
            return (
                _USER_WIRE.validate_python(org_info) if org_info is not None else None
            )
    return None


def override_users_me_endpoint(app: FastAPI) -> None:
    """Override the OSS /api/v1/users/me endpoint with SAAS version.

    This removes the base OSS endpoint and registers the SAAS version
    which includes organization context (org_id, org_name, role, permissions).

    Must be called after the app is created in saas_server.py.
    """
    # Find and remove the OSS /api/v1/users/me route
    routes_to_remove = []
    for route in app.routes:
        if hasattr(route, 'path') and route.path == '/api/v1/users/me':
            routes_to_remove.append(route)

    for route in routes_to_remove:
        app.routes.remove(route)
        _logger.debug('Removed OSS route: %s', route.path)

    # Add the SAAS version
    app.include_router(saas_users_v1_router)
    _logger.debug('Added SAAS /api/v1/users/me endpoint')
