# PodmanSandboxService: Investigation & Design

## Executive Summary

Podman provides a **Docker-compatible REST API** (`podman system service`), which
means the existing `docker` Python SDK can talk to Podman directly. The most
pragmatic `PodmanSandboxService` is therefore a **thin subclass of
`DockerSandboxService`** that overrides the connection logic, the host-alias
convention, and the `backend` discriminator — not a from-scratch reimplementation.

This document maps every integration point the Docker backend touches, identifies
where Podman differs, and shows what each new file would look like.

---

## 1. What DockerSandboxService Touches Today

Three files make up the Docker sandbox backend:

| File | Contents |
|------|----------|
| `sandbox/docker_sandbox_service.py` | `DockerSandboxService` (the service) + `DockerSandboxServiceInjector` (Pydantic config) |
| `sandbox/docker_sandbox_spec_service.py` | `get_docker_client()` singleton, `get_default_sandbox_specs()`, `DockerSandboxSpecServiceInjector` (image pulling) |
| `utils/docker_utils.py` | `replace_localhost_hostname_for_docker()` — rewrites `localhost` → `host.docker.internal` when inside a container |

### Class hierarchy

```
SandboxService (ABC)
  └─ ManagedSandboxService (ABC) — owns the DB row, lifecycle hooks
       └─ DockerSandboxService    — talks to `docker.DockerClient`

SandboxServiceInjector (DiscriminatedUnionMixin, Injector)
  └─ ManagedSandboxServiceInjector — adds lifecycle settings
       └─ DockerSandboxServiceInjector — Pydantic fields for ports, mounts, etc.
```

The `DiscriminatedUnionMixin` uses `__class__.__name__` as the `kind` tag, so
`PodmanSandboxServiceInjector` and `PodmanSandboxSpecServiceInjector` are
automatically registered as new union members just by existing — no manual
registry.

### External integration points (files that hardcode Docker assumptions)

| Location | What it does | Podman impact |
|----------|-------------|---------------|
| `sandbox_store.py:26` | `DOCKER_BACKEND = 'docker'` | Need `PODMAN_BACKEND = 'podman'` |
| `config.py:355-425` | Wires `DockerSandboxServiceInjector` when `RUNTIME` is unset | Need a `RUNTIME=podman` branch |
| `live_status_app_conversation_service.py:4290` | `isinstance(sandbox_service, DockerSandboxService)` for `host.docker.internal` fallback | Need to also check `PodmanSandboxService`, or use a shared protocol |
| `docker_utils.py` | `replace_localhost_hostname_for_docker()` uses `host.docker.internal` | Podman uses `host.containers.internal` by default |
| `docker_sandbox_service.py:479` | Hardcodes `http://host.docker.internal:{self.host_port}` in webhook + LLM refresh URLs | Need `host.containers.internal` for Podman |

---

## 2. Key Docker → Podman Differences

| Concern | Docker | Podman |
|---------|--------|--------|
| **Architecture** | Long-running `dockerd` daemon | Daemonless; `podman system service` starts a compatibility REST API on demand |
| **Socket path** | `/var/run/docker.sock` | `/run/podman/podman.sock` (rootful) or `$XDG_RUNTIME_DIR/podman/podman.sock` (rootless) |
| **Rootless** | Optional (rootless Docker exists but is uncommon) | **Default** — runs as the invoking user |
| **Host alias** | `host.docker.internal` (with `extra_hosts: host-gateway`) | `host.containers.internal` (built-in, no `extra_hosts` needed in rootless) |
| **`extra_hosts` host-gateway** | Supported | Supported in Podman ≥ 4.1; older versions need explicit IP |
| **Port binding < 1024** | Works (daemon runs as root) | Fails in rootless unless `sysctl net.ipv4.ip_unprivileged_port_start` is lowered |
| **`init=True`** | Uses `tini` | Uses `catatonit` (both provide PID 1 reaping) |
| **Container status strings** | `running`, `paused`, `exited`, `created`, `restarting`, `removing`, `dead` | **Same** — Podman's Docker-compat API returns identical strings |
| **Image format** | OCI / Docker image format | OCI / Docker image format (fully compatible) |
| **Network modes** | `host`, `bridge`, `none`, `container:` | `host`, `bridge` (`podman`), `none`, `container:`, `slirp4netns` (rootless default) |
| **Python SDK** | `docker` (docker-py) | `docker` works against compat API, or `podman` PyPI package for native API |

