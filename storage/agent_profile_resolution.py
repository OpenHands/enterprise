"""Shared cloud adapters for resolving Agent Profiles against an org row.

The thin glue (#15044) that lets the SDK profile substrate
(``resolve_agent_profile`` / ``find_referrers`` / ``cascade_rename``) operate
over the SaaS storage model. Everything domain-level is imported from
``openhands.sdk.profiles``; this module only adapts the ``org.agent_profiles`` /
``org_member.agent_profiles`` / ``org.llm_profiles`` columns to the SDK's
store/loader/mutator Protocols.

Used by the ``/api/agent-profiles`` router, the LLM-profile FK guard in
``org_profiles``, ``SaasSettingsStore.load``, and the
``/api/v1/users/me`` profile overlay.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import SecretStr, ValidationError

from openhands.app_server.settings.agent_profiles import AgentProfiles
from openhands.app_server.settings.llm_profiles import LLMProfiles, resolve_profile_llm
from openhands.app_server.settings.meta_profiles import OrgMetaProfiles
from openhands.app_server.settings.provider_connections import (
    ProviderConnectionNotFoundError,
    ProviderConnections,
)
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.sdk.launch import AgentLaunchError, LaunchStores
from openhands.sdk.llm.utils.openhands_provider import (
    canonicalize_openhands_llm_payload,
)
from openhands.sdk.mcp.config import MCPServer
from server.constants import LITE_LLM_API_URL, canonicalize_bundled_proxy_llm
from storage.mcp_config import coerce_persisted_mcp_config

if TYPE_CHECKING:
    from openhands.sdk.llm import LLM
    from openhands.sdk.profiles import AgentProfile
    from openhands.sdk.utils.cipher import Cipher
    from storage.org import Org
    from storage.org_member import OrgMember


def load_agent_profiles(org: Org) -> AgentProfiles:
    """Load ``AgentProfiles`` from the org row, defaulting to empty if unset.

    Degrades to an empty collection on schema drift rather than 500-ing — the
    same contract as ``org_profiles._load_profiles`` for ``llm_profiles``.
    """
    if org.agent_profiles is None:
        return AgentProfiles()
    try:
        return AgentProfiles.model_validate(org.agent_profiles)
    except ValidationError as exc:
        logger.warning(
            'Failed to load org agent profiles for %s: %s', org.id, type(exc).__name__
        )
        return AgentProfiles()


AgentProfileScope = Literal['personal', 'org']


def load_member_agent_profiles(member: OrgMember) -> AgentProfiles:
    """Load the member's personal ``AgentProfiles``, defaulting to empty if unset.

    Same degrade-on-drift contract as :func:`load_agent_profiles`.
    """
    if member.agent_profiles is None:
        return AgentProfiles()
    try:
        return AgentProfiles.model_validate(member.agent_profiles)
    except ValidationError as exc:
        logger.warning(
            'Failed to load personal agent profiles for member %s in org %s: %s',
            member.user_id,
            member.org_id,
            type(exc).__name__,
        )
        return AgentProfiles()


def locate_agent_profile(
    org: Org, member: OrgMember, profile_id: str
) -> tuple[AgentProfile, AgentProfileScope] | None:
    """Find ``profile_id`` in the member's collection, then the org's.

    Ids are UUIDs minted per save, so the order is only a tiebreak; it lets a
    member pointer or a launch override reference either scope. Returns the
    profile together with the scope it was found in, or ``None``.
    """
    personal = load_member_agent_profiles(member).profiles.get(profile_id)
    if personal is not None:
        return personal, 'personal'
    shared = load_agent_profiles(org).profiles.get(profile_id)
    if shared is not None:
        return shared, 'org'
    return None


def load_llm_profiles(org: Org) -> LLMProfiles:
    """Load ``LLMProfiles`` from the org row, defaulting to empty if unset."""
    if org.llm_profiles is None:
        return LLMProfiles()
    try:
        return LLMProfiles.model_validate(org.llm_profiles)
    except ValidationError as exc:
        logger.warning('Failed to load org LLM profiles for %s: %s', org.id, exc)
        return LLMProfiles()


def load_provider_connections(org: Org) -> ProviderConnections:
    """Load ``ProviderConnections`` from the org row, defaulting to empty if unset.

    Degrades to empty on schema drift rather than raising, matching
    :func:`load_llm_profiles`.
    """
    if org.provider_connections is None:
        return ProviderConnections()
    try:
        return ProviderConnections.model_validate(org.provider_connections)
    except Exception as exc:  # noqa: BLE001 - parity with load_llm_profiles
        logger.warning(
            'Failed to load org provider connections for %s: %s', org.id, exc
        )
        return ProviderConnections()


def load_meta_profiles(org: Org) -> OrgMetaProfiles:
    """Load the org's Model Routers from the org row, defaulting to empty if unset.

    Degrades to empty on schema drift rather than raising, matching
    :func:`load_provider_connections`.
    """
    if org.meta_profiles is None:
        return OrgMetaProfiles()
    try:
        return OrgMetaProfiles.model_validate(org.meta_profiles)
    except ValidationError as exc:
        logger.warning('Failed to load org Model Routers for %s: %s', org.id, exc)
        return OrgMetaProfiles()


def apply_provider_connection(org: Org, llm_payload: dict[str, Any]) -> dict[str, Any]:
    """Overlay a linked provider connection's credentials onto an LLM payload.

    Read-at-use resolution for the settings load path: when ``llm_payload``
    carries a ``provider_connection_id``, the connection's ``api_key`` and
    ``base_url`` win over whatever the composed settings hold, so every member
    launching with a linked LLM (the org default or an activated profile) uses
    the shared credential, and rotating the connection applies on the next
    load. A dangling id drops the composed key, so it is not sent to a
    provider host the linked LLM never chose; the load still works. The rule
    is ``ProviderConnections.apply_to_payload``.
    """
    return load_provider_connections(org).apply_to_payload(llm_payload)


def resolve_org_profile_llm(
    connections: ProviderConnections,
    llm: LLM,
    *,
    fallback_api_key: SecretStr | None,
    strict: bool = False,
) -> LLM:
    """Turn a saved org LLM profile into the LLM a run launches with.

    ``resolve_profile_llm`` with the org's ``connections`` (the shared rule:
    provider-default ``base_url``, fallback key for unlinked keyless profiles,
    a linked connection's key and ``base_url``), then the canonical model name
    for bundled-proxy models. ``strict`` raises ``AgentLaunchError`` for a
    missing connection; otherwise the profile resolves with no key. Shared by
    agent-profile launch (``OrgLLMProfileLoader``) and the
    ``/api/v1/users/me`` overlay.
    """
    try:
        resolved = resolve_profile_llm(
            llm,
            managed_proxy_url=LITE_LLM_API_URL,
            fallback_api_key=fallback_api_key,
            connections=connections,
            strict=strict,
        )
    except ProviderConnectionNotFoundError as exc:
        raise AgentLaunchError(
            'The LLM profile references a missing provider connection'
        ) from exc
    # Canonicalize after the connection is applied: it only renames the model
    # and drops a base_url that the SDK fills back with the same proxy, so the
    # connection's host and key still win.
    payload = canonicalize_bundled_proxy_llm(
        canonicalize_openhands_llm_payload(
            resolved.model_dump(mode='json', context={'expose_secrets': True})
        )
    )
    return type(resolved).model_validate(payload)


class OrgLLMProfileLoader:
    """``LLMProfileLoader`` over an org's LLM profiles — the resolver's ``llm_store``.

    The ``org.llm_profiles`` ``EncryptedJSON`` column decrypts at the column
    boundary, so the wrapped ``LLMProfiles`` already holds cleartext keys; the
    ``cipher`` arg is accepted for Protocol parity and ignored.
    """

    def __init__(
        self,
        profiles: LLMProfiles,
        *,
        org: Org | None = None,
        fallback_api_key: SecretStr | None = None,
    ) -> None:
        self._profiles = profiles
        self._org = org
        self._fallback_api_key = fallback_api_key

    def load(self, name: str, *, cipher: Cipher | None = None) -> LLM:
        llm = self._profiles.get(name)
        if llm is None:
            # The resolver maps this to ProfileNotFound (HTTP 4xx).
            raise FileNotFoundError(f'LLM profile {name!r} not found')
        if self._org is not None:
            llm = resolve_org_profile_llm(
                load_provider_connections(self._org),
                llm,
                fallback_api_key=self._fallback_api_key,
                strict=True,
            )
        return llm


def org_launch_stores(
    org: Org,
    mcp_config: dict[str, MCPServer],
    *,
    llm_profiles: LLMProfiles | None = None,
    fallback_api_key: SecretStr | None = None,
) -> LaunchStores:
    """Adapt organization catalogs to the SDK launch resolver."""
    profiles = llm_profiles if llm_profiles is not None else load_llm_profiles(org)
    return LaunchStores(
        llm_profiles=OrgLLMProfileLoader(
            profiles, org=org, fallback_api_key=fallback_api_key
        ),
        llm_profile_names=lambda: list(profiles.profiles),
        mcp_config=mcp_config,
        skills=lambda: [],
    )


class OrgLLMProfileMutator:
    """``LLMProfileMutator`` over an org's LLM profiles — drives the FK delete/rename.

    Mutates the in-memory ``LLMProfiles`` container; the caller persists it back
    onto the org row under the same locked transaction.
    """

    def __init__(self, profiles: LLMProfiles) -> None:
        self._profiles = profiles

    def delete(self, name: str) -> None:
        self._profiles.delete(name)

    def rename(self, old_name: str, new_name: str) -> None:
        self._profiles.rename(old_name, new_name)


def member_mcp_config(member: OrgMember) -> dict[str, MCPServer]:
    """Return the acting member's configured MCP servers."""
    raw = member.effective_mcp_config
    if not raw:
        return {}
    try:
        return coerce_persisted_mcp_config(raw)
    except Exception as exc:
        # Catch broadly, not just ValidationError: MCP normalization can also
        # raise on fastmcp contract drift, and a malformed member config must
        # resolve to "no servers" rather than 500 the materialize endpoint.
        logger.warning('Failed to parse member MCP config for resolve: %s', exc)
        return {}
