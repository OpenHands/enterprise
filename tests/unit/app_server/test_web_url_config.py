"""The application and native authentication share one public URL configuration."""

from collections.abc import Iterator

import pytest

from openhands.agent_server.env_parser import from_env
from openhands.app_server.config import AppServerConfig
from openhands.app_server.utils.web_url import get_web_url_from_env
from server.auth import auth_config


@pytest.fixture(autouse=True)
def clear_web_url_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (
        'OH',
        'OH_WEB_URL',
        'OH_WEB_URL_IS_NONE',
        'WEB_HOST',
        'AUTH_ALLOW_INSECURE_LOCALHOST',
        'NATIVE_AUTH_ALLOW_INSECURE_LOCALHOST',
    ):
        monkeypatch.delenv(name, raising=False)
    auth_config.get_native_auth_settings.cache_clear()
    yield
    auth_config.get_native_auth_settings.cache_clear()


@pytest.mark.parametrize(
    'environment,expected',
    [
        ({}, None),
        ({'WEB_HOST': 'public.example.test:8443'}, 'https://public.example.test:8443'),
        (
            {
                'WEB_HOST': 'legacy.example.test',
                'OH_WEB_URL': 'https://public.example.test/app/',
            },
            'https://public.example.test/app/',
        ),
        ({'WEB_HOST': 'legacy.example.test', 'OH_WEB_URL': ''}, ''),
        (
            {'OH': '{"web_url":"https://json.example.test/app"}'},
            'https://json.example.test/app',
        ),
        (
            {
                'OH': '{"web_url":"https://json.example.test"}',
                'OH_WEB_URL': 'https://override.example.test',
            },
            'https://override.example.test',
        ),
        ({'WEB_HOST': 'legacy.example.test', 'OH_WEB_URL_IS_NONE': '1'}, None),
        (
            {'OH_WEB_URL': 'https://public.example.test', 'OH_WEB_URL_IS_NONE': 'true'},
            'https://public.example.test',
        ),
    ],
)
def test_application_and_auth_resolve_the_same_web_url(
    monkeypatch: pytest.MonkeyPatch, environment: dict[str, str], expected: str | None
) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    assert get_web_url_from_env() == expected
    assert AppServerConfig().web_url == expected
    assert (
        AppServerConfig.model_validate(from_env(AppServerConfig, 'OH')).web_url
        == expected
    )
    monkeypatch.setenv('AUTH_ALLOW_INSECURE_LOCALHOST', '1')
    if expected == '':
        with pytest.raises(ValueError, match='web_url'):
            auth_config.get_native_auth_settings()
    else:
        assert auth_config.get_native_auth_settings().web_url == (
            expected.rstrip('/') if expected is not None else 'http://localhost:3000'
        )


def test_auth_derives_origin_and_ignores_removed_origin_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('OH_WEB_URL', 'https://public.example.test:8443/openhands/')
    monkeypatch.setenv('AUTH_APP_ORIGIN', 'https://wrong.example.test')
    monkeypatch.setenv('NATIVE_AUTH_APP_ORIGIN', 'invalid')
    settings = auth_config.get_native_auth_settings()
    assert settings.web_url == 'https://public.example.test:8443/openhands'
    assert settings.app_origin == 'https://public.example.test:8443'


@pytest.mark.parametrize('enabled', ['true', '1'])
@pytest.mark.parametrize('host', ['localhost', '127.0.0.1', '[::1]'])
def test_local_http_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch, enabled: str, host: str
) -> None:
    monkeypatch.setenv('OH_WEB_URL', f'http://{host}:3000/app/')
    with pytest.raises(ValueError, match='web_url'):
        auth_config.get_native_auth_settings()
    monkeypatch.setenv('AUTH_ALLOW_INSECURE_LOCALHOST', enabled)
    settings = auth_config.get_native_auth_settings()
    assert settings.app_origin == f'http://{host}:3000'
    assert settings.web_url == f'http://{host}:3000/app'


def test_unconfigured_local_http_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match='web_url'):
        auth_config.get_native_auth_settings()


@pytest.mark.parametrize(
    'web_url',
    [
        '',
        'public.example.test',
        '//public.example.test',
        'ftp://public.example.test',
        'https:///path',
        'http://public.example.test',
        'http://localhost.attacker.test',
        'https://*.example.test',
        'https://user@public.example.test',
        'https://user:password@public.example.test',
        'https://public.example.test/app?query=value',
        'https://public.example.test/app?',
        'https://public.example.test/app#fragment',
        'https://public.example.test/app#',
        'https://public.example.test:invalid',
        'https://public.example.test:65536',
        'https://public.example.test:',
        'https://[::1',
        'https://public.example.test\\attacker.test',
        'https://public.example.test/\n',
    ],
)
def test_auth_rejects_untrusted_public_urls(
    monkeypatch: pytest.MonkeyPatch, web_url: str
) -> None:
    monkeypatch.setenv('OH_WEB_URL', web_url)
    monkeypatch.setenv('AUTH_ALLOW_INSECURE_LOCALHOST', '1')
    with pytest.raises(ValueError, match='web_url'):
        auth_config.get_native_auth_settings()
