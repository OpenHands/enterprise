"""Native seams for the opt-in customer gateway: relay authorization, sandbox-bound token
minting, launch-time LLM binding and sandbox refresh environment.

Every entry point returns immediately when the deployment switch is OFF, and is a no-op
for orgs without an enabled gateway, so managed and BYOK launches are unchanged."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import SecretStr
from starlette.requests import Request

from openhands.app_server.config import get_global_config, get_sandbox_service
from openhands.app_server.sandbox.sandbox_models import SandboxInfo, SandboxStatus
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.specifiy_user_context import ADMIN, USER_CONTEXT_ATTR
from openhands.sdk import LLM, Agent
from server.customer_gateway.config import CustomerGatewaySettings, get_settings
from server.customer_gateway.profile import GatewayProfile, parse_profile
from server.customer_gateway.tokens import (
    InferencePrincipal,
    InvalidInferenceToken,
    mint_inference_token,
    verify_inference_token,
)
from storage.customer_gateway_store import (
    CustomerGatewayDenied,
    CustomerGatewayStore,
    MemberResolution,
    OrgGateway,
)

REFRESH_URL_ENV = 'OH_LLM_API_KEY_REFRESH_URL'
REFRESH_HEADERS_ENV = 'OH_LLM_API_KEY_REFRESH_HEADERS'
REFRESH_BASE_URLS_ENV = 'OH_LLM_API_KEY_REFRESH_BASE_URLS'


class CustomerGatewayLaunchError(Exception):
    """Launch refused while the org's customer gateway is ON (fail closed, no fallback)."""


def _jwt():
    injector = get_global_config().jwt
    assert injector is not None
    return injector.get_jwt_service()


def _profile(gw: OrgGateway, settings: CustomerGatewaySettings) -> GatewayProfile:
    return parse_profile(gw.profile, allow_http=settings.allow_http)


async def get_sandbox(sandbox_id: str) -> SandboxInfo | None:
    state = InjectorState()
    setattr(
        state, USER_CONTEXT_ATTR, ADMIN
    )  # same pattern as session_auth.validate_session_key
    async with get_sandbox_service(state) as sandbox_service:
        return await sandbox_service.get_sandbox(sandbox_id)


async def org_gateway_for_sandbox(sandbox: SandboxInfo) -> OrgGateway | None:
    if get_settings() is None or sandbox.credential_org_id is None:
        return None
    return await CustomerGatewayStore.get_enabled_org_gateway(sandbox.credential_org_id)


async def mint_for_sandbox(sandbox: SandboxInfo, gw: OrgGateway) -> str:
    """Owner/org come only from the persisted sandbox record, never from the caller."""
    settings = get_settings()
    assert settings is not None
    if (
        sandbox.status != SandboxStatus.RUNNING
        and sandbox.status != SandboxStatus.STARTING
    ):
        raise CustomerGatewayDenied('sandbox_inactive')
    if not sandbox.created_by_user_id:
        raise CustomerGatewayDenied('sandbox_unowned')
    user_id = UUID(sandbox.created_by_user_id)
    pv = await CustomerGatewayStore.current_policy_version(gw.org_id, user_id)
    return mint_inference_token(
        _jwt(),
        user_id=str(user_id),
        org_id=str(gw.org_id),
        sandbox_id=sandbox.id,
        gateway_id=gw.gateway_id,
        policy_version=pv,
        ttl=settings.token_ttl,
    )


async def authorize_inference(
    request: Request,
) -> tuple[InferencePrincipal, GatewayProfile, MemberResolution]:
    settings = get_settings()
    if settings is None:
        raise CustomerGatewayDenied('relay_disabled')
    auth = request.headers.get('authorization', '')
    token = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
    if token in ('', UNSUPPORTED_EXPORT_PLACEHOLDER):
        raise InvalidInferenceToken('missing bearer')
    p = verify_inference_token(_jwt(), token)
    try:
        org_id, user_id = UUID(p.org_id), UUID(p.user_id)
    except ValueError as exc:
        raise InvalidInferenceToken('claim format') from exc
    gw = await CustomerGatewayStore.get_enabled_org_gateway(org_id)
    if gw is None or gw.gateway_id != p.gateway_id:
        raise CustomerGatewayDenied('gateway_disabled')
    sandbox = await get_sandbox(p.session_id)
    if (
        sandbox is None
        or sandbox.status != SandboxStatus.RUNNING
        or sandbox.created_by_user_id != p.user_id
        or sandbox.credential_org_id != org_id
    ):
        raise CustomerGatewayDenied('session_inactive')
    res = await CustomerGatewayStore.resolve(org_id, user_id, p.policy_version)
    return p, _profile(gw, settings), res