### The Docker-compatible API (the key enabler)

Podman ships a Docker-compatible endpoint. Start it with:

```bash
# Rootful
podman system service --time=0 unix:///run/podman/podman.sock

# Rootless (per-user)
podman system service --time=0

# The socket is then at:
#   $XDG_RUNTIME_DIR/podman/podman.sock
#   (typically /run/user/$(id -u)/podman/podman.sock)
```

Point `docker-py` at it:

```bash
export DOCKER_HOST=unix:///run/user/$(id -u)/podman/podman.sock
```

And `docker.from_env()` connects to Podman. The `containers.list()`, `.get()`,
`.run()`, `.stop()`, `.start()`, `.unpause()`, `.remove()` calls all work.
This is the foundation of the proposed design.

---

## 3. Proposed File Structure

```
sandbox/
  podman_sandbox_service.py          ← PodmanSandboxService + Injector
  podman_sandbox_spec_service.py     ← get_podman_client() + Spec Injector
  docker_sandbox_service.py          ← (modified: extract shared helpers)
  docker_sandbox_spec_service.py     ← (unchanged)
  sandbox_store.py                   ← (modified: add PODMAN_BACKEND)
utils/
  docker_utils.py                    ← (modified: generalize hostname replacement)
  podman_utils.py                    ← (new: podman-specific hostname replacement)
```

---

## 4. Code: PodmanSandboxService

### `sandbox/podman_sandbox_service.py`

```python
import os
from dataclasses import dataclass
from typing import ClassVar

from openhands.app_server.sandbox.docker_sandbox_service import (
    DockerSandboxService,
    ExposedPort,
    VolumeMount,
)
from openhands.app_server.sandbox.docker_sandbox_spec_service import (
    get_podman_client,
)
from openhands.app_server.sandbox.managed_sandbox_service import (
    ManagedSandboxServiceInjector,
)
from openhands.app_server.sandbox.sandbox_store import PODMAN_BACKEND
from openhands.app_server.utils.podman_utils import (
    replace_localhost_hostname_for_podman,
)

# Podman rootless containers resolve this to reach the host. It is
# built into the rootless network stack — no extra_hosts entry needed.
PODMAN_HOST_GATEWAY = 'host.containers.internal'


@dataclass
class PodmanSandboxService(DockerSandboxService):
    """Sandbox service built on Podman via its Docker-compatible API.

    Podman exposes a Docker-compatible REST API (``podman system service``).
    The ``docker`` Python SDK talks to it when ``DOCKER_HOST`` points at the
    Podman socket. This subclass therefore reuses every method of
    ``DockerSandboxService`` and only overrides:

    * ``backend`` — so DB rows are tagged ``podman``, not ``docker``.
    * The client factory — connects to the Podman socket, not Docker's.
    * The webhook / LLM-refresh URLs — use ``host.containers.internal``
      instead of ``host.docker.internal``.
    """

    backend: ClassVar[str] = PODMAN_BACKEND

    # Override the default extra_hosts: Podman rootless networking already
    # resolves host.containers.internal, so we don't need host-gateway.
    # We keep the field for explicit overrides (e.g. LAN deployments).
    extra_hosts: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self):
        # If the caller didn't supply extra_hosts, use Podman's convention.
        # In rootless mode, host.containers.internal is built-in, so this
        # is a no-op safety net for rootful Podman where it may need to be
        # explicitly mapped.
        if self.extra_hosts is None:
            self.extra_hosts = {PODMAN_HOST_GATEWAY: 'host-gateway'}

    def _sandbox_url(self, sandbox_id: str, port: int) -> str:
        return self.container_url_pattern.format(port=port, sandbox_id=sandbox_id)

    # DockerSandboxService.start_sandbox hardcodes host.docker.internal in the
    # webhook callback and LLM key refresh URLs. We override to inject the
    # Podman host alias instead.
    #
    # The parent's start_sandbox builds these URLs inline:
    #   http://host.docker.internal:{self.host_port}/api/v1/webhooks
    #   http://host.docker.internal:{self.host_port}/api/keys/llm/managed/current
    #
    # Rather than copy the entire method, we use a small wrapper that patches
    # the host alias for this sandbox's lifecycle. The cleanest approach is
    # to override start_sandbox and post-process the env_vars, but since the
    # URLs are baked into env_vars inside the parent method, we take the
    # approach of overriding the _host_gateway property used to build them.
    #
    # See the _host_gateway property below.
    @property
    def _host_gateway(self) -> str:
        """The hostname sandboxes use to reach back to this server.

        Docker uses ``host.docker.internal`` (mapped via extra_hosts
        host-gateway). Podman rootless networking provides
        ``host.containers.internal`` built-in.
        """
        return PODMAN_HOST_GATEWAY


class PodmanSandboxServiceInjector(ManagedSandboxServiceInjector):
    """Dependency injector for Podman sandbox services."""

    backend: ClassVar[str] = PODMAN_BACKEND

    container_url_pattern: str = 'http://localhost:{port}'
    host_port: int = 3000
    container_name_prefix: str = 'oh-agent-server-'
    max_num_sandboxes: int = 5
    mounts: list[VolumeMount] = []
    exposed_ports: list[ExposedPort] = []  # inherits Docker defaults
    health_check_path: str | None = '/health'
    extra_hosts: dict[str, str] = {PODMAN_HOST_GATEWAY: 'host-gateway'}
    startup_grace_seconds: int = 15
    use_host_network: bool = False
    network: str | None = None
    kvm_enabled: bool = False

    async def inject(self, state, request=None):
        from openhands.app_server.config import (
            get_db_session,
            get_global_config,
            get_httpx_client,
            get_sandbox_spec_service,
            get_user_context,
        )

        config = get_global_config()
        async with (
            get_user_context(state, request) as user_context,
            get_httpx_client(state) as httpx_client,
            get_sandbox_spec_service(state) as sandbox_spec_service,
            get_db_session(state, request) as db_session,
        ):
            yield PodmanSandboxService(
                sandbox_spec_service=sandbox_spec_service,
                container_name_prefix=self.container_name_prefix,
                host_port=self.host_port,
                container_url_pattern=self.container_url_pattern,
                mounts=self.mounts,
                exposed_ports=self.exposed_ports,
                health_check_path=self.health_check_path,
                httpx_client=httpx_client,
                max_num_sandboxes=self.max_num_sandboxes,
                user_context=user_context,
                db_session=db_session,
                web_url=config.web_url,
                permitted_cors_origins=config.permitted_cors_origins,
                extra_hosts=self.extra_hosts,
                startup_grace_seconds=self.startup_grace_seconds,
                use_host_network=self.use_host_network,
                network=self.network,
                kvm_enabled=self.kvm_enabled,
                lifecycle=self.lifecycle,
                docker_client=get_podman_client(),
            )
```

