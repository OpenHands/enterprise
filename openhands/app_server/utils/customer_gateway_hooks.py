"""Thin core-side hooks into the SaaS-only opt-in customer gateway.

``server.customer_gateway`` is not importable in OSS builds; then every hook is the
identity. In SaaS builds each hook is still the identity unless the deployment switch
is on and the sandbox's credential org has an enabled gateway."""

from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

if TYPE_CHECKING:
    from openhands.sdk import LLM

T = TypeVar('T')

_UNSET: Any = object()
_service_module: Any = _UNSET


def _svc() -> Any:
    """Imported lazily: the service imports app_server config, which imports callers of this module."""
    global _service_module
    if _service_module is _UNSET:
        try:
            from server.customer_gateway import service as loaded
        except ImportError:  # OSS build
            loaded = None  # type: ignore[assignment]
        _service_module = loaded
    return _service_module


async def bind_start_request(
    request: T,
    sandbox: Any,
    trigger: str | None = None,
    *,
    default_org_id: UUID | None = None,
) -> T:
    svc = _svc()
    return (
        request
        if svc is None
        else await svc.bind_start_request(
            request, sandbox, trigger, default_org_id=default_org_id
        )
    )


async def select_launch_default_llm(
    llm: 'LLM', credential_org_id: UUID | None, user_id: str | None, trigger: str | None
) -> 'LLM | None':
    """The org's gateway default alias, or None when none applies. Raises a
    user-facing 409 when the member's key does not grant it."""
    svc = _svc()
    if svc is None:
        return None
    from server.customer_gateway.launch_default import (
        select_launch_default_llm as select,
    )

    try:
        return await select(llm, credential_org_id, user_id, trigger)
    except svc.CustomerGatewayLaunchError as exc:
        from fastapi import HTTPException, status

        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc


async def bind_profile_llm(llm: T, sandbox: Any) -> T:
    svc = _svc()
    return llm if svc is None else await svc.bind_profile_llm(llm, sandbox)


async def sandbox_environment(
    credential_org_id: UUID | None, environment: dict[str, str]
) -> dict[str, str]:
    svc = _svc()
    if svc is None:
        return environment
    return await svc.sandbox_env_overrides(credential_org_id, environment)


def deployment_switch_on() -> bool:
    """Cheap, no I/O: lets callers skip org lookups entirely when the switch is off."""
    svc = _svc()
    return svc is not None and svc.get_settings() is not None


async def gateway_enabled_for_org(credential_org_id: UUID | None) -> bool:
    svc = _svc()
    if svc is None or credential_org_id is None or svc.get_settings() is None:
        return False
    return (
        await svc.CustomerGatewayStore.get_enabled_org_gateway(credential_org_id)
        is not None
    )


def is_gateway_error(exc: BaseException) -> bool:
    svc = _svc()
    return svc is not None and isinstance(
        exc, (svc.CustomerGatewayLaunchError, svc.CustomerGatewayDenied)
    )


async def assert_resume_allowed(
    credential_org_id: UUID | None, created_at: Any
) -> None:
    """Raises a user-facing 409 (not a 500) for a sandbox that predates the gateway."""
    svc = _svc()
    if svc is None:
        return
    try:
        await svc.assert_resume_allowed(credential_org_id, created_at)
    except svc.CustomerGatewayLaunchError as exc:
        from fastapi import HTTPException, status

        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc


async def sanitize_sandbox_user_export(
    payload: dict[str, Any], session_api_key: str | None
) -> dict[str, Any]:
    """Call after the route has validated the session key and its ownership. When the
    deployment switch is off this returns before any sandbox or database lookup."""
    svc = _svc()
    if svc is None or svc.get_settings() is None:
        return payload
    from openhands.app_server.sandbox.session_auth import validate_session_key

    sandbox = await validate_session_key(session_api_key)
    return await svc.sanitize_sandbox_user_export(payload, sandbox)
