import asyncio

from pydantic import SecretStr
from sqlalchemy import select

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.types import AppMode
from openhands.app_server.utils.logger import openhands_logger as logger

_sync_tasks: set[asyncio.Task[None]] = set()


async def _user_has_gitlab_provider(user_id: str) -> bool:
    """Check if the user has authenticated with GitLab.

    Args:
        user_id: The Keycloak user ID

    Returns:
        True if the user has a GitLab provider token, False otherwise
    """
    from server.auth.auth_config import ENABLE_KEYCLOAK

    if not ENABLE_KEYCLOAK:
        from server.auth.native_git_config import git_config
        from server.services.native_git_credentials import get_native_git_service
        from server.services.native_git_provider import GitCredentialError

        try:
            token = await get_native_git_service().get_token(
                user_id, ProviderType.GITLAB
            )
            return token.host == git_config('gitlab').host
        except (GitCredentialError, ValueError):
            return False
    # Lazy import to avoid circular dependency issues at module load time
    from storage.auth_tokens import AuthTokens
    from storage.database import a_session_maker

    async with a_session_maker() as session:
        result = await session.execute(
            select(AuthTokens).where(
                AuthTokens.keycloak_user_id == user_id,
                AuthTokens.identity_provider == ProviderType.GITLAB.value,
            )
        )
        return result.scalars().first() is not None


def schedule_gitlab_repo_sync(
    user_id: str, keycloak_access_token: SecretStr | None = None
) -> None:
    """Schedule a background sync of GitLab repositories and webhook tracking.

    Because the outer call is already a background task, we instruct the service
    to store repository data synchronously (store_in_background=False) to avoid
    nested background tasks while still keeping the overall operation async.

    The sync is only performed if the user has authenticated with GitLab.
    """

    async def _run() -> None:
        try:
            # Check if the user has a GitLab provider token before syncing
            if not await _user_has_gitlab_provider(user_id):
                logger.debug(
                    'gitlab_repo_sync_skipped: user has no GitLab provider',
                    extra={'user_id': user_id},
                )
                return

            # Lazy import to avoid circular dependency:
            # middleware -> gitlab_sync -> integrations.gitlab.gitlab_service
            # -> openhands.app_server.integrations.gitlab.gitlab_service -> get_impl
            # -> integrations.gitlab.gitlab_service (circular)
            from integrations.gitlab.gitlab_service import SaaSGitLabService

            service = SaaSGitLabService(
                external_auth_id=user_id, external_auth_token=keycloak_access_token
            )
            await service.get_all_repositories(
                'pushed', AppMode.SAAS, store_in_background=False
            )
        except Exception:
            logger.warning('gitlab_repo_sync_failed', exc_info=True)

    task = asyncio.create_task(_run())
    _sync_tasks.add(task)
    task.add_done_callback(_sync_tasks.discard)
