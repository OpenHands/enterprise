import asyncio
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select

from openhands.app_server.integrations.gitlab.constants import GITLAB_HOST
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.types import AppMode
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.constants import is_oauth_v2_login_enabled
from server.auth.oauth_v2_refresh import get_gitlab_host
from storage.auth_provider import AuthProvider


async def _user_has_keycloak_gitlab_token(user_id: str) -> bool:
    # Lazy import to avoid circular dependency issues at module load time
    from storage.auth_tokens import AuthTokens
    from storage.database import a_session_maker

    async with a_session_maker() as session:
        result = await session.execute(
            select(AuthTokens.id).where(
                AuthTokens.keycloak_user_id == user_id,
                AuthTokens.identity_provider == ProviderType.GITLAB.value,
            )
        )
        return result.first() is not None


async def _linked_gitlab_provider_on_install_host(user_id: str) -> AuthProvider | None:
    """The user's OAuth v2 GitLab provider served by this install's ``GITLAB_HOST``.

    The repository and webhook tables hold bare GitLab ids with no host, so only
    the install's own host can be recorded in them. Syncing another GitLab host
    would file its project ids under the wrong instance.
    """
    from storage.auth_provider_store import AuthProviderStore

    providers = await AuthProviderStore().get_linked_git_providers(
        UUID(user_id), ProviderType.GITLAB.value
    )
    for provider in providers:
        host = get_gitlab_host(provider)
        if host and host.lower() == GITLAB_HOST.lower():
            return provider
    return None


def schedule_gitlab_repo_sync(
    user_id: str, keycloak_access_token: SecretStr | None = None
) -> None:
    """Schedule a background sync of GitLab repositories and webhook tracking.

    Because the outer call is already a background task, we instruct the service
    to store repository data synchronously (store_in_background=False) to avoid
    nested background tasks while still keeping the overall operation async.

    The sync is only performed if the user has authenticated with GitLab.
    """

    async def _run():
        try:
            provider = (
                await _linked_gitlab_provider_on_install_host(user_id)
                if is_oauth_v2_login_enabled()
                else None
            )
            if provider is None and not await _user_has_keycloak_gitlab_token(user_id):
                logger.debug(
                    'gitlab_repo_sync_skipped: no GitLab token for this install host',
                    extra={'user_id': user_id},
                )
                return

            # Lazy import to avoid circular dependency:
            # middleware -> gitlab_sync -> integrations.gitlab.gitlab_service
            # -> openhands.app_server.integrations.gitlab.gitlab_service -> get_impl
            # -> integrations.gitlab.gitlab_service (circular)
            from integrations.gitlab.gitlab_service import SaaSGitLabService

            service = SaaSGitLabService(
                external_auth_id=user_id,
                external_auth_token=keycloak_access_token,
                auth_provider_id=provider.id if provider else None,
            )
            await service.get_all_repositories(
                'pushed', AppMode.SAAS, store_in_background=False
            )
        except Exception:
            logger.warning('gitlab_repo_sync_failed', exc_info=True)

    _ = asyncio.create_task(_run())
