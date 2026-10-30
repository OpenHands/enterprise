"""Tests for the session API key dependency."""

import hmac
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from openhands.app_server.utils.dependencies import check_session_api_key


class TestCheckSessionApiKey:
    def test_valid_key(self):
        with patch(
            'openhands.app_server.utils.dependencies._SESSION_API_KEY', 'session-key'
        ):
            check_session_api_key('session-key')

    @pytest.mark.parametrize('provided', [None, '', 'wrong-key', 'séssion-key'])
    def test_invalid_key_is_rejected_in_constant_time(self, provided):
        with (
            patch(
                'openhands.app_server.utils.dependencies._SESSION_API_KEY',
                'session-key',
            ),
            patch('hmac.compare_digest', wraps=hmac.compare_digest) as spy,
        ):
            with pytest.raises(HTTPException) as exc_info:
                check_session_api_key(provided)
        assert exc_info.value.status_code == 401
        if provided is not None:
            spy.assert_called_once()
