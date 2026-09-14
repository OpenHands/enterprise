"""Validated operator configuration and private provider launch specifications."""

import ipaddress
import os
import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from string import Formatter
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    SerializationInfo,
    TypeAdapter,
    ValidationError,
    field_serializer,
    model_validator,
)

Provider = Literal['runtime_api', 'docker']
ServiceName = Literal['AGENT_SERVER', 'VSCODE', 'WORKER_1', 'WORKER_2']
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


class _LaunchSerializationContext(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    expose_secrets: bool = False


def _absolute_path(value: str) -> bool:
    return value.startswith('/') and '..' not in PurePosixPath(value).parts


def _http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in ('http', 'https')
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and (parsed.port is None or 0 < parsed.port < 65536)
        )
    except ValueError:
        return False


class DockerBindMount(_ConfigurationModel):
    """An operator-owned bind source. Sandbox cleanup never removes this source."""

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    read_only: bool = False

    @model_validator(mode='after')
    def validate_paths(self) -> Self:
        if not _absolute_path(self.source) or not _absolute_path(self.target):
            raise ValueError(
                'mount source and target must be absolute paths without ..'
            )
        return self


def _default_ports() -> dict[ServiceName, int]:
    return {'AGENT_SERVER': 8000}


def _validate_public_url_pattern(pattern: str) -> None:
    """Public ingress URLs identify a resource and service in one DNS label."""
    message = (
        'docker.public_url_pattern must be an HTTPS origin with plain '
        '{resource_id} and {container_port} in its first DNS label'
    )
    try:
        parts = list(Formatter().parse(pattern))
        fields = [name for _, name, _, _ in parts if name is not None]
        if sorted(fields) != ['container_port', 'resource_id'] or any(
            spec or conversion for _, _, spec, conversion in parts
        ):
            raise ValueError(message)
        rendered = pattern.format(resource_id='0' * 32, container_port=12345)
        url = urlsplit(rendered)
        first_label = pattern.removeprefix('https://').split('.', 1)[0]
        if (
            not all('{' + name + '}' in first_label for name in fields)
            or not pattern.startswith('https://')
            or not _http_url(rendered)
            or url.port is not None
            or url.path not in ('', '/')
            or url.query
            or not re.fullmatch(
                r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
                r'(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+',
                url.netloc,
            )
            or len(url.netloc) > 253
        ):
            raise ValueError(message)
    except (ValueError, KeyError, IndexError):
        raise ValueError(message) from None


class DockerLaunchOptions(_ConfigurationModel):
    network: str = Field(default='bridge', min_length=1)
    bind_host: str = Field(default='127.0.0.1', min_length=1)
    container_url_pattern: str = 'http://localhost:{port}'
    public_url_pattern: str | None = None
    webhook_url: str | None = None
    extra_hosts: dict[str, str] = Field(default_factory=dict)
    workspace_mount_path: str = '/workspace'
    ports: dict[ServiceName, Annotated[int, Field(ge=1, le=65535)]] = Field(
        default_factory=_default_ports
    )
    user: str | None = None
    mem_limit: str | None = None
    nano_cpus: Annotated[int, Field(gt=0)] | None = None
    mounts: list[DockerBindMount] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_options(self) -> Self:
        if self.public_url_pattern is not None:
            _validate_public_url_pattern(self.public_url_pattern)
        if self.network in ('host', 'none') or self.network.startswith('container:'):
            raise ValueError('docker.network must support isolated published ports')
        try:
            ipaddress.ip_address(self.bind_host)
        except ValueError:
            raise ValueError('docker.bind_host must be an IP address') from None
        if (
            not _absolute_path(self.workspace_mount_path)
            or self.workspace_mount_path == '/'
        ):
            raise ValueError(
                'docker.workspace_mount_path must be an absolute non-root path'
            )
        if 'AGENT_SERVER' not in self.ports or len(set(self.ports.values())) != len(
            self.ports
        ):
            raise ValueError(
                'docker.ports requires AGENT_SERVER and distinct container ports'
            )
        try:
            fields = [
                name
                for _, name, spec, conversion in Formatter().parse(
                    self.container_url_pattern
                )
                if name is not None
            ]
            # Only a plain {port} substitution is supported. Format specs and nested
            # fields make routing ambiguous and can fail only after container creation.
            valid_format = all(
                name is None or (name == 'port' and not spec and not conversion)
                for _, name, spec, conversion in Formatter().parse(
                    self.container_url_pattern
                )
            )
            rendered = self.container_url_pattern.format(port=12345)
        except (ValueError, KeyError, IndexError):
            raise ValueError(
                'docker.container_url_pattern must be an HTTP URL with {port}'
            ) from None
        if (
            fields != ['port']
            or not valid_format
            or not _http_url(rendered)
            or urlsplit(rendered).port != 12345
        ):
            raise ValueError(
                'docker.container_url_pattern must be an HTTP URL with {port}'
            )
        if self.webhook_url is not None and not _http_url(self.webhook_url):
            raise ValueError('docker.webhook_url must be an HTTP URL')
        if self.mem_limit is not None and not re.fullmatch(
            r'[1-9][0-9]*[bkmgBKMG]?', self.mem_limit
        ):
            raise ValueError('docker.mem_limit must be a positive size such as 4g')
        if self.user is not None and not re.fullmatch(
            r'[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)?', self.user
        ):
            raise ValueError('docker.user must be a user or user:group')
        workspace = PurePosixPath(self.workspace_mount_path)
        targets = [PurePosixPath(mount.target) for mount in self.mounts]
        if len(set(targets)) != len(targets) or any(
            target == workspace
            or target in workspace.parents
            or workspace in target.parents
            for target in targets
        ):
            raise ValueError(
                'docker.mounts must have distinct targets outside the managed workspace'
            )
        return self

    @property
    def public_url_domain(self) -> str | None:
        if self.public_url_pattern is None:
            return None
        return self.public_url_pattern.split('.', 1)[1].rstrip('/')


