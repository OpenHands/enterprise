"""CLI `set-key` validates --models against the org's stored catalog before writing."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from server.customer_gateway import cli
from storage.customer_gateway_store import OrgGateway

ORG = uuid4()
USER = uuid4()
PROFILE = {
    'base_url': 'https://relay.example.com',
    'models': {'alias-a': 'upstream-a', 'alias-b': 'upstream-b'},
}
GW = OrgGateway(ORG, 'gw-1', PROFILE)


def _stdin(monkeypatch, key='secret-key\n'):
    class FakeStdin:
        def readline(self):
            return key

    monkeypatch.setattr(cli.sys, 'stdin', FakeStdin())


async def _run(monkeypatch, models_arg, *, key='secret-key\n', get_org_gateway=None):
    _stdin(monkeypatch, key)
    set_member_credential = AsyncMock(return_value=1)
    with (
        patch.object(
            cli.CustomerGatewayStore,
            'get_org_gateway',
            get_org_gateway or AsyncMock(return_value=GW),
        ),
        patch.object(
            cli.CustomerGatewayStore, 'set_member_credential', set_member_credential
        ),
    ):
        rc = await cli.main(
            [
                'set-key',
                '--org',
                str(ORG),
                '--user',
                str(USER),
                '--models',
                models_arg,
            ]
        )
    return rc, set_member_credential


async def test_set_key_strips_and_accepts_known_aliases(monkeypatch):
    rc, set_member_credential = await _run(monkeypatch, 'alias-a, alias-b')
    assert rc == 0
    set_member_credential.assert_awaited_once_with(
        ORG, USER, 'secret-key', ['alias-a', 'alias-b'], cli._actor()
    )


async def test_set_key_rejects_empty_entry(monkeypatch):
    with pytest.raises(SystemExit):
        await _run(monkeypatch, 'alias-a, ,alias-b')


async def test_set_key_rejects_blank_models(monkeypatch):
    with pytest.raises(SystemExit):
        await _run(monkeypatch, '')


async def test_set_key_rejects_unknown_alias(monkeypatch):
    with pytest.raises(SystemExit, match='alias-typo'):
        await _run(monkeypatch, 'alias-a,alias-typo')


async def test_set_key_rejects_when_no_org_config(monkeypatch):
    with pytest.raises(SystemExit, match='no gateway config'):
        await _run(
            monkeypatch,
            'alias-a',
            get_org_gateway=AsyncMock(return_value=None),
        )
