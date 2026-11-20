"""Tests for SubstrateControl, the ate-api-server client.

The generated gRPC stub is replaced by a fake that records requests and
raises the status codes ate-api-server answers with.
"""

from unittest.mock import patch

import grpc
import pytest

from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.substrate import ateapi_pb2 as pb
from openhands.app_server.sandbox.substrate import control as control_module
from openhands.app_server.sandbox.substrate.control import SubstrateControl

ATESPACE = 'openhands'


def _rpc_error(code: grpc.StatusCode, details: str = '') -> grpc.aio.AioRpcError:
    return grpc.aio.AioRpcError(
        code, grpc.aio.Metadata(), grpc.aio.Metadata(), details=details
    )


class FakeStub:
    """ControlStub stand in. Each method answers from ``results`` in order."""

    def __init__(self, **results):
        self.results = {name: list(values) for name, values in results.items()}
        self.calls: list[tuple[str, object, dict]] = []

    def __getattr__(self, method: str):
        async def call(request, **kwargs):
            self.calls.append((method, request, kwargs))
            result = self.results[method].pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        return call


def _control(stub: FakeStub, token_file: str | None = None) -> SubstrateControl:
    return SubstrateControl(stub, ATESPACE, token_file=token_file)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_actor_reads_in_the_atespace():
    actor = pb.Actor(metadata=pb.ResourceMetadata(atespace=ATESPACE, name='a'))
    stub = FakeStub(GetActor=[actor])

    assert await _control(stub).get_actor('a') == actor

    request = stub.calls[0][1]
    assert request.actor == pb.ObjectRef(atespace=ATESPACE, name='a')


@pytest.mark.asyncio
async def test_missing_actor_is_none():
    stub = FakeStub(
        GetActor=[_rpc_error(grpc.StatusCode.NOT_FOUND)],
        SuspendActor=[_rpc_error(grpc.StatusCode.NOT_FOUND)],
        ResumeActor=[_rpc_error(grpc.StatusCode.NOT_FOUND)],
        DeleteActor=[_rpc_error(grpc.StatusCode.NOT_FOUND)],
    )
    control = _control(stub)

    assert await control.get_actor('a') is None
    assert await control.suspend_actor('a') is None
    assert await control.resume_actor('a', timeout=5) is None
    await control.delete_actor('a')


@pytest.mark.asyncio
async def test_create_actor_names_its_template():
    stub = FakeStub(CreateActor=[pb.Actor()])

    await _control(stub).create_actor('a', 'openhands-agent-server')

    actor = stub.calls[0][1].actor
    assert actor.metadata == pb.ResourceMetadata(atespace=ATESPACE, name='a')
    assert actor.actor_template == pb.ObjectRef(
        atespace=ATESPACE, name='openhands-agent-server'
    )


@pytest.mark.asyncio
async def test_unknown_template_says_how_to_register_it():
    stub = FakeStub(CreateActor=[_rpc_error(grpc.StatusCode.NOT_FOUND, 'no such')])

    with pytest.raises(SandboxError, match='kubectl ate create actor-template'):
        await _control(stub).create_actor('a', 'missing')


@pytest.mark.asyncio
async def test_egress_policy_is_the_actors_default_policy():
    stub = FakeStub(CreateActorEgressPolicy=[pb.EgressPolicy()])
    rules = [pb.EgressRule(http=pb.HttpRule(hostnames=['*']))]

    await _control(stub).create_egress_policy('a', rules)

    request = stub.calls[0][1]
    assert request.actor == pb.ObjectRef(atespace=ATESPACE, name='a')
    assert request.egress_policy.metadata.name == 'default'
    assert request.egress_policy.metadata.atespace == ATESPACE
    assert list(request.egress_policy.rules) == rules


@pytest.mark.asyncio
async def test_resume_retries_while_every_worker_is_busy():
    actor = pb.Actor(status=pb.ActorStatus(state=pb.ACTOR_STATE_RUNNING))
    stub = FakeStub(
        ResumeActor=[
            _rpc_error(grpc.StatusCode.RESOURCE_EXHAUSTED, 'no worker has room'),
            _rpc_error(grpc.StatusCode.ABORTED),
            pb.ResumeActorResponse(actor=actor, resumed=True),
        ]
    )

    with patch.object(control_module, 'RESUME_RETRY_SECONDS', 0):
        assert await _control(stub).resume_actor('a', timeout=30) == actor

    assert len(stub.calls) == 3


@pytest.mark.asyncio
async def test_resume_gives_up_at_the_deadline():
    stub = FakeStub(
        ResumeActor=[_rpc_error(grpc.StatusCode.RESOURCE_EXHAUSTED, 'no room')]
    )

    with pytest.raises(SandboxError, match='RESOURCE_EXHAUSTED'):
        await _control(stub).resume_actor('a', timeout=0)


@pytest.mark.asyncio
async def test_resume_does_not_retry_a_permanent_failure():
    stub = FakeStub(
        ResumeActor=[_rpc_error(grpc.StatusCode.FAILED_PRECONDITION, 'crashed')]
    )

    with pytest.raises(SandboxError, match='crashed'):
        await _control(stub).resume_actor('a', timeout=30)

    assert len(stub.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('code', 'hint'),
    [
        (grpc.StatusCode.UNAUTHENTICATED, 'SUBSTRATE_API_TOKEN_FILE'),
        (grpc.StatusCode.PERMISSION_DENIED, 'access-policy'),
        (grpc.StatusCode.UNAVAILABLE, 'ate.dev/api-access=true'),
    ],
)
async def test_errors_say_what_to_fix(code, hint):
    stub = FakeStub(GetActor=[_rpc_error(code)])

    with pytest.raises(SandboxError, match=hint):
        await _control(stub).get_actor('a')


@pytest.mark.asyncio
async def test_token_is_read_on_every_call(tmp_path):
    token_file = tmp_path / 'token'
    stub = FakeStub(GetActor=[pb.Actor(), pb.Actor()])
    control = _control(stub, token_file=str(token_file))

    token_file.write_text('first\n')
    await control.get_actor('a')
    token_file.write_text('second\n')
    await control.get_actor('a')

    assert [call[2]['metadata'] for call in stub.calls] == [
        (('authorization', 'Bearer first'),),
        (('authorization', 'Bearer second'),),
    ]


@pytest.mark.asyncio
async def test_connect_checks_the_server_name(tmp_path):
    ca_file = tmp_path / 'ca.crt'
    ca_file.write_bytes(b'')

    with patch.object(control_module.grpc.aio, 'secure_channel') as channel:
        control = SubstrateControl.connect(
            'localhost:18443',
            ATESPACE,
            ca_file=str(ca_file),
            server_name='api.ate-system.svc',
        )

    options = dict(channel.call_args.kwargs['options'])
    assert options['grpc.ssl_target_name_override'] == 'api.ate-system.svc'
    assert options['grpc.lb_policy_name'] == 'round_robin'
    assert control.atespace == ATESPACE