def catalog_alias(profile: GatewayProfile, model: str) -> str | None:
    if model in profile.models:
        return model
    tail = model.split('/', 1)[-1]
    return tail if tail in profile.models else None


def bind_llm(llm: LLM, profile: GatewayProfile, relay_url: str, token: str) -> LLM:
    # The relay serves Chat Completions only; Responses-mode LLMs are refused, not re-routed.
    if llm.api_mode == 'responses':
        raise CustomerGatewayLaunchError(
            'Responses API LLMs are not supported through the customer gateway'
        )
    alias = catalog_alias(profile, llm.model)
    if alias is None:
        raise CustomerGatewayLaunchError(
            f'model {llm.model!r} is not in the customer gateway catalog'
        )
    return llm.model_copy(
        update={
            'model': f'openai/{alias}',
            'base_url': relay_url,
            'api_key': SecretStr(token),
            'litellm_extra_body': {},
            # No SDK retries: a retried 429/5xx/timeout can be billed again at the customer.
            'num_retries': 0,
            'api_mode': 'chat',
            'provider_connection_id': None,
        }
    )


# Launch sources whose sandbox owner is an already-resolved native OpenHands user/org
# acting under their own membership and key, not a borrowed or ambient identity:
#
# - None/gui/suggested_task/openhands_api: the human who started the sandbox.
# - automation (ConversationTrigger.AUTOMATION): webhook_router resolves and runs the
#   conversation "in the context of the owner of the sandbox" (see its own docstring)
#   before this hook ever runs; mint_for_sandbox already keys exclusively off
#   `sandbox.created_by_user_id`, so this is the same owner binding, not a new one.
# - resolver (ConversationTrigger.RESOLVER): shared by every *_view.py resolver
#   integration (GitHub, GitLab, Bitbucket, Bitbucket DC, Azure DevOps, Slack, Jira,
#   Jira DC) — not GitLab-specific. Each resolves its own platform actor to a native
#   `keycloak_user_id` (e.g. gitlab_view.py's `token_manager.get_user_id_from_idp_user_id`)
#   and the target org via `integrations/resolver_org_router.py:resolve_org_for_repo`
#   before a sandbox is ever created. An unmapped actor or an unclaimed repo yields
#   `None`/`OrgNotUsableError` there — resolver_org_router's own docstring is explicit
#   that callers "should abort starting a conversation rather than falling back to a
#   personal workspace" for a suspended org; it does not itself force gateway-enabled
#   orgs to refuse an unclaimed-repo personal-workspace conversation. This hook does
#   not change or re-check that routing: `org_gateway_for_sandbox` keys strictly off
#   the org the sandbox was actually created under (`sandbox.credential_org_id`), so
#   it enforces whichever org resolution picked — it cannot pick up a different org's
#   gateway config, but it also cannot force a mis-routed personal-workspace fallback
#   back onto the intended org's catalog/budget. That routing correctness is
#   resolver_org_router's contract, not this allowlist's.
#
# Everything else (chat/issue integrations without a resolved native actor above, and
# microagent management) is refused while ON. Service accounts are a distinct,
# still-unimplemented policy, enforced independently of this allowlist:
# `storage/customer_gateway_store.py:CustomerGatewayStore._load` explicitly checks
# `account_type != ACCOUNT_TYPE_HUMAN` and raises `CustomerGatewayDenied('service_account')`
# even when a credential row and active membership both exist for that user_id — it is
# not merely "no such member row".
INTERACTIVE_TRIGGERS = frozenset(
    {None, 'gui', 'suggested_task', 'openhands_api', 'automation', 'resolver'}
)


def _assert_supported_runtime() -> None:
    """Only backends whose start path applies sandbox_env_overrides; a warm-pool claim
    (k8s agent sandbox) would keep the managed refresh env, so it is refused."""
    from openhands.app_server.sandbox.docker_sandbox_service import (
        DockerSandboxServiceInjector,
    )
    from openhands.app_server.sandbox.remote_sandbox_service import (
        RemoteSandboxServiceInjector,
    )

    injector = get_global_config().sandbox
    if not isinstance(
        injector, (RemoteSandboxServiceInjector, DockerSandboxServiceInjector)
    ):
        raise CustomerGatewayLaunchError(
            f'sandbox backend {type(injector).__name__} is not supported with the customer gateway'
        )


def _iter_llm_dicts(node: Any):
    if isinstance(node, dict):
        if 'model' in node and 'usage_id' in node:
            yield node
        for v in node.values():
            yield from _iter_llm_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_llm_dicts(v)