### `sandbox/podman_sandbox_spec_service.py`

```python
import asyncio
import logging
import os
from typing import AsyncGenerator

import docker
from docker.context import ContextAPI
from fastapi import Request
from pydantic import Field

from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.preset_sandbox_spec_service import (
    PresetSandboxSpecService,
)
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.sandbox.sandbox_spec_service import (
    SandboxSpecService,
    SandboxSpecServiceInjector,
    get_agent_server_env,
    get_agent_server_image,
)
from openhands.app_server.services.injector import InjectorState

_global_podman_client: docker.DockerClient | None = None
_logger = logging.getLogger(__name__)


def _connect_to_podman() -> docker.DockerClient:
    """Connect to the Podman Docker-compatible API.

    Resolution order:
    1. ``DOCKER_HOST`` env var (explicit, user-controlled).
    2. ``PODMAN_SOCKET`` env var (convenience, Podman-specific).
    3. Rootless socket at ``$XDG_RUNTIME_DIR/podman/podman.sock``.
    4. Rootful socket at ``/run/podman/podman.sock``.
    5. Fall back to ``docker.from_env()`` (picks up DOCKER_HOST if set
       elsewhere, or the Docker socket if Podman isn't running).
    """
    failures = []

    # 1 & 2: explicit env var
    explicit = os.environ.get('DOCKER_HOST') or os.environ.get('PODMAN_SOCKET')
    if explicit:
        try:
            return docker.DockerClient(base_url=explicit)
        except docker.errors.DockerException as exc:
            failures.append(f'explicit socket {explicit}: {exc}')

    # 3: rootless socket
    xdg_runtime = os.environ.get('XDG_RUNTIME_DIR')
    if xdg_runtime:
        rootless_socket = f'unix://{xdg_runtime}/podman/podman.sock'
        try:
            client = docker.DockerClient(base_url=rootless_socket)
            # Verify the connection is actually Podman
            client.ping()
            return client
        except Exception as exc:
            failures.append(f'rootless socket {rootless_socket}: {exc}')

    # 4: rootful socket
    rootful_socket = 'unix:///run/podman/podman.sock'
    try:
        client = docker.DockerClient(base_url=rootful_socket)
        client.ping()
        return client
    except Exception as exc:
        failures.append(f'rootful socket {rootful_socket}: {exc}')

    # 5: last resort
    try:
        return docker.from_env()
    except docker.errors.DockerException as exc:
        failures.append(f'docker.from_env(): {exc}')

    raise SandboxError(
        'Could not connect to the Podman daemon ('
        + '; '.join(failures)
        + '). Check that Podman is running and the compatibility API is '
        'started (e.g. `podman system service --time=0`), or set '
        'DOCKER_HOST to the socket path.'
    )


def get_podman_client() -> docker.DockerClient:
    global _global_podman_client
    if _global_podman_client is None:
        _global_podman_client = _connect_to_podman()
    return _global_podman_client


def get_default_podman_sandbox_specs():
    return [
        SandboxSpecInfo(
            id=get_agent_server_image(),
            command=['--port', '8000'],
            initial_env={
                'OPENVSCODE_SERVER_ROOT': '/openhands/.openvscode-server',
                'OH_ENABLE_VNC': '0',
                'LOG_JSON': 'true',
                'OH_CONVERSATIONS_PATH': '/workspace/conversations',
                'OH_BASH_EVENTS_DIR': '/workspace/bash_events',
                'PYTHONUNBUFFERED': '1',
                'ENV_LOG_LEVEL': '20',
                **get_agent_server_env(),
            },
            working_dir='/workspace/project',
        )
    ]


class PodmanSandboxSpecServiceInjector(SandboxSpecServiceInjector):
    specs: list[SandboxSpecInfo] = Field(
        default_factory=get_default_podman_sandbox_specs,
        description='Preset list of sandbox specs',
    )

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[SandboxSpecService, None]:
        yield PresetSandboxSpecService(specs=self.specs)

    async def prepare(self) -> None:
        """Pull the spec images that are not on the Podman host."""
        await asyncio.gather(
            *[self.pull_spec_if_missing(spec) for spec in self.specs]
        )

    async def pull_spec_if_missing(self, spec: SandboxSpecInfo):
        _logger.debug(f'Checking Podman Image: {spec.id}')
        try:
            podman_client = get_podman_client()
            try:
                podman_client.images.get(spec.id)
            except docker.errors.ImageNotFound:
                _logger.info(f'⬇️  Pulling Podman Image: {spec.id}')
                await self._pull_with_progress_logging(podman_client, spec.id)
                _logger.info(f'⬇️  Finished Pulling Podman Image: {spec.id}')
        except docker.errors.APIError as exc:
            raise SandboxError(f'Error Getting Podman Image: {spec.id}') from exc

    async def _pull_with_progress_logging(
        self, podman_client: docker.DockerClient, image_id: str
    ):
        pull_complete = asyncio.Event()

        async def periodic_logger():
            while not pull_complete.is_set():
                try:
                    await asyncio.wait_for(pull_complete.wait(), timeout=5.0)
                    break
                except asyncio.TimeoutError:
                    _logger.info(f'🔄 Downloading Podman Image: {image_id}...')

        async def pull_image():
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, podman_client.images.pull, image_id
                )
            finally:
                pull_complete.set()

        logger_task = asyncio.create_task(periodic_logger())
        pull_task = asyncio.create_task(pull_image())

        try:
            await pull_task
        finally:
            if not logger_task.done():
                logger_task.cancel()
                try:
                    await logger_task
                except asyncio.CancelledError:
                    pass
```

