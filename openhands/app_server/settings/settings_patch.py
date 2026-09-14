"""Typed conversion of sparse settings input and SDK JSON serialization."""

from collections.abc import Mapping

from fastmcp.mcp_config import MCPConfig
from pydantic import BaseModel, ConfigDict, JsonValue, SecretStr, TypeAdapter

_JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(
    JsonValue, config=ConfigDict(hide_input_in_errors=True)
)
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_INPUT_OBJECT = TypeAdapter(dict[str, object])
_INPUT_LIST = TypeAdapter(list[object])
_SECRET: TypeAdapter[SecretStr | None] = TypeAdapter(SecretStr | None)


def secret_text(value: str | SecretStr | None) -> str | None:
    """Normalize the SDK's two supported credential representations."""
    secret = _SECRET.validate_python(value)
    return secret.get_secret_value() if secret is not None else None


def normalize_settings_input(value: object) -> JsonValue:
    """Accept wire JSON and the supported in-process SDK/secret leaves.

    Shape checks live at this input boundary: nested JSON values need recursive
    conversion, while model dumps must expose actual secrets before merging.
    """
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, MCPConfig):
        return (
            _JSON_OBJECT.validate_python(
                value.model_dump(exclude_none=True, exclude_defaults=True)
            )
            or None
        )
    if isinstance(value, BaseModel):
        # SDK LLM, MCP server/auth and extension models are valid internal
        # patch leaves. Serialize once at the boundary before SDK validation.
        return _JSON_OBJECT.validate_python(
            value.model_dump(mode='json', context={'expose_secrets': 'plaintext'})
        )
    if isinstance(value, Mapping):
        return {
            key: normalize_settings_input(item)
            for key, item in _INPUT_OBJECT.validate_python(value).items()
        }
    if isinstance(value, (list, tuple)):
        return [
            normalize_settings_input(item)
            for item in _INPUT_LIST.validate_python(value)
        ]
    return _JSON_VALUE.validate_python(value, strict=True)


def settings_json(
    model: BaseModel, *, expose_secrets: bool = False
) -> dict[str, JsonValue]:
    """Validate the SDK's otherwise untyped JSON serialization contract."""
    return _JSON_OBJECT.validate_python(
        model.model_dump(mode='json', context={'expose_secrets': expose_secrets})
    )


def merge_settings_json(
    original: dict[str, JsonValue], patch: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Deep-merge JSON objects; null resets a field to its model default."""
    merged = original.copy()
    for key, value in patch.items():
        previous = merged.get(key)
        # Recursive JSON objects are the sole mergeable variant.
        if value is None:
            merged.pop(key, None)
        elif isinstance(previous, dict) and isinstance(value, dict):
            merged[key] = merge_settings_json(previous, value)
        else:
            merged[key] = value
    return merged
