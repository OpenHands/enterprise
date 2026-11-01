"""The usage report this install sends Customer Center.

The worker's ``usage_report`` job (``openhands/app_server/worker/scheduled_jobs.py``)
runs ``run()`` every 30 minutes while ``OPENHANDS_LICENSE_KEY`` is set, whether
or not license enforcement is on. Each report carries lifetime totals, not
changes since the last report, so a missed or duplicate report loses nothing.
A failed report is logged and not retried; the next run is the retry.
"""

from __future__ import annotations

from typing import Callable, Literal

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.config import get_global_config
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.sandbox.docker_sandbox_service import (
    DockerSandboxServiceInjector,
)
from openhands.app_server.sandbox.e2b_sandbox_service import E2BSandboxServiceInjector
from openhands.app_server.sandbox.k8s_agent_sandbox_service import (
    K8sAgentSandboxServiceInjector,
)
from openhands.app_server.sandbox.process_sandbox_service import (
    ProcessSandboxServiceInjector,
)
from openhands.app_server.sandbox.remote_sandbox_service import (
    RemoteSandboxServiceInjector,
)
from openhands.app_server.sandbox.sandbox_service import SandboxServiceInjector
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.constants import (
    ENABLE_ENTERPRISE_SSO,
    KEYCLOAK_CLIENT_ID,
    is_oauth_v2_login_enabled,
)
from server.licensing.config import LicenseConfig
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import (
    AuthProviderDetails,
    OAuthProviderDetails,
    SamlProviderDetails,
)
from storage.database import a_session_maker
from storage.instance_settings_store import InstanceSettingsStore
from storage.org import Org
from storage.stored_conversation_metadata import StoredConversationMetadata
from storage.user import User

USAGE_REPORTS_PATH = '/api/v1/usage-reports'
REQUEST_TIMEOUT_SECONDS = 10.0

SandboxStrategy = Literal['docker', 'process', 'remote', 'e2b', 'k8s-agent-sandbox']

# Subclasses report as the strategy they extend.
_SANDBOX_STRATEGIES: tuple[
    tuple[type[SandboxServiceInjector], SandboxStrategy], ...
] = (
    (DockerSandboxServiceInjector, 'docker'),
    (ProcessSandboxServiceInjector, 'process'),
    (RemoteSandboxServiceInjector, 'remote'),
    (E2BSandboxServiceInjector, 'e2b'),
    (K8sAgentSandboxServiceInjector, 'k8s-agent-sandbox'),
)


class UsageReport(BaseModel):
    """The body of ``POST /api/v1/usage-reports``."""

    license_key: str
    instance_id: str = Field(min_length=1, max_length=200)
    conversations: int = Field(ge=0)
    users: int = Field(ge=0)
    automations: int = Field(ge=0)
    orgs: int = Field(ge=0)
    sso_enabled: bool
    personal_workspaces_enabled: bool
    sandbox_strategy: SandboxStrategy


async def run(
    http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> None:
    """Collect and send one report. Does nothing without a license key."""
    config = LicenseConfig.from_env()
    if config.license_key is None:
        return
    report = await collect_usage_report(config.license_key)
    if report is None:
        return
    await send_usage_report(
        report,
        config.license_server_url,
        http_client_factory
        or (lambda: httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)),
    )


async def collect_usage_report(license_key: str) -> UsageReport | None:
    """This install's lifetime totals and settings, or None if they can't be reported."""
    strategy = sandbox_strategy(get_global_config().sandbox)
    if strategy is None:
        logger.warning(
            'usage_report_unknown_sandbox',
            extra={'sandbox': type(get_global_config().sandbox).__name__},
        )
        return None
    instance_id = await InstanceSettingsStore.get_or_create_instance_id()
    async with a_session_maker() as session:
        # Lifetime totals: soft-deleted conversations still count.
        conversations = await session.scalar(
            select(func.count()).select_from(StoredConversationMetadata)
        )
        users = await session.scalar(select(func.count()).select_from(User))
        # A personal workspace shares its id with its user, so it is already
        # counted in users.
        orgs = await session.scalar(
            select(func.count())
            .select_from(Org)
            .where(~exists().where(User.id == Org.id))
        )
        sso = await sso_enabled(session)
    return UsageReport(
        license_key=license_key,
        instance_id=str(instance_id),
        conversations=conversations or 0,
        users=users or 0,
        # Automations live in the automation service's own database, which
        # this install can't count yet.
        automations=0,
        orgs=orgs or 0,
        sso_enabled=sso,
        personal_workspaces_enabled=await personal_workspaces_enabled(),
        sandbox_strategy=strategy,
    )


async def send_usage_report(
    report: UsageReport,
    license_server_url: str,
    http_client_factory: Callable[[], httpx.AsyncClient],
) -> bool:
    """POST the report. Logs and returns False on any failure; never raises."""
    url = f'{license_server_url}{USAGE_REPORTS_PATH}'
    try:
        async with http_client_factory() as client:
            response = await client.post(url, json=report.model_dump())
    except httpx.HTTPError as e:
        logger.warning(
            'usage_report_failed',
            extra={'error': f'{type(e).__name__}: {e}', 'url': url},
        )
        return False
    if response.status_code == 204:
        logger.info('usage_report_sent', extra={'instance_id': report.instance_id})
        return True
    if response.status_code == 401:
        logger.warning('usage_report_failed: key not valid', extra={'url': url})
    else:
        logger.warning(
            'usage_report_failed',
            extra={
                'status': response.status_code,
                'body': response.text[:1000],
                'url': url,
            },
        )
    return False


def sandbox_strategy(
    injector: SandboxServiceInjector | None,
) -> SandboxStrategy | None:
    """The sandbox service in use, as Customer Center names it.

    ``config_from_env`` picks it from ``OH_SANDBOX_KIND``, else from
    ``RUNTIME`` (unset is Docker, ``local`` is the process sandbox).
    """
    for cls, strategy in _SANDBOX_STRATEGIES:
        if isinstance(injector, cls):
            return strategy
    return None


async def sso_enabled(session: AsyncSession) -> bool:
    """Whether users can sign in through a single sign-on identity provider.

    That is the Keycloak SAML provider (``ENABLE_ENTERPRISE_SSO``), or, with
    OAuth v2 login, an enabled login provider that is SAML or a direct
    OIDC/OAuth identity provider (Okta, Google, ...). Keycloak's own rows
    and git providers don't count.
    """
    if ENABLE_ENTERPRISE_SSO:
        return True
    if not is_oauth_v2_login_enabled():
        return False
    providers = await session.scalars(
        select(AuthProvider).where(
            AuthProvider.enabled.is_(True), AuthProvider.is_idp.is_(True)
        )
    )
    return any(_is_sso_provider(provider.details) for provider in providers)


def _is_sso_provider(details: AuthProviderDetails) -> bool:
    if isinstance(details, SamlProviderDetails):
        return True
    return (
        isinstance(details, OAuthProviderDetails)
        and details.provider_type == ProviderType.ENTERPRISE_SSO
        and details.legacy_keycloak_type is None
        and not (KEYCLOAK_CLIENT_ID and details.client_id == KEYCLOAK_CLIENT_ID)
    )


async def personal_workspaces_enabled() -> bool:
    """Whether the web client shows users their personal workspaces."""
    web_client = await get_global_config().web_client.get_web_client_config()
    return not web_client.feature_flags.hide_personal_workspaces
