import pytest

from openhands.app_server.config import get_default_web_url


def test_unset_web_host_returns_none(monkeypatch):
    monkeypatch.delenv('WEB_HOST', raising=False)
    assert get_default_web_url() is None


def test_empty_web_host_returns_none(monkeypatch):
    monkeypatch.setenv('WEB_HOST', '')
    assert get_default_web_url() is None


@pytest.mark.parametrize(
    'web_host, expected',
    [
        ('localhost', 'http://localhost'),
        ('localhost:3030', 'http://localhost:3030'),
        ('app.all-hands.dev', 'https://app.all-hands.dev'),
        ('openhands.example.com:8443', 'https://openhands.example.com:8443'),
        ('localhost.example.com', 'https://localhost.example.com'),
    ],
)
def test_scheme_depends_on_host(monkeypatch, web_host, expected):
    monkeypatch.setenv('WEB_HOST', web_host)
    assert get_default_web_url() == expected
