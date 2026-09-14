"""Policy for the optional external LiteLLM gateway, independent of the SDK."""

import os
from typing import TypedDict
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.llm import LLM
from openhands.sdk.settings import (
    AgentSettingsConfig,
    CondenserSettingsConfig,
    ConversationSettings,
)

_GATEWAY_URLS = TypeAdapter(list[str])
_JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(
    JsonValue, config=ConfigDict(hide_input_in_errors=True)
)


class _LLMTransport(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    model: str | None = None
    base_url: str | None = None


class DirectLLMDefaults(TypedDict):
    model: str
    base_url: str | None
    api_key: str | None


class LiteLLMIntegrationDisabled(ValueError):
    """A configuration requires the disabled managed gateway."""


def is_litellm_enabled() -> bool:
    return os.getenv('ENABLE_LITELLM', 'true').lower() in ('true', '1')


def require_litellm_enabled() -> None:
    if not is_litellm_enabled():
        raise LiteLLMIntegrationDisabled('The LiteLLM integration is disabled.')


def _origin(url: str | None) -> tuple[str, str, int | None] | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url.strip())
        if not parsed.hostname:
            return None
        scheme = parsed.scheme.lower()
        port = parsed.port or {'http': 80, 'https': 443}.get(scheme)
        return scheme, parsed.hostname.lower().rstrip('.'), port
    except ValueError:
        return None


def is_managed_llm(model: str | None, base_url: str | None = None) -> bool:
    """Recognize managed transport prefixes and current or retired gateway origins.

    Paths do not change a gateway's identity (including legacy ``/v1`` URLs).
    Keep old origins in LITELLM_KNOWN_GATEWAY_URLS when disabling/removing a
    deployment's gateway credentials so persisted aliases cannot bypass policy.
    """
    if model and model.strip().lower().startswith(('openhands/', 'litellm_proxy/')):
        return True
    origin = _origin(base_url)
    if origin is None:
        return False
    known = [
        'https://llm-proxy.app.all-hands.dev',
        'https://llm-proxy.app.openhands.dev',
        'https://llm-proxy.app.openhands.ai',
        'https://llm-proxy.eval.all-hands.dev',
        'https://llm-proxy.staging.all-hands.dev',
        'https://llm-proxy.staging.openhands.dev',
        'https://llm-proxy.staging.openhands.ai',
        os.getenv('LITE_LLM_API_URL'),
        os.getenv('OPENHANDS_PROVIDER_BASE_URL'),
    ]
    try:
        configured = _GATEWAY_URLS.validate_json(
            os.getenv('LITELLM_KNOWN_GATEWAY_URLS', '[]'), strict=True
        )
    except ValidationError as exc:
        raise ValueError(
            'LITELLM_KNOWN_GATEWAY_URLS must be a JSON list of URLs'
        ) from exc
    if any(_origin(value) is None for value in configured):
        raise ValueError('LITELLM_KNOWN_GATEWAY_URLS must be a JSON list of URLs')
    known.extend(configured)
    return origin in {_origin(url) for url in known if url}


def validate_llm_configuration(model: str | None, base_url: str | None = None) -> None:
    if not is_litellm_enabled() and is_managed_llm(model, base_url):
        raise LiteLLMIntegrationDisabled(
            'The LiteLLM integration is disabled. Select a direct provider model.'
        )


def validate_agent_llms(
    settings: AgentSettingsConfig
    | CondenserSettingsConfig
    | ConversationSettings
    | StartConversationRequest
    | LLM
    | None,
) -> None:
    """Validate final effective LLMs, including condenser and planning models.

    SDK models use their serialization contract so LLMs in nested extension
    settings are covered as well as the standard main and condenser fields.
    """
    if is_litellm_enabled() or settings is None:
        return
    validate_agent_llm_payload(
        _JSON_VALUE.validate_python(settings.model_dump(mode='json'))
    )


def validate_agent_llm_payload(payload: JsonValue) -> None:
    """Guard sparse settings patches and the final serialized dispatch payload.

    Sparse patches are not complete SDK models. Their nested structure also
    includes arbitrary plugin/tool configuration, so recursively inspect the
    JSON tree instead of assuming a fixed set of top-level LLM slots.
    """
    if not is_litellm_enabled():
        _validate_llm_tree(payload)


def _validate_llm_tree(value: JsonValue) -> None:
    # JSON's two recursive variants require narrowing; scalar values are leaves.
    if isinstance(value, dict):
        # Tool schemas can also contain a "model" property whose value is an
        # object; only a model name identifies an LLM configuration.
        if isinstance(value.get('model'), str):
            transport = _LLMTransport.model_validate(value)
            validate_llm_configuration(transport.model, transport.base_url)
        for child in value.values():
            _validate_llm_tree(child)
    elif isinstance(value, list):
        for child in value:
            _validate_llm_tree(child)


def llm_credentials_compatible(
    model: str | None,
    base_url: str | None,
    other_model: str | None,
    other_base_url: str | None,
) -> bool:
    """Only inherit a credential within the same provider and endpoint."""
    if uses_managed_gateway(model, base_url) or uses_managed_gateway(
        other_model, other_base_url
    ):
        gateway = (
            os.getenv('OPENHANDS_PROVIDER_BASE_URL')
            or os.getenv('LITE_LLM_API_URL')
            or 'https://llm-proxy.app.all-hands.dev'
        )
        return (
            is_litellm_enabled()
            and uses_managed_gateway(model, base_url)
            and uses_managed_gateway(other_model, other_base_url)
            and _origin(base_url or gateway) == _origin(other_base_url or gateway)
        )
    from openhands.app_server.utils.llm import _assign_provider, get_provider_api_base

    if not model or not other_model:
        return False
    provider = _assign_provider(model).split('/', 1)[0]
    other_provider = _assign_provider(other_model).split('/', 1)[0]
    if provider != other_provider:
        return False
    endpoint = (base_url or get_provider_api_base(model) or '').rstrip('/')
    other_endpoint = (
        other_base_url or get_provider_api_base(other_model) or ''
    ).rstrip('/')
    return (_origin(endpoint), urlsplit(endpoint).path.rstrip('/')) == (
        _origin(other_endpoint),
        urlsplit(other_endpoint).path.rstrip('/'),
    )


def direct_llm_defaults() -> DirectLLMDefaults:
    """Native/keyless setup defaults without importing SaaS configuration."""
    model = os.getenv('OPENHANDS_DEFAULT_LLM_MODEL') or os.getenv('LLM_MODEL')
    base_url = os.getenv('OPENHANDS_DEFAULT_LLM_BASE_URL') or os.getenv('LLM_BASE_URL')
    api_key = os.getenv('OPENHANDS_DEFAULT_LLM_API_KEY') or os.getenv('LLM_API_KEY')
    if not model or is_managed_llm(model, base_url):
        return {'model': 'openai/gpt-4o', 'base_url': None, 'api_key': None}
    return {'model': model, 'base_url': base_url, 'api_key': api_key or None}


def uses_managed_gateway(model: str | None, base_url: str | None) -> bool:
    """Whether gateway credentials may be used for this effective transport.

    Unlike disabled-mode rejection, a managed-looking prefix with an explicit
    external endpoint never authorizes sending a managed virtual key there.
    """
    if base_url:
        return is_managed_llm(None, base_url)
    return is_managed_llm(model)
