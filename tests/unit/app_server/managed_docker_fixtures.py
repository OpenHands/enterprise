"""Typed Docker fixtures backed by SDK clients and resource models."""

import copy
from contextlib import ExitStack
from dataclasses import dataclass
from typing import NotRequired, TypedDict, Unpack
from unittest.mock import MagicMock, PropertyMock, patch

import httpx
from docker import DockerClient
from docker.errors import APIError, NotFound
from docker.models.containers import Container, ContainerCollection
from docker.models.images import Image, ImageCollection
from docker.models.volumes import Volume, VolumeCollection
from docker.types import Mount
from pydantic import TypeAdapter

from openhands.agent_server.init_router import InitState
from openhands.app_server.sandbox.managed_docker_sandbox_service import _InitPayload


class CreateOptions(TypedDict):
    image: str
    command: list[str] | None
    name: str
    labels: dict[str, str]
    mounts: list[Mount]
    detach: bool
    auto_remove: bool
    user: str | None
    environment: NotRequired[dict[str, str]]
    ports: NotRequired[dict[str, tuple[str, int]]]
    working_dir: NotRequired[str]
    network: NotRequired[str | None]
    extra_hosts: NotRequired[dict[str, str]]
    mem_limit: NotRequired[str | int | None]
    nano_cpus: NotRequired[int | None]
    entrypoint: NotRequired[list[str]]
    network_disabled: NotRequired[bool]


class ListFilters(TypedDict):
    label: str
    id: NotRequired[list[str]]
    name: NotRequired[list[str]]


class ListOptions(TypedDict):
    all: bool
    sparse: bool
    filters: ListFilters


class ResourceConfig(TypedDict):
    Labels: dict[str, str]


class ContainerState(TypedDict):
    Status: str
    StartedAt: str


class PortBinding(TypedDict):
    HostPort: str


class NetworkSettings(TypedDict):
    Ports: dict[str, list[PortBinding]]


class ContainerAttrs(TypedDict):
    Id: str
    Name: str
    Config: ResourceConfig
    State: ContainerState
    NetworkSettings: NetworkSettings


class VolumeAttrs(TypedDict):
    Name: str
    Labels: dict[str, str]


@dataclass
class ContainerFixture:
    model: Container
    attrs: ContainerAttrs
    start: MagicMock
    pause: MagicMock
    unpause: MagicMock
    remove: MagicMock
    reload: MagicMock
    wait: MagicMock

    @property
    def id(self) -> str:
        return self.attrs['Id']

    @property
    def name(self) -> str:
        return self.attrs['Name']


@dataclass
class VolumeFixture:
    model: Volume
    attrs: VolumeAttrs
    remove: MagicMock


