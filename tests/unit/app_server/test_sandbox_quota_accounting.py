"""Exercise sandbox quota accounting against the migrated PostgreSQL schema.

The RemoteSandboxService is constructed with stubbed spec/HTTP layers: quota
reservation, release, and counting all use production code
(``DailySandboxQuotaService``). Conversation starts are not involved: the
metered unit is the sandbox.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException

from openhands.app_server.config_api.config_models import AppMode
from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.remote_sandbox_service import RemoteSandboxService
from openhands.app_server.sandbox.sandbox_models import SandboxStatus
from openhands.app_server.shared import server_config
from openhands.app_server.user.user_models import UserInfo
from server.services.daily_sandbox_quota_service import DailySandboxQuotaService
from storage.user import User


@dataclass
class QuotaUserContext:
    user_id: str | None
    org_id: UUID

    async def get_user_id(self):
        return self.user_id

    async def get_effective_org_id(self):
        return self.org_id

    async def get_user_info(self, **kwargs):
        return UserInfo(id=self.user_id)

    async def get_default_sandbox_spec_id(self):
        return None


@pytest.fixture
async def sandbox_quota(app_db_session, async_session_maker, create_user, monkeypatch):
    user = create_user(daily_sandbox_limit=3)
    user_context = QuotaUserContext(str(user.id), user.current_org_id)
    monkeypatch.setattr(server_config, 'app_mode', AppMode.SAAS)

    async def start(outcome='running', user_context_override=None):
        spec_service = AsyncMock()
        spec = MagicMock()
        spec.id = 'spec-1'
        spec_service.get_default_sandbox_spec.return_value = spec
        spec_service.get_sandbox_spec.return_value = spec

        db_session = AsyncMock()
        db_session.add = MagicMock()

        service = RemoteSandboxService(
            sandbox_spec_service=spec_service,
            api_url='https://api.example.com',
            api_key='test-api-key',
            web_url='https://web.example.com',
            resource_factor=1,
            runtime_class='gvisor',
            start_sandbox_timeout=120,
            max_num_sandboxes=10,
            user_context=user_context_override or user_context,
            httpx_client=AsyncMock(),
            db_session=db_session,
        )
        service.pause_old_sandboxes = AsyncMock(return_value=[])
        service._init_environment = AsyncMock(return_value={})
        service.build_sandbox_start_request = AsyncMock(return_value={})
        if outcome == 'http-error':
            service._send_runtime_api_request = AsyncMock(
                side_effect=httpx.HTTPError('boom')
            )
        else:
            response = MagicMock()
            response.json.return_value = {
                'session_id': 'sb-1',
                'status': outcome,
                'url': 'https://sandbox.example.com',
                'session_api_key': 'key',
                'runtime_id': 'runtime-1',
            }
            response.raise_for_status = MagicMock()
            service._send_runtime_api_request = AsyncMock(return_value=response)
        return await service.start_sandbox()

    async def used_today():
        async with async_session_maker() as session:
            result = await DailySandboxQuotaService(session).get_status(str(user.id))
        return result.used_today

    return user, start, used_today


@pytest.mark.asyncio
async def test_successful_sandbox_start_consumes_one_slot(sandbox_quota):
    user, start, used_today = sandbox_quota
    info = await start()
    assert info.status == SandboxStatus.RUNNING
    assert await used_today() == 1


@pytest.mark.asyncio
async def test_failed_sandbox_start_releases_reservation(sandbox_quota):
    user, start, used_today = sandbox_quota
    with pytest.raises(SandboxError):
        await start(outcome='http-error')
    assert await used_today() == 0


@pytest.mark.asyncio
async def test_limit_reached_rejects_next_start(sandbox_quota):
    user, start, used_today = sandbox_quota
    for _ in range(3):
        await start()
    assert await used_today() == 3
    with pytest.raises(HTTPException) as exc:
        await start()
    assert exc.value.status_code == 429
    assert exc.value.detail['code'] == 'daily_sandbox_limit_reached'
    assert await used_today() == 3


@pytest.mark.asyncio
async def test_unlimited_user_is_not_counted(sandbox_quota, async_session_maker):
    user, start, used_today = sandbox_quota
    async with async_session_maker() as session:
        db_user = await session.get(User, user.id)
        db_user.daily_sandbox_limit = -1
        await session.commit()
    await start()
    await start()
    assert await used_today() == 0


@pytest.mark.asyncio
async def test_unattributed_sandbox_is_not_counted(sandbox_quota):
    user, start, used_today = sandbox_quota
    anon = QuotaUserContext(None, user.current_org_id)
    await start(user_context_override=anon)
    assert await used_today() == 0
