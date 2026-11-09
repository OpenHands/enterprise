"""The usage report this install sends Customer Center.

The worker's ``usage_report`` job (``openhands/app_server/worker/scheduled_jobs.py``)
runs ``run()`` every 30 minutes while ``OPENHANDS_LICENSE_KEY`` is set and no
offline license is configured, whether or not license enforcement is on. Each report carries lifetime totals, not
changes since the last report, so a missed or duplicate report loses nothing.
A failed report is logged and not retried; the next run is the retry.
"""

from __future__ import annotations

import os
from typing import Callable, Literal

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.config import get_global_config
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
from openhands.app_server.utils.http_session import httpx_verify_option
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.constants import (
    AUTOMATION_SERVICE_URL,
    ENABLE_ENTERPRISE_SSO,
    is_oauth_v2_login_enabled,
)
from server.licensing.config import LicenseConfig
from server.licensing.limits import count_orgs
from server.licensing.sso import is_sso_provider
from storage.auth_provider import AuthProvider
from storage.database import a_session_maker
from storage.instance_settings_store import InstanceSettingsStore
from storage.stored_conversation_metadata import StoredConversationMetadata
from storage.user import User

USAGE_REPORTS_PATH = '/api/v1/usage-reports'
# The automation service's instance-wide totals, under AUTOMATION_SERVICE_URL.
AUTOMATION_USAGE_PATH = '/v1/usage'
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
    # None when the automation service's count can't be read.
    automations: int | None = Field(ge=0)
    orgs: int = Field(ge=0)
    sso_enabled: bool
    personal_workspaces_enabled: bool
    sandbox_strategy: SandboxStrategy


class AutomationUsage(BaseModel):
    """The body of the automation service's ``GET /v1/usage``."""

    automations: int = Field(ge=0)


async def run(
    http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> None:
    """Collect and send one report.

    Does nothing without a license key, or with an offline license.
    """
    config = LicenseConfig.from_env()
    if config.license_key is None or config.offline:
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
        orgs = await count_orgs(session)
        sso = await sso_enabled(session)
    return UsageReport(
        license_key=license_key,
        instance_id=str(instance_id),
        conversations=conversations or 0,
        users=users or 0,
        automations=await automation_count(),
        orgs=orgs,
        sso_enabled=sso,
        personal_workspaces_enabled=await personal_workspaces_enabled(),
        sandbox_strategy=strategy,
    )


async def automation_count() -> int | None:
    """The number of automations on this install, or None if it can't be read.

    Automations live in the automation service's own database, which counts
    them across every org at ``GET /v1/usage``. The request carries the
    service key that service uses to call this server, so it needs no user.
    None without a configured service or key, and on any failure: the report
    goes out without the count.
    """
    service_key = os.getenv('AUTOMATIONS_SERVICE_KEY', '').strip()
    if not AUTOMATION_SERVICE_URL or not service_key:
        return None
    url = f'{AUTOMATION_SERVICE_URL.rstrip("/")}{AUTOMATION_USAGE_PATH}'
    try:
        async with httpx.AsyncClient(
            verify=httpx_verify_option(), timeout=REQUEST_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(url, headers={'X-Service-API-Key': service_key})
            response.raise_for_status()
            return AutomationUsage.model_validate(response.json()).automations
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(
            'usage_report_automation_count_failed',
            extra={'error': f'{type(e).__name__}: {e}', 'url': url},
        )
        return None


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
    return any(is_sso_provider(provider.details) for provider in providers)


async def personal_workspaces_enabled() -> bool:
    """Whether the web client shows users their personal workspaces."""
    web_client = await get_global_config().web_client.get_web_client_config()
    return not web_client.feature_flags.hide_personal_workspaces