class RuntimeAPITemplate(_ConfigurationModel):
    id: TemplateId
    provider: Literal['runtime_api'] = 'runtime_api'
    config_name: str = Field(min_length=1, pattern=r'^\S(?:.*\S)?$')
    init_api_key_env: EnvironmentReference | None = None


class DockerTemplate(_ConfigurationModel):
    id: TemplateId
    provider: Literal['docker'] = 'docker'
    image: str = Field(min_length=1, pattern=r'^\S+$')
    command: list[str] | None = Field(default=None, repr=False)
    working_dir: str = '/workspace/project'
    initial_env: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    docker: DockerLaunchOptions = Field(default_factory=DockerLaunchOptions)

    @model_validator(mode='after')
    def validate_workspace(self) -> Self:
        if any(
            name in ('OH_SECRET_KEY', 'OH_SESSION_API_KEYS')
            or name.startswith('OH_SESSION_API_KEYS_')
            for name in self.initial_env
        ):
            raise ValueError(
                'Docker template credential fields are reserved for per-sandbox '
                'startup: OH_SECRET_KEY and OH_SESSION_API_KEYS (including indexed fields)'
            )
        workspace = PurePosixPath(self.docker.workspace_mount_path)
        working_dir = PurePosixPath(self.working_dir)
        if not _absolute_path(self.working_dir) or (
            working_dir != workspace and workspace not in working_dir.parents
        ):
            raise ValueError('working_dir must be inside docker.workspace_mount_path')
        for name in (
            'OH_PERSISTENCE_DIR',
            'OH_CONVERSATIONS_PATH',
            'OH_BASH_EVENTS_DIR',
            'OH_CONVERSATION_WORKTREE_ROOT',
            'OH_WORKSPACE_PATH',
        ):
            if name in self.initial_env:
                path = self.initial_env[name].get_secret_value()
                if (
                    not _absolute_path(path)
                    or workspace not in PurePosixPath(path).parents
                ):
                    raise ValueError(
                        f'{name} must be inside docker.workspace_mount_path'
                    )
        if any(
            not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)
            for name in self.initial_env
        ):
            raise ValueError(
                'initial_env names must be valid environment variable names'
            )
        agent_port = self.docker.ports['AGENT_SERVER']
        if self.command:
            if not any(
                arg == '--port' or arg.startswith('--port=') for arg in self.command
            ):
                raise ValueError(
                    'command must specify --port matching docker.ports.AGENT_SERVER'
                )
            for index, arg in enumerate(self.command):
                if arg == '--port':
                    if index + 1 == len(self.command) or self.command[index + 1] != str(
                        agent_port
                    ):
                        raise ValueError(
                            'command --port must match docker.ports.AGENT_SERVER'
                        )
                elif arg.startswith('--port=') and arg != f'--port={agent_port}':
                    raise ValueError(
                        'command --port must match docker.ports.AGENT_SERVER'
                    )
        vscode_port = self.initial_env.get('OH_VSCODE_PORT')
        if vscode_port is not None and (
            'VSCODE' not in self.docker.ports
            or vscode_port.get_secret_value() != str(self.docker.ports['VSCODE'])
        ):
            raise ValueError('OH_VSCODE_PORT must match docker.ports.VSCODE')
        return self


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


