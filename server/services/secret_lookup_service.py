"""Short-lived lookup references for a member's git tokens and custom secrets.

The automations service fetches plugins from private repositories as the
identity an automation runs as. It asks for one reference per secret name
(``POST /api/service/users/{user_id}/orgs/{org_id}/secret-lookups``) and passes
the references to the SDK as ``LookupSecret``s. Nothing here returns a value:
a reference is a JWS that ``GET /api/v1/webhooks/secrets`` trades for the
current value when the plugin is fetched.

Names resolve the way a conversation of that principal in that org would see
them: a linked OAuth v2 provider's ``named_secret``, then ``<PROVIDER_TYPE>_TOKEN``
for a connected provider type without one, then the personal and org-shared
custom secrets of the org. A provider or OAuth secret wins a name collision,
as at conversation launch. The lowercase ``<provider>_token`` that the sandbox
secrets endpoint uses is not a launch name: a custom secret with that exact
name wins over it.

Only an active member gets references, and a reference resolves only while
its principal still is one (``is_active_org_member``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal
from uuid import UUID

from openhands.app_server.app_conversation.live_status_app_conversation_service import (
    secret_lookup_url,
)
from openhands.app_server.config import get_global_config
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.sandbox.docker_sandbox_service import (
    DockerSandboxServiceInjector,
)
from openhands.app_server.sandbox.remote_sandbox_service import (
    RemoteSandboxServiceInjector,
)
from openhands.app_server.services.jwt_service import JwtAudience, JwtService
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.auth_error import AuthError
from server.auth.saas_user_auth import SaasUserAuth
from storage.org_member_store import OrgMemberStore
from storage.user_store import UserStore

DEFAULT_TTL_SECONDS = 900
MAX_TTL_SECONDS = 3600
MAX_NAMES = 20

# Injected into a conversation by the app server; never handed out here.
_SYSTEM_SECRET_NAMES = frozenset({'OPENHANDS_API_KEY'})

SecretLookupKind = Literal['provider', 'oauth', 'custom']


@dataclass(frozen=True)
class SecretLookupTarget:
    """What one reference resolves to: exactly one of the three claims."""

    kind: SecretLookupKind
    claims: dict[str, str | int] = field(default_factory=dict)
    provider: str | None = None


def provider_type_for_secret_name(name: str) -> ProviderType | None:
    """The git provider type a ``<PROVIDER_TYPE>_TOKEN`` name stands for.

    The old lowercase ``<provider>_token`` sandbox secret name is accepted
    for backwards compatibility; a custom secret with that exact name wins
    over it (``resolve_secret_names``).
    """
    for provider_type in ProviderType:
        if provider_type is ProviderType.ENTERPRISE_SSO:
            continue
        if name in (
            _launch_name(provider_type),
            f'{provider_type.value}_token',
        ):
            return provider_type
    return None


def _launch_name(provider_type: ProviderType) -> str:
    """The name a conversation launch gives a provider's token."""
    return f'{provider_type.name}_TOKEN'


async def is_active_org_member(user_id: str, org_id: UUID) -> bool:
    """Whether ``user_id`` may get or redeem references for ``org_id``.

    The user exists, is neither disabled nor being deleted, and has an active
    membership in the org. There is no super-admin exception, and the rule
    does not depend on ``ENABLE_SUPER_ADMIN``.
    """
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        return False
    user = await UserStore.get_user_auth_state(str(user_uuid))
    if user is None or user.is_disabled or user.deletion_pending:
        return False
    member = await OrgMemberStore.get_org_member(org_id, user_uuid)
    # NULL is active; any other status, known or not, is not.
    return member is not None and member.status in (None, 'active')


def sandbox_secret_lookup_url() -> str | None:
    """The webhook secrets URL that sandboxes call, as conversations get it.

    Mirrors ``get_sandbox_web_url`` and ``secret_lookup_url`` for the
    configured sandbox service; ``None`` when sandboxes cannot reach it.
    """
    config = get_global_config()
    sandbox = config.sandbox
    web_url = config.sandbox_web_url or config.web_url
    if web_url is None and isinstance(sandbox, DockerSandboxServiceInjector):
        web_url = f'http://host.docker.internal:{sandbox.host_port}'
    return secret_lookup_url(
        web_url, remote_sandboxes=isinstance(sandbox, RemoteSandboxServiceInjector)
    )


