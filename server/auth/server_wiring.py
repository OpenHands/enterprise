"""Explicit server and worker composition for the selected installation."""

import asyncio
import os

from fastapi import FastAPI

from server.auth import auth_config
from server.auth.composition import get_auth_services
from server.logger import logger


def configure_authentication_environment() -> None:
    from server.config import validate_native_auth_configuration

    validate_native_auth_configuration()
    if not auth_config.ENABLE_KEYCLOAK:
        os.environ.setdefault('APP_MODE', 'saas')
        os.environ.setdefault('OH_APP_MODE', 'saas')
        os.environ.setdefault(
            'OH_LIFESPAN_KIND',
            'server.app_lifespan.saas_app_lifespan_service.SaasAppLifespanService',
        )


def install_authentication_routes(base_app: FastAPI) -> None:
    get_auth_services()
    if auth_config.ENABLE_KEYCLOAK:
        _install_keycloak_routes(base_app)
    else:
        _install_openhands_routes(base_app)


def _install_keycloak_routes(base_app: FastAPI) -> None:
    from server.auth.constants import (
        AZURE_DEVOPS_CLIENT_ID,
        BITBUCKET_APP_CLIENT_ID,
        BITBUCKET_DATA_CENTER_HOST,
        GITHUB_APP_CLIENT_ID,
        GITLAB_APP_CLIENT_ID,
    )
    from server.routes.auth import oauth_router
    from server.routes.github_proxy import add_github_proxy_routes
    from server.routes.integration.slack import slack_router

    get_auth_services()
    base_app.include_router(oauth_router)
    if GITHUB_APP_CLIENT_ID:
        from integrations.github.github_v1_callback_processor import (
            GithubV1CallbackProcessor,
        )
        from server.routes.integration.github import github_integration_router

        logger.debug(f'Loaded {GithubV1CallbackProcessor.__name__}')
        base_app.include_router(github_integration_router)
    if GITLAB_APP_CLIENT_ID:
        from integrations.gitlab.gitlab_v1_callback_processor import (
            GitlabV1CallbackProcessor,
        )
        from server.routes.integration.gitlab import gitlab_integration_router

        logger.debug(f'Loaded {GitlabV1CallbackProcessor.__name__}')
        base_app.include_router(gitlab_integration_router)
    if BITBUCKET_APP_CLIENT_ID:
        from integrations.bitbucket.bitbucket_v1_callback_processor import (
            BitbucketV1CallbackProcessor,
        )
        from server.routes.integration.bitbucket import bitbucket_integration_router

        logger.debug(f'Loaded {BitbucketV1CallbackProcessor.__name__}')
        base_app.include_router(bitbucket_integration_router)
    if AZURE_DEVOPS_CLIENT_ID:
        from integrations.azure_devops.azure_devops_v1_callback_processor import (
            AzureDevOpsV1CallbackProcessor,
        )
        from server.routes.integration.azure_devops import (
            azure_devops_integration_router,
        )

        logger.debug(f'Loaded {AzureDevOpsV1CallbackProcessor.__name__}')
        base_app.include_router(azure_devops_integration_router)
    add_github_proxy_routes(base_app)
    base_app.include_router(slack_router)
    if BITBUCKET_DATA_CENTER_HOST:
        from server.routes.bitbucket_dc_proxy import router as bitbucket_dc_proxy_router

        base_app.include_router(bitbucket_dc_proxy_router)
        from integrations.bitbucket_data_center.bitbucket_dc_v1_callback_processor import (
            BitbucketDCV1CallbackProcessor,
        )
        from server.routes.integration.bitbucket_dc import (
            bitbucket_dc_integration_router,
        )

        logger.debug(f'Loaded {BitbucketDCV1CallbackProcessor.__name__}')
        base_app.include_router(bitbucket_dc_integration_router)


def _install_openhands_routes(base_app: FastAPI) -> None:
    from server.routes.native_auth import native_auth_router

    base_app.include_router(native_auth_router)


def install_authentication_middleware(base_app: FastAPI) -> None:
    from openhands.app_server.middleware import CacheControlMiddleware
    from server.constants import PERMITTED_CORS_ORIGINS
    from server.middleware import ApiKeyAwareCORSMiddleware, SetAuthCookieMiddleware

    if auth_config.ENABLE_KEYCLOAK:
        base_app.add_middleware(
            ApiKeyAwareCORSMiddleware, allow_origins=PERMITTED_CORS_ORIGINS
        )
        base_app.add_middleware(CacheControlMiddleware)
        base_app.middleware('http')(SetAuthCookieMiddleware())
    else:
        from server.config import get_native_cors_origins

        base_app.add_middleware(CacheControlMiddleware)
        base_app.middleware('http')(SetAuthCookieMiddleware())
        # Browser errors must also carry the authoritative CORS policy.
        base_app.add_middleware(
            ApiKeyAwareCORSMiddleware, allow_origins=get_native_cors_origins()
        )


async def start_authentication_server() -> asyncio.Task[None] | None:
    from server.auth.bootstrap import initialize_auth_installation

    get_auth_services()
    await initialize_auth_installation()
    if auth_config.ENABLE_KEYCLOAK:
        return None
    from server.services.native_maintenance_service import native_maintenance_loop

    return asyncio.create_task(native_maintenance_loop())


async def verify_authentication_worker() -> None:
    from server.auth.bootstrap import verify_auth_installation

    configure_authentication_environment()
    get_auth_services()
    await verify_auth_installation()


async def run_authentication_maintenance() -> None:
    await verify_authentication_worker()
    if auth_config.ENABLE_KEYCLOAK:
        return
    from server.services.native_maintenance_service import run_native_maintenance

    result = await run_native_maintenance()
    if result.get('error_count'):
        logger.warning('Authentication maintenance has pending retries', extra=result)
