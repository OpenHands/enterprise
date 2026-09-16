"""Public capability and import compatibility for installation auth modes."""

import os
from collections.abc import Iterator

import pytest

from server import config
from server.auth import auth_config


@pytest.fixture
def native_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv('OH_WEB_URL', 'https://native.example.com')
    for name in list(os.environ):
        if name.startswith(('OH_PERMITTED_CORS_ORIGINS_')):
            monkeypatch.delenv(name)
    auth_config.get_native_auth_settings.cache_clear()
    yield
    auth_config.get_native_auth_settings.cache_clear()


def test_native_cors_accepts_explicit_indexed_origins_and_rejects_wildcards(
    native_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('OH_PERMITTED_CORS_ORIGINS_0', 'https://console.example.com')
    assert config.get_native_cors_origins() == [
        'https://native.example.com',
        'https://console.example.com',
    ]
    monkeypatch.setenv('OH_PERMITTED_CORS_ORIGINS_0', '*')
    with pytest.raises(ValueError, match='explicit trusted'):
        config.get_native_cors_origins()
