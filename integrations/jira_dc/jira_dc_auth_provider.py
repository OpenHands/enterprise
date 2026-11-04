"""Jira DC identity and tokens from a ``jira_dc`` auth provider.

With OAuth v2 login on, a user who signed in with (or linked) a ``jira_dc``
auth provider already holds a verified Jira identity (``auth_provider_users``,
whose ``external_subject_id`` is the Jira user ``key`` given the preset's
``sub -> key`` attribute mapping) and an OAuth token (``oauth_tokens``). The
Jira DC integration reuses them so the user does not consent to Jira twice.

A provider only counts for a Jira host when its ``token_url`` is on that host,
so a token issued by one Jira server is never sent to another.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import UUID

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.constants import is_oauth_v2_login_enabled
from server.auth.oauth_v2_refresh import (
    get_permitted_drift_seconds,
    make_refresh_callback,
)
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import OAuthProviderDetails
from storage.auth_provider_store import AuthProviderStore
from storage.auth_provider_user_store import AuthProviderUserStore
from storage.oauth_token_store import OAuthTokenStore


@dataclass(frozen=True)
class JiraDcSecretHint:
    base_url: str
    secret_name: str


def _host(url: str | None) -> str | None:
    hostname = urlparse(url).hostname if url else None
    return hostname.lower() if hostname else None


def _jira_dc_details(provider: AuthProvider) -> OAuthProviderDetails | None:
    details = provider.details
    if (
        isinstance(details, OAuthProviderDetails)
        and details.provider_type == ProviderType.JIRA_DC
    ):
        return details
    return None


def _serves_host(provider: AuthProvider, host: str) -> bool:
    details = _jira_dc_details(provider)
    return details is not None and _host(details.token_url) == host.lower()


def _base_url(details: OAuthProviderDetails) -> str | None:
    # Jira DC endpoints live under ``<base>/rest/...``; ``<base>`` may carry a
    # context path (``https://host/jira``).
    url = details.token_url or details.userinfo_url
    if not url or '/rest/' not in url:
        return None
    return url.split('/rest/', 1)[0]


def _user_uuid(user_id: str) -> UUID | None:
    try:
        return UUID(user_id)
    except ValueError:
        return None


async def find_linked_jira_dc_provider(
    user_id: str, host: str | None
) -> AuthProvider | None:
    """The ``jira_dc`` provider for ``host`` the user holds a token for."""
    if not host or not is_oauth_v2_login_enabled():
        return None
    user_uuid = _user_uuid(user_id)
    if user_uuid is None:
        return None
    for provider in await AuthProviderStore().get_linked_oauth_providers(user_uuid):
        if _serves_host(provider, host):
            return provider
    return None


async def get_provider_access_token(user_id: str, provider: AuthProvider) -> str | None:
    """The user's access token for ``provider``, refreshed if due; ``None`` if
    it is missing or cannot be refreshed."""
    try:
        return await OAuthTokenStore(
            user_id=UUID(user_id), oauth_provider_id=provider.id
        ).get_valid_access_token(
            permitted_drift_seconds=get_permitted_drift_seconds(provider),
            refresh=make_refresh_callback(provider),
        )
    except Exception:
        logger.warning(
            '[Jira DC] Auth provider token unavailable',
            extra={'user_id': user_id, 'oauth_provider_id': provider.id},
            exc_info=True,
        )
        return None


async def get_provider_user_key(user_id: str, provider: AuthProvider) -> str | None:
    """The Jira user ``key`` the user is linked to on ``provider``."""
    link = await AuthProviderUserStore().get_by_user(UUID(user_id), provider.id)
    return link.external_subject_id if link else None


async def get_jira_dc_access_token(user_id: str, host: str | None) -> str | None:
    provider = await find_linked_jira_dc_provider(user_id, host)
    if provider is None:
        return None
    return await get_provider_access_token(user_id, provider)


async def find_user_id_by_jira_dc_key(jira_user_key: str, host: str) -> str | None:
    """The OpenHands user linked to Jira user ``jira_user_key`` on ``host``.

    ``None`` when nobody is, or when the key is linked to more than one user
    (it can't tell them apart, so it trusts neither).
    """
    if not host or not jira_user_key or not is_oauth_v2_login_enabled():
        return None
    links = await AuthProviderUserStore().list_links_by_subject(
        ProviderType.JIRA_DC.value, jira_user_key
    )
    user_ids = {
        str(user_id) for user_id, provider in links if _serves_host(provider, host)
    }
    if len(user_ids) > 1:
        logger.warning(
            '[Jira DC] Jira user key is linked to several users; not matching',
            extra={'host': host, 'user_count': len(user_ids)},
        )
        return None
    return user_ids.pop() if user_ids else None


async def get_jira_dc_secret_hint(user_id: str) -> JiraDcSecretHint | None:
    """Where the generic named-secret path exposes the user's Jira DC token
    (``SaasUserAuth.get_named_oauth_secrets``), so the agent can be told how
    to use it."""
    if not is_oauth_v2_login_enabled():
        return None
    user_uuid = _user_uuid(user_id)
    if user_uuid is None:
        return None
    for provider in await AuthProviderStore().get_linked_oauth_providers(user_uuid):
        details = _jira_dc_details(provider)
        if details is None or not details.named_secret:
            continue
        base_url = _base_url(details)
        if base_url:
            return JiraDcSecretHint(base_url=base_url, secret_name=details.named_secret)
    return None
