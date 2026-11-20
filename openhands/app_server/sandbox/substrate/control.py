"""The ate-api-server calls the Substrate sandbox backend makes."""

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import grpc

from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.substrate import ateapi_pb2 as pb
from openhands.app_server.sandbox.substrate.ateapi_pb2_grpc import ControlStub

# The only name Substrate accepts for an actor's egress policy.
EGRESS_POLICY_NAME = 'default'
# ResumeActor answers these while every worker is busy or the actor is still
# moving between states. Substrate's own router retries them.
RETRYABLE_RESUME_CODES = frozenset(
    {
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        grpc.StatusCode.ABORTED,
        grpc.StatusCode.UNAVAILABLE,
    }
)
RESUME_RETRY_SECONDS = 1.0


class SubstrateControl:
    """Actor calls in one atespace, through ate-api-server.

    An actor that is gone reads as None. Every other failure raises
    ``SandboxError``.
    """

    def __init__(
        self,
        stub: ControlStub,
        atespace: str,
        token_file: str | None = None,
        request_timeout: float = 30.0,
        channel: grpc.aio.Channel | None = None,
    ):
        self.stub = stub
        self.atespace = atespace
        self.token_file = token_file
        self.request_timeout = request_timeout
        self._channel = channel

    @classmethod
    def connect(
        cls,
        endpoint: str,
        atespace: str,
        root_certificates: bytes | None = None,
        server_name: str | None = None,
        token_file: str | None = None,
        request_timeout: float = 30.0,
    ) -> 'SubstrateControl':
        """Open a channel to ate-api-server.

        ``server_name`` overrides the name the server certificate is checked
        against, for an endpoint that is not that name, such as a port-forward.
        """
        # The api Service is headless, with one address per replica.
        options: list[tuple[str, str]] = [('grpc.lb_policy_name', 'round_robin')]
        if server_name:
            options.append(('grpc.ssl_target_name_override', server_name))
        channel = grpc.aio.secure_channel(
            endpoint,
            grpc.ssl_channel_credentials(root_certificates=root_certificates),
            options=options,
        )
        return cls(
            ControlStub(channel),
            atespace,
            token_file=token_file,
            request_timeout=request_timeout,
            channel=channel,
        )

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()

    def _metadata(self) -> tuple[tuple[str, str], ...]:
        """The bearer token, read on every call because the kubelet rotates it."""
        if not self.token_file:
            return ()
        token = Path(self.token_file).read_text().strip()
        return (('authorization', f'Bearer {token}'),)

    def _ref(self, name: str) -> pb.ObjectRef:
        return pb.ObjectRef(atespace=self.atespace, name=name)

    @contextmanager
    def _reported(self, action: str) -> Iterator[None]:
        """Raise a failed call as a ``SandboxError`` that says what to fix."""
        try:
            yield
        except grpc.aio.AioRpcError as exc:
            code = exc.code()
            if code == grpc.StatusCode.UNAUTHENTICATED:
                raise SandboxError(
                    f'Substrate rejected the credentials used to {action}. The app '
                    'sends a ServiceAccount token with audience api.ate-system.svc '
                    'from SUBSTRATE_API_TOKEN_FILE.'
                ) from exc
            if code == grpc.StatusCode.PERMISSION_DENIED:
                raise SandboxError(
                    f'Substrate refused to {action} in atespace '
                    f'{self.atespace!r}. Grant the app editor in that atespace '
                    'with kubectl ate create access-policy.'
                ) from exc
            if code == grpc.StatusCode.UNAVAILABLE:
                raise SandboxError(
                    f'Could not {action}: ate-api-server is unreachable '
                    f'({exc.details()}). Check SUBSTRATE_API_ENDPOINT, and that the '
                    "app's namespace is labeled ate.dev/api-access=true."
                ) from exc
            raise SandboxError(
                f'Could not {action}: {code.name} {exc.details()}'
            ) from exc
        except OSError as exc:
            raise SandboxError(f'Could not {action}: {exc}') from exc

    async def get_actor(self, name: str) -> pb.Actor | None:
        with self._reported(f'read actor {name}'):
            try:
                return await self.stub.GetActor(
                    pb.GetActorRequest(actor=self._ref(name)),
                    metadata=self._metadata(),
                    timeout=self.request_timeout,
                )
            except grpc.aio.AioRpcError as exc:
                if exc.code() == grpc.StatusCode.NOT_FOUND:
                    return None
                raise

    async def create_actor(self, name: str, template: str) -> pb.Actor:
        """Create a suspended actor from an ActorTemplate in the atespace."""
        actor = pb.Actor(
            metadata=pb.ResourceMetadata(atespace=self.atespace, name=name),
            actor_template=self._ref(template),
        )
        with self._reported(f'create actor {name} from template {template!r}'):
            try:
                return await self.stub.CreateActor(
                    pb.CreateActorRequest(actor=actor),
                    metadata=self._metadata(),
                    timeout=self.request_timeout,
                )
            except grpc.aio.AioRpcError as exc:
                if exc.code() in (
                    grpc.StatusCode.NOT_FOUND,
                    grpc.StatusCode.FAILED_PRECONDITION,
                ):
                    raise SandboxError(
                        f'ActorTemplate {template!r} cannot be used in atespace '
                        f'{self.atespace!r} ({exc.details()}). The operator registers '
                        'it with kubectl ate create actor-template.'
                    ) from exc
                raise

    async def create_egress_policy(self, name: str, rules: list[pb.EgressRule]) -> None:
        """Give an actor its egress policy. An actor without one has no egress."""
        policy = pb.EgressPolicy(
            metadata=pb.ResourceMetadata(
                atespace=self.atespace, name=EGRESS_POLICY_NAME
            ),
            rules=rules,
        )
        with self._reported(f'create the egress policy of actor {name}'):
            await self.stub.CreateActorEgressPolicy(
                pb.CreateActorEgressPolicyRequest(
                    actor=self._ref(name), egress_policy=policy
                ),
                metadata=self._metadata(),
                timeout=self.request_timeout,
            )

    async def resume_actor(self, name: str, timeout: float) -> pb.Actor | None:
        """Resume an actor and wait until it serves.

        ResumeActor returns once the template's wakeup probe answers. While
        every worker is busy it fails with a retryable code, so this retries
        until ``timeout``. Returns None when the actor is gone.
        """
        deadline = time.monotonic() + timeout
        with self._reported(f'resume actor {name}'):
            while True:
                remaining = deadline - time.monotonic()
                try:
                    response = await self.stub.ResumeActor(
                        pb.ResumeActorRequest(actor=self._ref(name)),
                        metadata=self._metadata(),
                        timeout=max(remaining, 1.0),
                    )
                    return response.actor
                except grpc.aio.AioRpcError as exc:
                    if exc.code() == grpc.StatusCode.NOT_FOUND:
                        return None
                    if (
                        exc.code() not in RETRYABLE_RESUME_CODES
                        or time.monotonic() + RESUME_RETRY_SECONDS >= deadline
                    ):
                        raise
                await asyncio.sleep(RESUME_RETRY_SECONDS)

    async def suspend_actor(self, name: str) -> pb.Actor | None:
        """Snapshot a running actor and release its worker.

        Returns once the snapshot is on the node; the upload to object storage
        continues in the background. Returns None when the actor is gone.
        """
        with self._reported(f'suspend actor {name}'):
            try:
                response = await self.stub.SuspendActor(
                    pb.SuspendActorRequest(actor=self._ref(name)),
                    metadata=self._metadata(),
                    timeout=self.request_timeout,
                )
            except grpc.aio.AioRpcError as exc:
                if exc.code() == grpc.StatusCode.NOT_FOUND:
                    return None
                raise
        return response.actor

    async def delete_actor(self, name: str) -> None:
        """Delete an actor, stopping it first if it runs. A missing one is not an error."""
        with self._reported(f'delete actor {name}'):
            try:
                await self.stub.DeleteActor(
                    pb.DeleteActorRequest(actor=self._ref(name)),
                    metadata=self._metadata(),
                    timeout=self.request_timeout,
                )
            except grpc.aio.AioRpcError as exc:
                if exc.code() != grpc.StatusCode.NOT_FOUND:
                    raise