def assert_all_llms_bound(obj: Any, relay_url: str) -> int:
    """Invariant over the serialized payload, not an enumeration of known fields."""
    dumped = obj.model_dump(mode='json', context={'expose_secrets': True})
    count = 0
    for llm in _iter_llm_dicts(dumped):
        if llm.get('base_url') != relay_url or not llm.get('api_key'):
            raise CustomerGatewayLaunchError(
                f'unbound LLM {llm.get("usage_id")!r} would bypass the customer gateway'
            )
        try:
            verify_inference_token(_jwt(), llm['api_key'])
        except InvalidInferenceToken as exc:
            raise CustomerGatewayLaunchError(
                'LLM carries a non-inference credential'
            ) from exc
        count += 1
    return count


async def bind_start_request(
    request: Any, sandbox: SandboxInfo, trigger: str | None = None
) -> Any:
    """Rebind every LLM carried by a StartConversationRequest, or refuse the launch."""
    gw = await org_gateway_for_sandbox(sandbox)
    if gw is None:
        return request
    settings = get_settings()
    assert settings is not None
    if trigger not in INTERACTIVE_TRIGGERS:
        raise CustomerGatewayLaunchError(
            f'{trigger} launches are not supported while the customer gateway is on'
        )
    _assert_supported_runtime()
    _assert_born_after_enable(gw, sandbox.created_at)
    agent = request.agent
    if not isinstance(agent, Agent):
        raise CustomerGatewayLaunchError(
            'only OpenHands agents can run while the customer gateway is on'
        )
    try:
        token = await mint_for_sandbox(sandbox, gw)
    except CustomerGatewayDenied as exc:
        raise CustomerGatewayLaunchError(
            f'customer gateway access denied: {exc.reason}'
        ) from exc
    profile = _profile(gw, settings)
    updates: dict[str, Any] = {
        'llm': bind_llm(agent.llm, profile, settings.relay_url, token)
    }
    condenser = getattr(agent, 'condenser', None)
    if condenser is not None and getattr(condenser, 'llm', None) is not None:
        updates['condenser'] = condenser.model_copy(
            update={'llm': bind_llm(condenser.llm, profile, settings.relay_url, token)}
        )
    definitions = []
    for d in getattr(request, 'agent_definitions', None) or []:
        # A definition model names a profile; only the default (seeded, bound) store is allowed.
        if d.model and d.model != 'inherit' and d.profile_store_dir is not None:
            raise CustomerGatewayLaunchError(
                f'agent definition {d.name!r} uses a custom profile store'
            )
        if d.condenser is not None and getattr(d.condenser, 'llm', None) is not None:
            d = d.model_copy(
                update={
                    'condenser': d.condenser.model_copy(
                        update={
                            'llm': bind_llm(
                                d.condenser.llm, profile, settings.relay_url, token
                            )
                        }
                    )
                }
            )
        definitions.append(d)
    request = request.model_copy(update={'agent': agent.model_copy(update=updates)})
    if definitions:
        request = request.model_copy(update={'agent_definitions': definitions})
    if assert_all_llms_bound(request, settings.relay_url) == 0:
        raise CustomerGatewayLaunchError('no LLM found to bind')
    return request


async def bind_profile_llm(llm: LLM, sandbox: SandboxInfo) -> LLM:
    """For profiles seeded into the sandbox (model switching, title, sub-agents)."""
    gw = await org_gateway_for_sandbox(sandbox)
    if gw is None:
        return llm
    settings = get_settings()
    assert settings is not None
    _assert_supported_runtime()
    _assert_born_after_enable(gw, sandbox.created_at)
    token = await mint_for_sandbox(sandbox, gw)
    bound = bind_llm(llm, _profile(gw, settings), settings.relay_url, token)
    assert_all_llms_bound(bound, settings.relay_url)
    return bound


def withhold_llm_keys(payload: Any) -> int:
    """Null api_key on every LLM-shaped object in a JSON export (in place)."""
    count = 0
    for llm in _iter_llm_dicts(payload):
        if llm.get('api_key') is not None:
            llm['api_key'] = None
            count += 1
    return count


# Not a credential: lets a standalone SDK client reach the relay (litellm refuses to send
# a request without some api_key) so it receives the explicit missing-token error.
UNSUPPORTED_EXPORT_PLACEHOLDER = 'customer-gateway-no-standalone-llm'
UNSUPPORTED_EXPORT_DETAIL = (
    'This organization routes inference through its customer gateway. LLM settings '
    'exported to a sandbox carry no credential; standalone SDK use such as '
    'CloudWorkspace.get_llm() is unsupported. Use the LLM bound to the conversation.'
)


