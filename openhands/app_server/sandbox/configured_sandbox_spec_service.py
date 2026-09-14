"""Stable public template IDs with private, complete provider launch data."""

import asyncio
import os
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import TypedDict, Unpack

import httpx
from fastapi import Request
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    ValidationError,
)

from openhands.app_server.sandbox.remote_sandbox_spec_service import (
    get_default_sandbox_specs,
)
from openhands.app_server.sandbox.sandbox_provider_config import (
    RuntimeAPILaunchSpec,
    SandboxProviderConfig,
    _bootstrap_key,
)
from openhands.app_server.sandbox.sandbox_spec_models import (
    SandboxSpecInfo,
    SandboxSpecInfoPage,
)
from openhands.app_server.sandbox.sandbox_spec_service import (
    SandboxSpecService,
    SandboxSpecServiceInjector,
)
from openhands.app_server.services.injector import InjectorState


class _RuntimeConfiguration(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    name: str = Field(min_length=1)
    image: str
    command: list[str] | None
    environment: dict[str, SecretStr]
    working_dir: str
    run_as_user: int | None = None
    run_as_group: int | None = None
    fs_group: int | None = None

    def launch(self) -> RuntimeAPILaunchSpec:
        return RuntimeAPILaunchSpec(
            id=self.image,
            image=self.image,
            command=self.command,
            initial_env=self.environment,
            working_dir=self.working_dir,
            run_as_user=self.run_as_user if self.run_as_user is not None else 10001,
            run_as_group=self.run_as_group if self.run_as_group is not None else 10001,
            fs_group=self.fs_group if self.fs_group is not None else 10001,
        )


class _RuntimeConfigurations(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    configs: list[_RuntimeConfiguration]


class _InjectorOptions(TypedDict, total=False):
    api_url: str
    api_key: SecretStr
    cache_ttl_seconds: int


@dataclass
class ConfiguredSandboxSpecService(SandboxSpecService):
    provider_config: SandboxProviderConfig
    api_url: str = ''
    api_key: SecretStr = field(default_factory=lambda: SecretStr(''), repr=False)
    cache_ttl_seconds: int = 60
    _native_configs: dict[str, RuntimeAPILaunchSpec] = field(
        default_factory=dict, init=False, repr=False
    )
    _cache_expires_at: float = field(default=0.0, init=False)
    _fetch_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False, repr=False
    )

    async def _fetch_native_configs(self) -> dict[str, RuntimeAPILaunchSpec]:
        if time.monotonic() < self._cache_expires_at:
            return self._native_configs
        async with self._fetch_lock:
            if time.monotonic() < self._cache_expires_at:
                return self._native_configs
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f'{self.api_url.rstrip("/")}/api/warm-runtime-configs',
                        headers={'X-API-Key': self.api_key.get_secret_value()},
                        timeout=10.0,
                    )
                    response.raise_for_status()
                    body = _RuntimeConfigurations.model_validate_json(response.content)
                native: dict[str, RuntimeAPILaunchSpec] = {}
                for config in body.configs:
                    if config.name in native:
                        raise ValueError
                    native[config.name] = config.launch()
            except httpx.HTTPError:
                raise ValueError(
                    'Unable to fetch Runtime API template configurations'
                ) from None
            except (KeyError, TypeError, ValueError, ValidationError):
                # Native responses may contain credentials in command/environment.
                raise ValueError(
                    'Runtime API returned invalid template configurations'
                ) from None
            self._native_configs = native
            self._cache_expires_at = time.monotonic() + self.cache_ttl_seconds
            return native

    async def get_launch_spec(self, sandbox_spec_id: str) -> RuntimeAPILaunchSpec:
        template = next(
            (
                item
                for item in self.provider_config.templates
                if item.id == sandbox_spec_id
            ),
            None,
        )
        if template is not None and template.provider == 'runtime_api':
            configs = await self._fetch_native_configs()
            native = configs.get(template.config_name)
            if native is None:
                raise ValueError(
                    f'Runtime API configuration for template {template.id!r} was not found'
                )
            return native.model_copy(
                update={
                    'id': template.id,
                    'init_api_key': _bootstrap_key(
                        template.init_api_key_env, os.environ
                    )
                    if template.init_api_key_env
                    else None,
                },
                deep=True,
            )
        if self.provider_config.provider == 'runtime_api':
            # Image IDs remain resolvable for pre-catalog inventory and user defaults.
            # They are deliberately absent from search/default catalog choices.
            configs = await self._fetch_native_configs()
            native = next(
                (
                    config
                    for config in configs.values()
                    if config.image == sandbox_spec_id
                ),
                None,
            )
            if native is not None:
                return native.model_copy(deep=True)
            for default in get_default_sandbox_specs():
                if default.id == sandbox_spec_id:
                    return RuntimeAPILaunchSpec(
                        id=default.id,
                        image=default.id,
                        command=default.command,
                        working_dir=default.working_dir,
                        initial_env={
                            name: SecretStr(value)
                            for name, value in default.initial_env.items()
                        },
                        run_as_user=default.run_as_user,
                        run_as_group=default.run_as_group,
                        fs_group=default.fs_group,
                    )
        raise KeyError(sandbox_spec_id)

    async def get_sandbox_spec(self, sandbox_spec_id: str) -> SandboxSpecInfo | None:
        try:
            launch = await self.get_launch_spec(sandbox_spec_id)
        except KeyError:
            return None
        # Even command arguments can contain secrets. Only catalog identity and
        # workspace location are public; actual startup data stays server-side.
        return SandboxSpecInfo(
            id=launch.id, command=None, initial_env={}, working_dir=launch.working_dir
        )

    async def search_sandbox_specs(
        self, page_id: str | None = None, limit: int = 100
    ) -> SandboxSpecInfoPage:
        offset = int(page_id) if page_id else 0
        if offset < 0 or limit < 1:
            raise ValueError(
                'Sandbox spec pagination must be nonnegative with a positive limit'
            )
        templates = self.provider_config.templates
        items = [
            await self.get_sandbox_spec(template.id)
            for template in templates[offset : offset + limit]
        ]
        return SandboxSpecInfoPage(
            items=[item for item in items if item is not None],
            next_page_id=str(offset + limit)
            if offset + limit < len(templates)
            else None,
        )

    async def get_default_sandbox_spec(self) -> SandboxSpecInfo:
        default_id = self.provider_config.default_template
        if default_id is None:
            raise ValueError('Configured sandbox catalog has no default template')
        spec = await self.get_sandbox_spec(default_id)
        if spec is None:
            raise ValueError('Configured default sandbox template was not found')
        return spec


