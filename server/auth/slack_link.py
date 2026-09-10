"""Choose the existing Slack authorization entry point for the installation."""

from collections.abc import Callable
from urllib.parse import urlencode

from integrations.utils import HOST_URL
from server.auth.mode import AuthMode, get_auth_mode


def slack_login_url(state: str, legacy_authorize: Callable[[str], str]) -> str:
    if get_auth_mode() is AuthMode.LOCAL:
        return f'{HOST_URL}/slack/install' + (
            '?' + urlencode({'state': state}) if state else ''
        )
    return legacy_authorize(state)
