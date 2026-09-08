"""Tests for the redis client URL construction.

The authed URL is consumed by the ``limits`` library, which parses it with a
URL parser (coredis). Passwords containing URL-special characters (notably a
trailing newline from a Secret mount) must be URL-encoded so the parser
recovers the exact bytes Redis expects.
"""

from urllib.parse import unquote, urlparse

import pytest

import openhands.app_server.utils.redis as redis_utils
from openhands.app_server.utils.redis import (
    REDIS_DB,
    REDIS_HOST,
    REDIS_PORT,
    get_redis_authed_url,
)


def _parse_password(url: str) -> str:
    return unquote(urlparse(url).password or '')


@pytest.fixture
def restore_password():
    original = redis_utils.REDIS_PASSWORD
    yield
    redis_utils.REDIS_PASSWORD = original


def _set_password(value: str) -> None:
    redis_utils.REDIS_PASSWORD = value


@pytest.mark.usefixtures('restore_password')
class TestGetRedisAuthedUrl:
    def test_password_round_trips_through_url_parsing(self):
        pw = 's3cret-pw'
        _set_password(pw)
        assert _parse_password(get_redis_authed_url()) == pw

    def test_password_with_trailing_newline_round_trips(self):
        # Reproduces the Secret-mount case that broke coredis auth.
        pw = 's3cret-pw\n'
        _set_password(pw)
        assert _parse_password(get_redis_authed_url()) == pw

    @pytest.mark.parametrize(
        'pw',
        [
            'pw with spaces',
            'p@ss:word/with#special?chars+and%',
            'unicode-pässwörd',
        ],
    )
    def test_password_with_special_characters_round_trips(self, pw: str):
        _set_password(pw)
        assert _parse_password(get_redis_authed_url()) == pw

    def test_empty_password_produces_empty_credentials(self):
        _set_password('')
        url = get_redis_authed_url()
        # quote('', safe='') == '', so the password slot is empty rather than None.
        assert _parse_password(url) == ''
        assert url == f'redis://:@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}'

    def test_url_contains_host_port_db(self):
        _set_password('irrelevant')
        url = get_redis_authed_url()
        parsed = urlparse(url)
        assert parsed.hostname == REDIS_HOST
        assert parsed.port == REDIS_PORT
        assert int(parsed.path.lstrip('/')) == REDIS_DB
