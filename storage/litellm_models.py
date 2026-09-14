"""Validated LiteLLM management responses and application financial projections.

Optional TypedDict fields retain the wire distinction between absent data and null.
The gateway can return legacy serialized member records or toolkit model records;
those are normalized only at the response boundary.
"""

from dataclasses import dataclass
from typing import Annotated, TypeVar

import httpx
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    JsonValue,
    TypeAdapter,
    with_config,
)
from typing_extensions import TypedDict

ResponseT = TypeVar('ResponseT')
_RESPONSE_CONFIG = ConfigDict(
    strict=True, hide_input_in_errors=True, allow_inf_nan=False
)


@with_config(_RESPONSE_CONFIG)
class LiteLlmBudget(TypedDict, total=False):
    max_budget: float | None


@with_config(_RESPONSE_CONFIG)
class LiteLlmMember(TypedDict, total=False):
    user_id: str | None
    user_email: str | None
    role: str
    team_id: str | None
    budget_id: str | None
    spend: float | None
    max_budget_in_team: float | None
    litellm_budget_table: LiteLlmBudget | None


_MEMBER = TypeAdapter(LiteLlmMember)


def _member_input(value: JsonValue | BaseModel) -> JsonValue | LiteLlmMember:
    if isinstance(value, str):
        # Older responses contain either a plain user id or a serialized record.
        if value.lstrip().startswith('{'):
            return _MEMBER.validate_json(value)
        return {'user_id': value}
    if isinstance(value, BaseModel):
        return _MEMBER.validate_python(value.model_dump(exclude_unset=True))
    return value


MemberRecord = Annotated[LiteLlmMember, BeforeValidator(_member_input)]


@with_config(_RESPONSE_CONFIG)
class LiteLlmMetadata(TypedDict, total=False):
    type: str | None
    team_member_budget_id: str | None
    version: int
    model: str
    native_work_id: str


@with_config(_RESPONSE_CONFIG)
class LiteLlmKey(TypedDict, total=False):
    user_id: str | None
    team_id: str | None
    token: str | None
    key_alias: str | None
    key_name: str | None
    spend: float | None
    max_budget: float | None
    metadata: LiteLlmMetadata | None


@with_config(_RESPONSE_CONFIG)
class LiteLlmTeamInfo(LiteLlmBudget, total=False):
    team_id: str
    spend: float | None
    models: list[str] | None
    metadata: LiteLlmMetadata | None
    members_with_roles: list[MemberRecord] | None


@with_config(_RESPONSE_CONFIG)
class LiteLlmTeamResponse(TypedDict, total=False):
    team_info: LiteLlmTeamInfo
    team_memberships: list[MemberRecord] | None
    keys: list[LiteLlmKey] | None


@with_config(_RESPONSE_CONFIG)
class LiteLlmUserInfo(LiteLlmBudget, total=False):
    user_id: str | None
    spend: float | None


@with_config(_RESPONSE_CONFIG)
class LiteLlmUserResponse(TypedDict, total=False):
    user_info: LiteLlmUserInfo
    keys: list[LiteLlmKey]


@with_config(_RESPONSE_CONFIG)
class LiteLlmKeyListResponse(TypedDict, total=False):
    keys: list[str]


@with_config(_RESPONSE_CONFIG)
class LiteLlmKeyResponse(TypedDict, total=False):
    info: LiteLlmKey | None


@with_config(_RESPONSE_CONFIG)
class LiteLlmGeneratedKey(TypedDict):
    key: str


class KeyFinancialData(TypedDict, total=False):
    key_max_budget: float | None
    key_spend: float | None


@with_config(_RESPONSE_CONFIG)
class MemberFinancialData(TypedDict):
    spend: float
    max_budget: float | None
    uses_shared_budget: bool


@with_config(_RESPONSE_CONFIG)
class TeamFinancialData(TypedDict, total=False):
    team_max_budget: float | None
    team_spend: float
    members: dict[str, MemberFinancialData]


TEAM_RESPONSE = TypeAdapter(LiteLlmTeamResponse)
USER_RESPONSE = TypeAdapter(LiteLlmUserResponse)
KEY_LIST_RESPONSE = TypeAdapter(LiteLlmKeyListResponse)
KEY_RESPONSE = TypeAdapter(LiteLlmKeyResponse)
GENERATED_KEY = TypeAdapter(LiteLlmGeneratedKey)


def parse_response(
    response: httpx.Response, schema: TypeAdapter[ResponseT]
) -> ResponseT:
    """Validate the untrusted HTTP JSON once without exposing input in errors."""
    try:
        return schema.validate_python(response.json())
    except ValueError:
        # Validation errors can embed credentials in paths and chained input.
        raise ValueError('Invalid LiteLLM management response') from None


@with_config(_RESPONSE_CONFIG)
class LiteLlmModelResponse(TypedDict, total=False):
    data: list[JsonValue] | None


MODEL_RESPONSE = TypeAdapter(LiteLlmModelResponse)


@dataclass(frozen=True)
class ModelDiscoveryEntry:
    name: str
    hidden: bool
    canonical: str | None


def parse_model_entry(entry: JsonValue) -> ModelDiscoveryEntry | None:
    """Normalize a discovery row, retaining legacy malformed-row tolerance."""
    if not isinstance(entry, dict):
        return None
    name = entry.get('model_name')
    if not isinstance(name, str) or not name:
        return None
    model_info = entry.get('model_info')
    if not isinstance(model_info, dict):
        return ModelDiscoveryEntry(name=name, hidden=False, canonical=None)
    canonical = model_info.get('openhands_canonical')
    return ModelDiscoveryEntry(
        name=name,
        hidden=bool(model_info.get('openhands_hidden')),
        canonical=canonical if isinstance(canonical, str) and canonical else None,
    )
