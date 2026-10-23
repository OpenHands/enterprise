import pytest

from openhands.app_server.utils.web_host import get_web_host, get_web_url


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv('WEB_HOST', raising=False)
    monkeypatch.delenv('OH_WEB_URL', raising=False)


def test_unset_returns_none():
    assert get_web_host() is None
    assert get_web_url() is None


def test_web_host_only(monkeypatch):
    monkeypatch.setenv('WEB_HOST', ' app.example.com ')

    assert get_web_host() == 'app.example.com'
    assert get_web_url() is None


def test_web_url_only_gives_host_without_port(monkeypatch):
    monkeypatch.setenv('OH_WEB_URL', 'http://localhost:3100/')

    assert get_web_host() == 'localhost'
    assert get_web_url() == 'http://localhost:3100'


def test_web_host_wins_when_both_set(monkeypatch):
    monkeypatch.setenv('WEB_HOST', 'app.example.com')
    monkeypatch.setenv('OH_WEB_URL', 'https://app.example.com')

    assert get_web_host() == 'app.example.com'
    assert get_web_url() == 'https://app.example.com'