class DockerLaunchSpec(_ConfigurationModel):
    """Private Docker startup data; the adapter adds credentials at allocation."""

    id: str
    provider: Literal['docker'] = 'docker'
    image: str
    command: list[str] | None = Field(repr=False)
    working_dir: str
    initial_env: dict[str, SecretStr] = Field(repr=False)
    docker: DockerLaunchOptions

    @field_serializer('initial_env', when_used='json')
    def serialize_environment(
        self, values: dict[str, SecretStr], info: SerializationInfo
    ) -> dict[str, str]:
        expose = (
            _LaunchSerializationContext.model_validate(info.context).expose_secrets
            if info.context is not None
            else False
        )
        return {
            name: value.get_secret_value() if expose else str(value)
            for name, value in values.items()
        }


class SandboxProviderConfig(_ConfigurationModel):
    provider: Provider
    templates: list[RuntimeAPITemplate | DockerTemplate] = Field(
        default_factory=list, repr=False
    )
    default_template: str | None = None

    @model_validator(mode='after')
    def validate_catalog(self) -> Self:
        ids = [template.id for template in self.templates]
        if len(ids) != len(set(ids)):
            raise ValueError('SANDBOX_TEMPLATES contains duplicate template IDs')
        if any(template.provider != self.provider for template in self.templates):
            raise ValueError('SANDBOX_TEMPLATES provider must match SANDBOX_PROVIDER')
        if self.provider == 'docker' and not self.templates:
            raise ValueError(
                'SANDBOX_PROVIDER=docker requires nonempty SANDBOX_TEMPLATES'
            )
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
        if provider not in ('runtime_api', 'docker'):
            raise ValueError('SANDBOX_PROVIDER supports runtime_api and docker')
        raw_templates = env.get('SANDBOX_TEMPLATES')
        try:
            data = _TEMPLATE_ENTRIES.validate_json(
                raw_templates if raw_templates is not None else '[]', strict=True
            )
        except ValidationError:
            raise ValueError('SANDBOX_TEMPLATES must be a valid JSON list') from None
        templates: list[RuntimeAPITemplate | DockerTemplate] = []
        for index, item in enumerate(data):
            try:
                entry = _TEMPLATE_ENTRY.validate_python(item, strict=True)
            except ValidationError:
                raise ValueError(
                    f'SANDBOX_TEMPLATES entry {index} must be an object'
                ) from None
            try:
                template_type = (
                    RuntimeAPITemplate if provider == 'runtime_api' else DockerTemplate
                )
                template = template_type.model_validate({'provider': provider, **entry})
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
            if template.provider == 'docker':
                _docker_environment(template)
            templates.append(template)
        try:
            return cls(
                provider='runtime_api' if provider == 'runtime_api' else 'docker',
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


def _docker_environment(template: DockerTemplate) -> dict[str, SecretStr]:
    """Build deferred startup settings without allocating sandbox credentials."""
    result = template.initial_env.copy()
    deferred = result.get('OH_DEFERRED_INIT')
    if deferred is not None and deferred.get_secret_value().lower() not in (
        'true',
        '1',
    ):
        raise ValueError('Docker template OH_DEFERRED_INIT must be true or 1')
    result['OH_DEFERRED_INIT'] = SecretStr('true')
    workspace = template.docker.workspace_mount_path.rstrip('/')
    result.setdefault('OH_PERSISTENCE_DIR', SecretStr(f'{workspace}/.openhands'))
    result.setdefault('OH_CONVERSATIONS_PATH', SecretStr(f'{workspace}/conversations'))
    result.setdefault('OH_BASH_EVENTS_DIR', SecretStr(f'{workspace}/bash_events'))
    result.setdefault(
        'OH_CONVERSATION_WORKTREE_ROOT', SecretStr(f'{workspace}/worktrees')
    )
    result.setdefault('OH_WORKSPACE_PATH', SecretStr(template.working_dir))
    return result