EXPORT_MODEL = 'openai/customer-gateway-unsupported'


def _iter_exported_llm_dicts(node: Any):
    # Wider than _iter_llm_dicts: saved profiles need not carry usage_id.
    if isinstance(node, dict):
        if 'model' in node and ({'api_key', 'base_url', 'usage_id'} & node.keys()):
            yield node
        for v in list(node.values()):
            yield from _iter_exported_llm_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_exported_llm_dicts(v)


def _org_uuid(value: Any) -> UUID | None:
    if value in (None, ''):
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


async def sanitize_sandbox_user_export(
    payload: dict[str, Any], sandbox: SandboxInfo
) -> dict[str, Any]:
    """Sandbox-facing user export (``/api/v1/users/me?expose_secrets=true``) while ON.

    No stored LLM key, base URL or provider choice leaves the app server. Every LLM-shaped
    object and the flat SDK compatibility fields are replaced by one Chat Completions LLM
    pointing at the relay with a non-secret placeholder bearer, so a standalone SDK client
    (``CloudWorkspace.get_llm``) gets the relay's explicit ``missing_inference_token``
    error and cannot reach the old provider. MCP config and other fields are unchanged.

    The export is scoped by both the payload org and the sandbox credential org. If either
    has an enabled gateway they must be the same org: a sandbox from another (e.g. OFF) org
    of the same user cannot be used to read an ON org's user info, and vice versa.
    """
    settings = get_settings()
    if settings is None:
        return payload
    sandbox_org = sandbox.credential_org_id
    payload_org = _org_uuid(payload.get('org_id'))
    orgs = {o for o in (sandbox_org, payload_org) if o is not None}
    enabled = [o for o in orgs if await CustomerGatewayStore.get_enabled_org_gateway(o)]
    if not enabled:
        return payload
    if payload_org is not None and payload_org != sandbox_org:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail='Customer gateway: this sandbox belongs to a different organization '
            'than the current one; secrets are not exported across organizations.',
        )
    for llm in list(_iter_exported_llm_dicts(payload)):
        usage_id = llm.get('usage_id')
        llm.clear()
        llm.update(
            model=EXPORT_MODEL,
            base_url=settings.relay_url,
            api_key=UNSUPPORTED_EXPORT_PLACEHOLDER,
            api_mode='chat',
        )
        if usage_id is not None:
            llm['usage_id'] = usage_id
    for key, value in (
        ('llm_model', EXPORT_MODEL),
        ('llm_base_url', settings.relay_url),
        ('llm_api_key', UNSUPPORTED_EXPORT_PLACEHOLDER),
    ):
        if key in payload:
            payload[key] = value
    payload['customer_gateway'] = {
        'llm_export': 'unsupported',
        'detail': UNSUPPORTED_EXPORT_DETAIL,
    }
    return payload


def _assert_born_after_enable(gw: OrgGateway, created_at: Any) -> None:
    """A sandbox created before the org's gateway was switched on carries the managed
    refresh env and possibly persisted platform credentials: never start, bind or resume
    a conversation in it while ON. Operators stop the org's running sandboxes on enabling."""
    if gw.enabled_since is None or created_at is None or created_at <= gw.enabled_since:
        raise CustomerGatewayLaunchError(
            'this sandbox predates the customer gateway; start a new conversation'
        )


async def assert_resume_allowed(
    credential_org_id: UUID | None, created_at: Any
) -> None:
    if get_settings() is None or credential_org_id is None:
        return
    gw = await CustomerGatewayStore.get_enabled_org_gateway(credential_org_id)
    if gw is not None:
        _assert_born_after_enable(gw, created_at)


async def sandbox_env_overrides(
    credential_org_id: UUID | None, environment: dict[str, str]
) -> dict[str, str]:
    """Point the stock agent-server refresh-on-401 hook at the inference-token endpoint,
    scoped to the relay base URL only. Managed/BYOK sandboxes keep their env byte-identical."""
    settings = get_settings()
    if settings is None or credential_org_id is None:
        return environment
    if await CustomerGatewayStore.get_enabled_org_gateway(credential_org_id) is None:
        return environment
    headers = environment.get(REFRESH_HEADERS_ENV)
    if not headers or 'X-Session-API-Key' not in json.loads(headers):
        raise CustomerGatewayLaunchError(
            'sandbox runtime has no session-key refresh header'
        )
    return {
        **environment,
        REFRESH_URL_ENV: settings.token_url,
        REFRESH_BASE_URLS_ENV: settings.relay_url,
    }