### `utils/podman_utils.py`

```python
from urllib.parse import urlparse, urlunparse

from openhands.app_server.utils.environment import is_running_in_container


def replace_localhost_hostname_for_podman(
    url: str, replacement: str = 'host.containers.internal'
) -> str:
    """Replace localhost hostname in URL with host.containers.internal when
    running inside a Podman container.

    Podman rootless networking resolves ``host.containers.internal`` to the
    host automatically, so no ``extra_hosts`` mapping is needed.
    """
    if not is_running_in_container():
        return url
    parsed = urlparse(url)
    if parsed.hostname == 'localhost':
        netloc = parsed.netloc.replace('localhost', replacement, 1)
        return urlunparse(parsed._replace(netloc=netloc))
    return url
```

---

## 5. Required Modifications to Existing Files

### `sandbox_store.py` — add backend constant

```python
REMOTE_BACKEND = 'remote'
DOCKER_BACKEND = 'docker'
PODMAN_BACKEND = 'podman'          # ← ADD
E2B_BACKEND = 'e2b'
K8S_AGENT_SANDBOX_BACKEND = 'k8s-agent-sandbox'
```

### `config.py` — add `RUNTIME=podman` branch

```python
# In get_config(), the sandbox injector section:
if config.sandbox is None:
    if os.getenv('RUNTIME') == 'remote':
        config.sandbox = RemoteSandboxServiceInjector(...)
    elif os.getenv('RUNTIME') == 'e2b':
        config.sandbox = E2BSandboxServiceInjector()
    elif os.getenv('RUNTIME') == 'k8s-agent-sandbox':
        config.sandbox = K8sAgentSandboxServiceInjector()
    elif os.getenv('RUNTIME') in ('local', 'process'):
        config.sandbox = ProcessSandboxServiceInjector()
    elif os.getenv('RUNTIME') == 'podman':       # ← ADD THIS BRANCH
        from openhands.app_server.sandbox.podman_sandbox_service import (
            PodmanSandboxServiceInjector,
        )
        config.sandbox = PodmanSandboxServiceInjector()
    else:
        # ... existing Docker fallback ...

# And in the sandbox_spec section:
if config.sandbox_spec is None:
    if os.getenv('RUNTIME') == 'remote':
        ...
    elif os.getenv('RUNTIME') == 'podman':       # ← ADD THIS BRANCH
        from openhands.app_server.sandbox.podman_sandbox_spec_service import (
            PodmanSandboxSpecServiceInjector,
        )
        config.sandbox_spec = PodmanSandboxSpecServiceInjector()
    else:
        ...
```