class ConfiguredSandboxSpecServiceInjector(SandboxSpecServiceInjector):
    # Private because the SDK's OH_* parser cannot represent the nested secret
    # dictionaries, and this configuration is supplied only by SANDBOX_TEMPLATES.
    _provider_config: SandboxProviderConfig = PrivateAttr()

    def __init__(
        self,
        *,
        provider_config: SandboxProviderConfig,
        **kwargs: Unpack[_InjectorOptions],
    ) -> None:
        BaseModel.__init__(self, **kwargs)
        self._provider_config = provider_config

    @property
    def provider_config(self) -> SandboxProviderConfig:
        return self._provider_config

    api_url: str = Field(
        default_factory=lambda: os.environ.get('SANDBOX_REMOTE_RUNTIME_API_URL', '')
    )
    api_key: SecretStr = Field(
        default_factory=lambda: SecretStr(os.environ.get('SANDBOX_API_KEY', '')),
        repr=False,
    )
    cache_ttl_seconds: int = Field(default=60, gt=0)
    # The injector belongs to global config, so cache TTL spans HTTP requests.
    _service: ConfiguredSandboxSpecService | None = PrivateAttr(default=None)

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[SandboxSpecService, None]:
        if self._service is None:
            self._service = ConfiguredSandboxSpecService(
                provider_config=self.provider_config,
                api_url=self.api_url,
                api_key=self.api_key,
                cache_ttl_seconds=self.cache_ttl_seconds,
            )
        yield self._service
