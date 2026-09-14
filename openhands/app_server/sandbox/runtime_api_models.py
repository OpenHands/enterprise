"""Runtime API payloads at the sandbox adapter's HTTP boundary."""

from typing import NotRequired, TypedDict

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)


class RuntimeInfo(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    session_id: str | None = None
    session_api_key: str | None = Field(default=None, repr=False)
    url: str | None = None
    runtime_id: str | None = None
    status: str | None = None
    status_detail: str | None = None

    def require_runtime_id(self) -> str:
        if not self.runtime_id:
            raise ValueError('Runtime API response has no runtime ID')
        return self.runtime_id


class RuntimeList(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    runtimes: list[RuntimeInfo]


_BATCH_RESPONSE = TypeAdapter(
    list[JsonValue], config=ConfigDict(hide_input_in_errors=True)
)


def parse_runtime_batch(content: bytes) -> list[RuntimeInfo | None]:
    """Keep invalid entries unknown without discarding other decoded runtimes."""
    entries = _BATCH_RESPONSE.validate_json(content)
    result: list[RuntimeInfo | None] = []
    for entry in entries:
        if entry is None:
            result.append(None)
            continue
        try:
            result.append(RuntimeInfo.model_validate(entry))
        except ValidationError:
            result.append(RuntimeInfo())
    return result


class RuntimeStartRequest(TypedDict):
    image: str
    command: list[str] | None
    working_dir: str
    environment: dict[str, str]
    session_id: str
    resource_factor: int
    run_as_user: int
    run_as_group: int
    fs_group: int
    runtime_class: NotRequired[str]


class RuntimeRequestOptions(TypedDict, total=False):
    params: list[tuple[str, str | int | float | bool | None]]
    json: RuntimeStartRequest | dict[str, str | None]
