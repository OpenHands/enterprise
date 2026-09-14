"""User router for OpenHands App Server. For the moment, this simply implements the /me endpoint."""

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse

from openhands.app_server.config import depends_user_context
from openhands.app_server.integrations.service_types import UserGitInfo
from openhands.app_server.sandbox.session_auth import validate_session_key_ownership
from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.user.user_models import UserInfo
from openhands.app_server.utils.dependencies import get_dependencies
from openhands.app_server.utils.litellm_integration import (
    LiteLLMIntegrationDisabled,
    is_litellm_enabled,
    is_managed_llm,
    validate_agent_llms,
)

# We use the get_dependencies method here to signal to the OpenAPI docs that this endpoint
# is protected. The actual protection is provided by SetAuthCookieMiddleware
router = APIRouter(prefix='/users', tags=['User'], dependencies=get_dependencies())
user_dependency = depends_user_context()

# Read methods


@router.get('/me', response_model=UserInfo)
async def get_current_user(
    user_context: UserContext = user_dependency,
    expose_secrets: bool = Query(
        default=False,
        description='If true, return unmasked secret values (e.g. llm_api_key). '
        'Requires a valid X-Session-API-Key header for an active sandbox '
        'owned by the authenticated user.',
    ),
    x_session_api_key: str | None = Header(default=None),
) -> UserInfo | JSONResponse:
    """Get the current authenticated user."""
    user = await user_context.get_user_info()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail='Not authenticated')
    if not is_litellm_enabled():
        user = user.model_copy()
        user.llm_profiles = user.llm_profiles.model_copy(
            update={'profiles': dict(user.llm_profiles.profiles)}
        )
        llm = user.agent_settings.llm
        if llm is not None and is_managed_llm(llm.model, llm.base_url):
            user.agent_settings = user.agent_settings.model_copy(
                update={
                    'llm': Settings().agent_settings.llm.model_copy(
                        update={'api_key': None}
                    )
                }
            )
        user.llm_profiles.profiles = {
            name: llm
            for name, llm in user.llm_profiles.profiles.items()
            if not is_managed_llm(llm.model, llm.base_url)
        }
        if user.llm_profiles.active not in user.llm_profiles.profiles:
            user.llm_profiles.active = None
    if expose_secrets:
        await validate_session_key_ownership(user_context, x_session_api_key)
        try:
            validate_agent_llms(user.agent_settings)
        except LiteLLMIntegrationDisabled as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(
            content=user.model_dump(mode='json', context={'expose_secrets': True})
        )
    return user


@router.get('/git-info')
async def get_current_user_git_info(
    user_context: UserContext = user_dependency,
) -> UserGitInfo:
    """Get the current authenticated user's metadata from the git provider."""
    user = await user_context.get_user_git_info()
    if user is None:
        # Return 403 Forbidden (not 401) when user has no git provider connected
        # 401 would trigger frontend logout, but the user IS authenticated - they just
        # don't have a git provider (e.g., logged in via SAML without GitHub linked)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail='Git provider not connected',
        )
    return user
