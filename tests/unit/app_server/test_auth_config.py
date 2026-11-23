"""OAuth v2 login is declared once: the backend and the web client must agree."""

import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

from openhands.agent_server.env_parser import from_env
from openhands.app_server.auth_config import get_auth_config
from openhands.app_server.types import AppMode
from openhands.app_server.web_client import default_web_client_config_injector as mod
from server.auth.constants import is_oauth_v2_login_enabled

CANONICAL = 'OH_AUTH_ENABLE_OAUTH_V2_LOGIN'
STRUCTURED = 'OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_OAUTH_V2_LOGIN'
BARE = 'ENABLE_OAUTH_V2_LOGIN'


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (CANONICAL, STRUCTURED, BARE):
        monkeypatch.delenv(name, raising=False)


class _FakeFlagService:
    async def resolve(self, key, default=False):
        return default

    async def get_global_flags(self):
        return {}


class _FakeGlobalConfig:
    app_mode = AppMode.SAAS


async def _web_client_flag() -> bool:
    """The flag as served, with the injector built the way the app builds it."""
    fake_module = types.ModuleType('server.services.feature_flag_service')
    fake_module.feature_flag_service = _FakeFlagService()
    with (
        patch(
            'openhands.app_server.config.get_global_config',
            return_value=_FakeGlobalConfig(),
        ),
        patch.dict(sys.modules, {'server.services.feature_flag_service': fake_module}),
        patch.object(mod, '_resolve_enable_integrated_idp', AsyncMock(False)),
    ):
        injector = from_env(mod.DefaultWebClientConfigInjector, 'OH_WEB_CLIENT')
        config = await injector.get_web_client_config()
    return config.feature_flags.enable_oauth_v2_login


@pytest.mark.parametrize(
    ('env', 'expected'),
    [
        ({}, False),
        ({BARE: '1'}, True),
        ({BARE: 'true'}, True),
        ({BARE: 'TRUE'}, True),
        ({BARE: 'false'}, False),
        ({BARE: '0'}, False),
        ({STRUCTURED: '1'}, True),
        ({STRUCTURED: 'true'}, True),
        ({STRUCTURED: 'false'}, False),
        ({STRUCTURED: '1', BARE: 'false'}, True),
        ({STRUCTURED: 'false', BARE: '1'}, False),
        ({CANONICAL: '1'}, True),
        ({CANONICAL: 'true'}, True),
        ({CANONICAL: '0'}, False),
        ({CANONICAL: 'false', BARE: '1', STRUCTURED: '1'}, False),
        ({CANONICAL: '1', BARE: 'false', STRUCTURED: 'false'}, True),
        # An unrelated structured flag resets the web client's other fields.
        ({'OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_BILLING': 'true', BARE: '1'}, True),
        ({'OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_BILLING': 'true'}, False),
        ({'OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_BILLING': 'true', CANONICAL: '1'}, True),
    ],
)
@pytest.mark.asyncio
async def test_backend_and_web_client_agree(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: bool
):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert get_auth_config().enable_oauth_v2_login is expected
    assert is_oauth_v2_login_enabled() is expected
    assert await _web_client_flag() is expected