### `docker_sandbox_service.py` — extract `_host_gateway` for override

The parent `DockerSandboxService.start_sandbox()` hardcodes
`http://host.docker.internal:{self.host_port}` in two places (lines 479 and
486). To make this overridable without duplicating the entire method, extract
a property:

```python
# In DockerSandboxService:
@property
def _host_gateway(self) -> str:
    """The hostname sandboxes use to reach back to this server."""
    return 'host.docker.internal'

# Then in start_sandbox, replace the hardcoded strings:
env_vars[WEBHOOK_CALLBACK_VARIABLE] = (
    f'http://{self._host_gateway}:{self.host_port}/api/v1/webhooks'
)
env_vars[LLM_API_KEY_REFRESH_URL_VARIABLE] = (
    f'http://{self._host_gateway}:{self.host_port}/api/keys/llm/managed/current'
)
```

`PodmanSandboxService` then overrides `_host_gateway` to return
`'host.containers.internal'`.

### `live_status_app_conversation_service.py` — generalize the isinstance check

```python
# Line ~4290, change:
if web_url is None and isinstance(sandbox_service, DockerSandboxService):
    web_url = f'http://host.docker.internal:{sandbox_service.host_port}'

# To:
if web_url is None and isinstance(sandbox_service, (DockerSandboxService, PodmanSandboxService)):
    host_alias = (
        'host.containers.internal'
        if isinstance(sandbox_service, PodmanSandboxService)
        else 'host.docker.internal'
    )
    web_url = f'http://{host_alias}:{sandbox_service.host_port}'
```

Or better, add a `_host_gateway` property to the base and use that:

```python
if web_url is None and hasattr(sandbox_service, '_host_gateway'):
    web_url = f'http://{sandbox_service._host_gateway}:{sandbox_service.host_port}'
```

### `utils/environment.py` — generalize container detection

The existing `is_running_in_docker()` already checks for
`/run/.containerenv` (a Podman marker), so it works for Podman. Rename or
alias it:

```python
@lru_cache(maxsize=1)
def is_running_in_container() -> bool:
    """Best-effort detection for Docker or Podman containers."""
    # /.dockerenv → Docker, /run/.containerenv → Podman
    ...

# Keep the old name as an alias for backward compatibility
is_running_in_docker = is_running_in_container
```

---

## 6. Design Decisions & Trade-offs

