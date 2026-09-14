"""Validated fields consumed from Docker's inspect and sparse-list responses."""

from pydantic import BaseModel, ConfigDict, Field


class DockerResponse(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)


class DockerResourceConfig(DockerResponse):
    labels: dict[str, str] | None = Field(default=None, alias='Labels')
    user: str = Field(default='', alias='User')


class DockerResourceAttributes(DockerResponse):
    config: DockerResourceConfig = Field(
        default_factory=DockerResourceConfig, alias='Config'
    )
    labels: dict[str, str] | None = Field(default=None, alias='Labels')

    def ownership_labels(self) -> dict[str, str]:
        return self.config.labels or self.labels or {}


class DockerContainerState(DockerResponse):
    status: str = Field(default='unknown', alias='Status')
    started_at: str = Field(default='', alias='StartedAt')


class DockerPortBinding(DockerResponse):
    host_port: str = Field(alias='HostPort')


class DockerNetworkSettings(DockerResponse):
    ports: dict[str, list[DockerPortBinding] | None] | None = Field(
        default=None, alias='Ports'
    )


class DockerContainerInspect(DockerResponse):
    state: DockerContainerState = Field(
        default_factory=DockerContainerState, alias='State'
    )
    network_settings: DockerNetworkSettings = Field(
        default_factory=DockerNetworkSettings, alias='NetworkSettings'
    )


class DockerContainerNames(DockerResponse):
    names: list[str] | None = Field(default=None, alias='Names')
    name: str = Field(default='', alias='Name')

    def all_names(self) -> list[str]:
        return self.names if self.names is not None else [self.name]


class DockerWaitResult(DockerResponse):
    status_code: int = Field(alias='StatusCode')
