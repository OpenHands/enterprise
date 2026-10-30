"""Tests for auth utility helpers."""

import hmac
from unittest.mock import patch

import pytest

from openhands.app_server.utils.auth import secrets_equal


class TestSecretsEqual:
    def test_equal_secrets(self):
        assert secrets_equal('s3cret', 's3cret') is True

    @pytest.mark.parametrize(
        'provided',
        ['wrong', '', 's3cre', 's3crett', 'sécret', 'ÿþ'],
    )
    def test_unequal_secrets(self, provided: str):
        # Non-ASCII input must return False instead of raising TypeError.
        assert secrets_equal(provided, 's3cret') is False

    def test_equal_non_ascii_secrets(self):
        assert secrets_equal('sécret', 'sécret') is True

    def test_uses_constant_time_compare(self):
        with patch('hmac.compare_digest', wraps=hmac.compare_digest) as spy:
            secrets_equal('a', 'b')
        spy.assert_called_once_with(b'a', b'b')