async def user_auth_for_org(user_id: str, org_id: UUID) -> SaasUserAuth:
    """User auth that reads ``user_id``'s secrets as a member of ``org_id``.

    The org override is verified here, before any secret is read.
    """
    user_auth = await SaasUserAuth.get_for_user(user_id)
    assert isinstance(user_auth, SaasUserAuth)
    user_auth.set_effective_org_id_override(org_id)
    await user_auth.get_effective_org_id()
    return user_auth


async def resolve_secret_names(
    user_auth: SaasUserAuth, names: Sequence[str]
) -> tuple[dict[str, SecretLookupTarget], list[str]]:
    """Map each name to what it means for this principal, or list it as missing.

    Reads which providers are connected and which custom secrets exist; the
    values are never kept or returned.
    """
    targets: dict[str, SecretLookupTarget] = {}
    missing: list[str] = []

    named = await user_auth.get_named_oauth_secrets()
    oauth_ids = (
        {secret.secret_name: secret.oauth_provider_id for secret in named.secrets}
        if named
        else {}
    )
    linked_types = named.linked_provider_types if named else set()

    connected: set[ProviderType] | None = None
    custom_names: set[str] | None = None

    async def is_custom(name: str) -> bool:
        nonlocal custom_names
        if custom_names is None:
            custom_names = await _custom_secret_names(user_auth)
        return name in custom_names

    def custom(name: str) -> SecretLookupTarget:
        return SecretLookupTarget(kind='custom', claims={'secret_name': name})

    for name in names:
        if name in _SYSTEM_SECRET_NAMES:
            missing.append(name)
            continue
        oauth_provider_id = oauth_ids.get(name)
        if oauth_provider_id is not None:
            targets[name] = SecretLookupTarget(
                kind='oauth', claims={'oauth_provider_id': oauth_provider_id}
            )
            continue
        provider_type = provider_type_for_secret_name(name)
        # A conversation launches with custom secrets under their own names and
        # the provider token only as <PROVIDER_TYPE>_TOKEN, so a custom secret
        # wins over the lowercase alias.
        if (
            provider_type is not None
            and name != _launch_name(provider_type)
            and await is_custom(name)
        ):
            targets[name] = custom(name)
            continue
        if provider_type is not None and provider_type not in linked_types:
            if connected is None:
                connected = await _connected_provider_types(user_auth)
            if provider_type in connected:
                targets[name] = SecretLookupTarget(
                    kind='provider',
                    claims={'provider_type': provider_type.value},
                    provider=provider_type.value,
                )
                continue
        if await is_custom(name):
            targets[name] = custom(name)
            continue
        missing.append(name)
    return targets, missing


async def _connected_provider_types(user_auth: SaasUserAuth) -> set[ProviderType]:
    try:
        provider_tokens = await user_auth.get_provider_tokens()
    except AuthError:
        # The provider's refresh token no longer works: its names are missing
        # until the user reconnects, as a conversation would find them.
        logger.warning(
            'secret_lookups:provider_tokens_unavailable',
            extra={'user_id': user_auth.user_id},
        )
        return set()
    return {
        provider_type
        for provider_type, provider_token in (provider_tokens or {}).items()
        if provider_token.token
    }


async def _custom_secret_names(user_auth: SaasUserAuth) -> set[str]:
    secrets_store = await user_auth.get_secrets_store()
    secrets = await secrets_store.load()
    if secrets is None:
        return set()
    return set(secrets.custom_secrets) - _SYSTEM_SECRET_NAMES


def sign_secret_lookup(
    jwt_service: JwtService,
    *,
    user_id: str,
    org_id: UUID,
    target: SecretLookupTarget,
    ttl_seconds: int,
) -> str:
    """A JWS that resolves exactly one secret of ``user_id`` in ``org_id``."""
    return jwt_service.create_jws_token(
        payload={'user_id': user_id, 'org_id': str(org_id), **target.claims},
        expires_in=timedelta(seconds=ttl_seconds),
        audience=JwtAudience.SECRET_LOOKUP,
    )