### Why subclass DockerSandboxService instead of implementing from scratch?

| Approach | Pros | Cons |
|----------|------|------|
| **Subclass** (proposed) | Zero duplicated logic; inherits all edge-case handling (port collision, health check grace, VSCode URL building, lifecycle state machine); trivial to maintain | Tightly coupled to DockerSandboxService's internal API; if DockerSandboxService changes, PodmanSandboxService must follow |
| Fresh implementation | Clean separation; can use Podman-native features | ~400 lines of duplicated logic; high maintenance burden; risk of behavioral drift |
| Shared base class | Clean factoring of common logic | Requires refactoring DockerSandboxService to extract the shared base; larger PR |

**Recommendation:** Start with the subclass. If Podman-specific needs grow
(e.g., Podman pods, rootless port mapping quirks), extract a shared base class
later.

### Why use `docker-py` instead of the `podman` Python package?

- `docker-py` is already a dependency — no new package.
- Podman's Docker-compatible API covers 100% of what `DockerSandboxService`
  uses (list, get, run, stop, start, unpause, remove, images.get, images.pull).
- The `podman` PyPI package uses Podman's native API, which has different
  method signatures and return types — it would require rewriting every method.
- If a Podman-specific feature is ever needed that the compat API doesn't
  expose, the native package can be added as an *optional* dependency for that
  one feature.

### Why `host.containers.internal` instead of `host.docker.internal`?

- `host.containers.internal` is Podman's native convention, available in
  rootless mode without any `extra_hosts` configuration.
- `host.docker.internal` works in Podman ≥ 4.1 when `extra_hosts` includes
  `host-gateway`, but relying on it couples the Podman deployment to Docker's
  naming convention.
- Using the native name makes the Podman backend self-documenting.

---

## 7. Rootless Podman Considerations

Rootless Podman is the default and most common deployment mode. Key impacts:

### Port binding
Rootless Podman cannot bind to ports < 1024 by default. The agent server
defaults to port 8000 (fine), but if `host_port` is set to 80 or 443, it will
fail. Document this, or add a startup check.

### Socket discovery
The rootless socket lives at `$XDG_RUNTIME_DIR/podman/podman.sock`, which
varies per user. The `_connect_to_podman()` function handles this with the
resolution order described above.

### Volume mounts
Rootless Podman maps UIDs. A bind mount from the host may have permission
issues because the container's root (UID 0) maps to the host user. The
`mounts` configuration should document this, or use `:Z` / `:z` SELinux
labels.

### `pause` vs `stop`
Podman rootless containers can be paused (`podman pause`) or stopped
(`podman stop`). The Docker-compatible API's `.stop()` and `.unpause()`
work identically. No change needed.

---

## 8. Testing Strategy

The existing Docker test suite (`test_docker_sandbox_service.py`) mocks the
`docker.DockerClient` and tests the service logic. Since `PodmanSandboxService`
inherits from `DockerSandboxService`, the same tests apply. Additional tests:

1. **`test_podman_sandbox_service.py`** — Tests that:
   - `backend == 'podman'`
   - `_host_gateway` returns `host.containers.internal`
   - Webhook and LLM refresh URLs use `host.containers.internal`
   - `extra_hosts` defaults to `{host.containers.internal: host-gateway}`

2. **`test_podman_sandbox_spec_service.py`** — Tests that:
   - `_connect_to_podman()` tries the rootless socket, then rootful
   - Image pulling uses the podman client

3. **Integration test** (requires Podman installed):
   - Start `podman system service`
   - Create a sandbox, verify it runs, pause/resume/delete

---

## 9. Migration Path for Existing Deployments

A deployment currently using Docker can switch to Podman by:

1. Install Podman: `dnf install podman` (RHEL/Fedora) or
   `apt install podman` (Debian/Ubuntu)
2. Start the compatibility API:
   ```bash
   # Systemd service (rootful)
   systemctl enable --now podman.socket

   # Or rootless (per-user, linger required)
   loginctl enable-linger $USER
   systemctl --user enable --now podman.socket
   ```
3. Set `RUNTIME=podman`
4. Optionally set `DOCKER_HOST` if the socket is in a non-standard location
5. Restart OpenHands

Existing `docker` backend sandboxes in the DB will remain under `backend='docker'`
and will not be visible to the Podman backend (the `backend` column isolates
them). This is the same behavior as switching between any two backends.