class NativeDocker:
    """Real SDK resource objects; intercepted collection and lifecycle calls."""

    def __init__(self) -> None:
        self.client = DockerClient(
            base_url='unix:///unused-test-docker.sock', version='1.41'
        )
        self.items: dict[str, ContainerFixture] = {}
        self.volume_items: dict[str, VolumeFixture] = {}
        self.created: list[CreateOptions] = []
        self.listed: list[ListOptions] = []
        self.calls: list[tuple[str, str]] = []
        self.fail_create = False
        self.fail_cleanup = False
        self.fail_lookup = False
        self.fail_pause = False
        self.init_state: InitState = 'dormant'
        self.active_key: str | None = None
        self.workspace_key: str | None = None
        self.init_key: str | None = None
        self.init_posts: list[tuple[dict[str, str], _InitPayload]] = []
        self.fail_init = False
        self.fail_ready = False
        self.containers = MagicMock(spec=ContainerCollection)
        self.containers.create.side_effect = self.create
        self.containers.get.side_effect = self.get_model
        self.containers.list.side_effect = self.list
        self.volumes = MagicMock(spec=VolumeCollection)
        self.volumes.create.side_effect = self.create_volume_model
        self.volumes.get.side_effect = self.get_volume_model
        self.images = MagicMock(spec=ImageCollection)
        self.images.get.return_value = Image(
            attrs={'Config': {'User': '10001:10001'}}, client=self.client
        )

    def intercept(self) -> ExitStack:
        stack = ExitStack()
        for name, collection in (
            ('containers', self.containers),
            ('volumes', self.volumes),
            ('images', self.images),
        ):
            stack.enter_context(
                patch.object(
                    DockerClient,
                    name,
                    new_callable=PropertyMock,
                    return_value=collection,
                )
            )
        stack.callback(self.client.close)
        return stack

    def get(self, identity: str | None) -> ContainerFixture:
        assert identity is not None
        self.calls.append(('get', identity))
        if self.fail_lookup:
            raise APIError('daemon unavailable')
        item = next(
            (c for c in self.items.values() if c.id == identity or c.name == identity),
            None,
        )
        if item is None:
            raise NotFound('missing')
        return item

    def get_model(self, identity: str) -> Container:
        return self.get(identity).model

    def list(self, **kwargs: Unpack[ListOptions]) -> list[Container]:
        self.listed.append(kwargs)
        if self.fail_lookup:
            raise APIError('daemon unavailable')
        if 'id' in kwargs['filters']:
            return [
                c.model for c in self.items.values() if c.id in kwargs['filters']['id']
            ]
        names = [
            name.removeprefix('^/').removesuffix('$')
            for name in kwargs['filters']['name']
        ]
        return [c.model for c in self.items.values() if c.name in names]

    def create(self, **kwargs: Unpack[CreateOptions]) -> Container:
        self.calls.append(('create', kwargs['name']))
        if self.fail_create and kwargs['labels'].get('org.openhands.role') == 'sandbox':
            raise APIError('create failed')
        self.created.append(copy.deepcopy(kwargs))
        attributes: ContainerAttrs = {
            'Id': f'native-{len(self.created)}',
            'Name': kwargs['name'],
            'Config': {'Labels': kwargs['labels']},
            'State': {'Status': 'created', 'StartedAt': '0001-01-01T00:00:00Z'},
            'NetworkSettings': {
                'Ports': {
                    str(port): [{'HostPort': str(41000 + i)}]
                    for i, port in enumerate(kwargs.get('ports', {}))
                }
            },
        }
        model = Container(attrs=dict(attributes), client=self.client)
        generation = 0

        def start() -> None:
            nonlocal generation
            generation += 1
            attributes['State']['Status'] = 'running'
            attributes['State']['StartedAt'] = f'2026-09-12T00:00:{generation:02}Z'
            if kwargs['labels'].get('org.openhands.role') == 'sandbox':
                self.init_state = 'dormant'
                self.active_key = None
                self.init_key = kwargs['environment']['OH_SECRET_KEY']

        def pause() -> None:
            if self.fail_pause:
                raise APIError('cannot pause')
            attributes['State']['Status'] = 'paused'

        def unpause() -> None:
            attributes['State']['Status'] = 'running'

        def remove(*, force: bool = False) -> None:
            if self.fail_cleanup:
                raise APIError('cannot remove')
            self.items.pop(kwargs['name'], None)

        item = ContainerFixture(
            model,
            attributes,
            MagicMock(side_effect=start),
            MagicMock(side_effect=pause),
            MagicMock(side_effect=unpause),
            MagicMock(side_effect=remove),
            MagicMock(),
            MagicMock(return_value={'StatusCode': 0}),
        )
        for name, method in (
            ('start', item.start),
            ('pause', item.pause),
            ('unpause', item.unpause),
            ('remove', item.remove),
            ('reload', item.reload),
            ('wait', item.wait),
        ):
            setattr(model, name, method)
        self.items[item.name] = item
        return model

    def get_volume(self, name: str) -> VolumeFixture:
        if name not in self.volume_items:
            raise NotFound('missing volume')
        return self.volume_items[name]

    def get_volume_model(self, name: str) -> Volume:
        return self.get_volume(name).model

    def create_volume(self, name: str, labels: dict[str, str]) -> VolumeFixture:
        attrs: VolumeAttrs = {'Name': name, 'Labels': labels}
        model = Volume(attrs=dict(attrs), client=self.client)

        def remove() -> None:
            if self.fail_cleanup:
                raise APIError('cannot delete volume')
            self.volume_items.pop(name, None)

        item = VolumeFixture(model, attrs, MagicMock(side_effect=remove))
        setattr(model, 'remove', item.remove)
        self.volume_items[name] = item
        return item

    def create_volume_model(self, name: str, labels: dict[str, str]) -> Volume:
        return self.create_volume(name, labels).model

    def http(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/init' and request.method == 'GET':
            return httpx.Response(200, json={'state': self.init_state})
        if request.url.path == '/api/init' and request.method == 'POST':
            body = TypeAdapter(_InitPayload).validate_json(request.content)
            self.init_posts.append((dict(request.headers), body))
            if self.fail_init:
                return httpx.Response(500, json={})
            if (
                self.init_state != 'dormant'
                or request.headers['X-Init-API-Key'] != self.init_key
            ):
                return httpx.Response(401, json={})
            self.init_state = 'ready'
            self.active_key = body['session_api_keys'][0]
            self.workspace_key = body['secret_key']
            return httpx.Response(200, json={'state': 'ready'})
        if request.url.path == '/api/conversations/search':
            if (
                self.fail_ready
                or request.headers['X-Session-API-Key'] != self.active_key
            ):
                return httpx.Response(503, json={})
            return httpx.Response(200, json={'items': []})
        raise AssertionError(request.url)
