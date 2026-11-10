from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from server.licensing import service as service_module
from server.licensing.config import LicenseConfig
from server.licensing.limits import (
    OrgLimitReachedError,
    UserLimitReachedError,
    check_can_add_org,
    check_can_add_user,
    count_orgs,
    count_users,
)
from server.licensing.service import LicenseService
from storage.user import ACCOUNT_TYPE_SERVICE
from tests.unit.license_signing import LICENSE_KEY, NOW, Signer, valid_payload


async def _install(monkeypatch, signer: Signer | None = None, **config) -> None:
    """Install a license service, and check a license with the given answer."""
    answer = config.pop('answer', None)

    def handler(request: httpx.Request) -> httpx.Response:
        assert signer is not None
        return httpx.Response(200, json=signer.sign(answer))

    values = {
        'enforcement_enabled': True,
        'license_key': LICENSE_KEY,
        'license_server_url': 'https://cc.example',
        'check_interval_seconds': 1800,
    }
    values.update(config)
    service = LicenseService(
        LicenseConfig(**values),
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
        clock=lambda: NOW,
    )
    if answer is not None:
        await service.check_now()
    monkeypatch.setattr(service_module, '_license_service', service)


@pytest.fixture
def db(async_session_maker):
    with patch('server.licensing.limits.a_session_maker', async_session_maker):
        yield async_session_maker


async def test_unlicensed_install_allows_one_user(monkeypatch, db, create_user):
    await _install(monkeypatch, license_key=None)

    await check_can_add_user()

    create_user()
    with pytest.raises(UserLimitReachedError) as exc:
        await check_can_add_user()
    assert exc.value.limit == 1
    assert str(exc.value) == "You've reached your license's user limit."


async def test_license_limit_blocks_the_next_user(monkeypatch, db, create_user, signer):
    await _install(
        monkeypatch,
        signer,
        answer=valid_payload(limits={'users': 3, 'conversations': None}),
    )
    for _ in range(2):
        create_user()

    await check_can_add_user()

    create_user()
    with pytest.raises(UserLimitReachedError) as exc:
        await check_can_add_user()
    assert exc.value.limit == 3


async def test_an_install_over_its_limit_keeps_its_users(monkeypatch, db, create_user):
    """A smaller license only stops new users; it never removes any."""
    for _ in range(3):
        create_user()
    await _install(monkeypatch, license_key=None)

    with pytest.raises(UserLimitReachedError):
        await check_can_add_user()
    async with db() as session:
        assert await count_users(session) == 3


async def test_unlimited_license_has_no_limit(monkeypatch, db, create_user, signer):
    await _install(monkeypatch, signer, answer=valid_payload(limits={'users': None}))
    for _ in range(3):
        create_user()

    await check_can_add_user()


@pytest.mark.parametrize('config', [{'enforcement_enabled': False}, {}])
async def test_no_limit_while_enforcement_is_off_or_pending(
    monkeypatch, db, create_user, config
):
    # With no answer, the service has not checked yet, so its state is pending.
    await _install(monkeypatch, **config)
    for _ in range(3):
        create_user()

    await check_can_add_user()


async def test_disabled_users_and_service_accounts_take_no_seat(
    async_session_maker, create_user
):
    create_user()
    create_user(is_disabled=True)
    create_user(deletion_pending=True)
    create_user(account_type=ACCOUNT_TYPE_SERVICE)

    async with async_session_maker() as session:
        assert await count_users(session) == 1


async def test_personal_workspaces_are_not_counted_as_orgs(
    async_session_maker, create_user, create_org
):
    personal = create_org()
    create_user(id=personal.id, current_org_id=personal.id)
    create_org()

    async with async_session_maker() as session:
        assert await count_orgs(session) == 1


async def test_unlicensed_install_allows_one_team_org(
    monkeypatch, db, create_user, create_org
):
    await _install(monkeypatch, license_key=None)
    # A personal workspace shares its user's id and doesn't count.
    personal = create_org()
    create_user(id=personal.id, current_org_id=personal.id)

    await check_can_add_org()

    create_org()
    with pytest.raises(OrgLimitReachedError) as exc:
        await check_can_add_org()
    assert exc.value.limit == 1
    assert str(exc.value) == "You've reached your license's organization limit."


async def test_an_install_over_its_org_limit_keeps_its_orgs(
    monkeypatch, db, create_org
):
    for _ in range(3):
        create_org()
    await _install(monkeypatch, license_key=None)

    with pytest.raises(OrgLimitReachedError):
        await check_can_add_org()
    async with db() as session:
        assert await count_orgs(session) == 3


async def test_license_org_limit(monkeypatch, db, create_org, signer):
    await _install(
        monkeypatch, signer, answer=valid_payload(limits={'users': 10, 'orgs': 2})
    )
    create_org()

    await check_can_add_org()

    create_org()
    with pytest.raises(OrgLimitReachedError):
        await check_can_add_org()


async def test_unlimited_orgs(monkeypatch, db, create_org, signer):
    await _install(monkeypatch, signer, answer=valid_payload(limits={'orgs': None}))
    for _ in range(3):
        create_org()

    await check_can_add_org()
