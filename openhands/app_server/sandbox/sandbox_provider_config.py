"""Validated operator configuration and private provider launch specifications."""

import os
from collections.abc import Mapping
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    TypeAdapter,
    ValidationError,
    model_validator,
)

Provider = Literal['runtime_api']
TemplateId = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
EnvironmentReference = Annotated[str, Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')]
_TEMPLATE_ENTRIES: TypeAdapter[list[JsonValue]] = TypeAdapter(
    list[JsonValue], config=ConfigDict(hide_input_in_errors=True)
)
_TEMPLATE_ENTRY: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(
    dict[str, JsonValue], config=ConfigDict(hide_input_in_errors=True)
)


class _ConfigurationModel(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True, strict=True)


class RuntimeAPITemplate(_ConfigurationModel):
    id: TemplateId
    provider: Literal['runtime_api'] = 'runtime_api'
    config_name: str = Field(min_length=1, pattern=r'^\S(?:.*\S)?$')
    init_api_key_env: EnvironmentReference | None = None


class RuntimeAPILaunchSpec(_ConfigurationModel):
    """Private resolved native preset. Never use as a public response model."""

    id: str
    provider: Literal['runtime_api'] = 'runtime_api'
    image: str = Field(min_length=1, pattern=r'^\S+$')
    command: list[str] | None = Field(repr=False)
    working_dir: str
    initial_env: dict[str, SecretStr] = Field(repr=False)
    init_api_key: SecretStr | None = Field(default=None, repr=False)
    run_as_user: int = 10001
    run_as_group: int = 10001
    fs_group: int = 10001


class SandboxProviderConfig(_ConfigurationModel):
    provider: Provider
    templates: list[RuntimeAPITemplate] = Field(default_factory=list, repr=False)
    default_template: str | None = None

    @model_validator(mode='after')
    def validate_catalog(self) -> Self:
        ids = [template.id for template in self.templates]
        if len(ids) != len(set(ids)):
            raise ValueError('SANDBOX_TEMPLATES contains duplicate template IDs')
        if any(template.provider != self.provider for template in self.templates):
            raise ValueError('SANDBOX_TEMPLATES provider must match SANDBOX_PROVIDER')
        if self.templates and self.default_template not in ids:
            raise ValueError(
                'SANDBOX_DEFAULT_TEMPLATE must identify a configured template'
            )
        if not self.templates and self.default_template is not None:
            raise ValueError('SANDBOX_DEFAULT_TEMPLATE requires SANDBOX_TEMPLATES')
        return self

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> Self | None:
        """Parse explicit provider configuration without exposing raw operator input."""
        env = os.environ if environment is None else environment
        provider = env.get('SANDBOX_PROVIDER')
        if provider is None:
            return None
        if provider != 'runtime_api':
            raise ValueError('SANDBOX_PROVIDER supports runtime_api')
        raw_templates = env.get('SANDBOX_TEMPLATES')
        try:
            data = _TEMPLATE_ENTRIES.validate_json(
                raw_templates if raw_templates is not None else '[]', strict=True
            )
        except ValidationError:
            raise ValueError('SANDBOX_TEMPLATES must be a valid JSON list') from None
        templates: list[RuntimeAPITemplate] = []
        for index, item in enumerate(data):
            try:
                entry = _TEMPLATE_ENTRY.validate_python(item, strict=True)
            except ValidationError:
                raise ValueError(
                    f'SANDBOX_TEMPLATES entry {index} must be an object'
                ) from None
            try:
                template = RuntimeAPITemplate.model_validate(
                    {'provider': provider, **entry}
                )
            except ValidationError as exc:
                # Field paths identify configuration mistakes; input and validator
                # context can include complete environments or unvalidated secrets.
                details = []
                for error in exc.errors(include_input=False, include_context=False):
                    path = '.'.join(str(part) for part in error['loc'])
                    message = error['msg'].removeprefix('Value error, ')
                    details.append(f'{path}: {message}' if path else message)
                raise ValueError(
                    f'SANDBOX_TEMPLATES entry {index} has invalid fields: {"; ".join(details)}'
                ) from None
            if template.provider == 'runtime_api' and template.init_api_key_env:
                _bootstrap_key(template.init_api_key_env, env)
            templates.append(template)
        try:
            return cls(
                provider='runtime_api',
                templates=templates,
                default_template=env.get('SANDBOX_DEFAULT_TEMPLATE'),
            )
        except ValidationError as exc:
            # All cross-catalog errors have fixed messages and no input values.
            message = exc.errors(include_input=False)[0]['msg']
            raise ValueError(message.removeprefix('Value error, ')) from None


def _bootstrap_key(reference: str, environment: Mapping[str, str]) -> SecretStr:
    value = environment.get(reference)
    if value is None or not value.strip():
        raise ValueError(
            f'Template init_api_key_env {reference} must reference a nonempty app environment variable'
        )
    return SecretStr(value)
