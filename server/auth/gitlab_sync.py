import asyncio
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.types import AppMode
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.constants import is_oauth_v2_login_enabled
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


async def _linked_gitlab_providers(user_id: str) -> list[AuthProvider]:
    from storage.auth_provider_store import AuthProviderStore

    return await AuthProviderStore().get_linked_git_providers(
        UUID(user_id), ProviderType.GITLAB.value
    )


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
            providers = (
                await _linked_gitlab_providers(user_id)
                if is_oauth_v2_login_enabled()
                else []
            )
            if not providers and not await _user_has_keycloak_gitlab_token(user_id):
                logger.debug(
                    'gitlab_repo_sync_skipped: user has no GitLab token',
                    extra={'user_id': user_id},
                )
                return

            # Lazy import to avoid circular dependency:
            # middleware -> gitlab_sync -> integrations.gitlab.gitlab_service
            # -> openhands.app_server.integrations.gitlab.gitlab_service -> get_impl
            # -> integrations.gitlab.gitlab_service (circular)
            from integrations.gitlab.gitlab_service import (
                SaaSGitLabService,
                provider_service_kwargs,
            )

            # One service per linked GitLab provider, each with its own token and
            # host. A user with only a Keycloak token has no provider to name.
            services = [
                SaaSGitLabService(
                    external_auth_id=user_id,
                    **await provider_service_kwargs(provider.id),
                )
                for provider in providers
            ] or [
                SaaSGitLabService(
                    external_auth_id=user_id,
                    external_auth_token=keycloak_access_token,
                )
            ]
            for service in services:
                try:
                    await service.get_all_repositories(
                        'pushed', AppMode.SAAS, store_in_background=False
                    )
                except Exception:
                    logger.warning(
                        'gitlab_repo_sync_failed',
                        extra={'provider_id': service.auth_provider_id},
                        exc_info=True,
                    )
        except Exception:
            logger.warning('gitlab_repo_sync_failed', exc_info=True)

    _ = asyncio.create_task(_run())
