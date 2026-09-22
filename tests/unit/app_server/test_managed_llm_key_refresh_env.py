"""Tests for the sandbox-side managed-key refresh env contract (#5189).

The app server injects three env vars so the in-sandbox agent-server can
re-resolve a rotated managed LiteLLM key on a 401 and retry in place. The names
and header shape are the contract consumed by the agent-server's
``register_managed_llm_key_refresh`` (software-agent-sdk#5222). These tests pin
that contract: the OH_ prefixed names, the URL, and — critically — that the auth
header references the session key as ``${OH_SESSION_API_KEYS_0}`` (which the
agent-server expands in-sandbox) rather than embedding a value the app server
cannot know for remote runtimes.
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openhands.app_server.sandbox.remote_sandbox_service import RemoteSandboxService
from openhands.app_server.sandbox.sandbox_service import (
    LLM_API_KEY_REFRESH_BASE_URLS_VARIABLE,
    LLM_API_KEY_REFRESH_HEADERS_VALUE,
    LLM_API_KEY_REFRESH_HEADERS_VARIABLE,
    LLM_API_KEY_REFRESH_URL_VARIABLE,
    SESSION_API_KEY_VARIABLE,
)

MANAGED_BASE_URL = 'https://llm-proxy.example'
WEB_URL = 'https://app.example'


def _spec():
    return SimpleNamespace(initial_env={})


def test_refresh_contract_var_names_use_oh_prefix():
    """Names must match what the agent-server reads via os.environ (#5222)."""
    assert LLM_API_KEY_REFRESH_URL_VARIABLE == 'OH_LLM_API_KEY_REFRESH_URL'
    assert LLM_API_KEY_REFRESH_HEADERS_VARIABLE == 'OH_LLM_API_KEY_REFRESH_HEADERS'
    assert LLM_API_KEY_REFRESH_BASE_URLS_VARIABLE == 'OH_LLM_API_KEY_REFRESH_BASE_URLS'


def test_refresh_header_value_references_session_key():
    """The header references the session key rather than embedding it."""
    assert json.loads(LLM_API_KEY_REFRESH_HEADERS_VALUE) == {
        'X-Session-API-Key': '${' + SESSION_API_KEY_VARIABLE + '}'
    }


def test_refresh_header_value_expands_to_session_key(monkeypatch):
    """Mirrors the agent-server: the reference resolves to the sandbox key.

    The in-sandbox consumer expands header values with os.path.expandvars, so a
    sandbox holding OH_SESSION_API_KEYS_0 turns the reference into the real key.
    """
    monkeypatch.setenv(SESSION_API_KEY_VARIABLE, 'sess-abc123')
    headers = json.loads(LLM_API_KEY_REFRESH_HEADERS_VALUE)
    expanded = {k: os.path.expandvars(v) for k, v in headers.items()}
    assert expanded == {'X-Session-API-Key': 'sess-abc123'}


@pytest.mark.asyncio
async def test_remote_init_environment_injects_refresh_contract():
    """A remote sandbox with a public web_url gets the full refresh contract."""
    stub = SimpleNamespace(web_url=WEB_URL)
    with patch('server.constants.LITE_LLM_API_URL', MANAGED_BASE_URL):
        env = await RemoteSandboxService._init_environment(stub, _spec(), 'sid')

    assert (
        env[LLM_API_KEY_REFRESH_URL_VARIABLE]
        == f'{WEB_URL}/api/keys/llm/managed/current'
    )
    assert (
        env[LLM_API_KEY_REFRESH_HEADERS_VARIABLE] == LLM_API_KEY_REFRESH_HEADERS_VALUE
    )
    assert env[LLM_API_KEY_REFRESH_BASE_URLS_VARIABLE] == MANAGED_BASE_URL


@pytest.mark.asyncio
async def test_remote_init_environment_skips_refresh_without_web_url():
    """Without a public web_url (local dev) the refresh vars are not injected."""
    stub = SimpleNamespace(web_url=None)
    with patch('server.constants.LITE_LLM_API_URL', MANAGED_BASE_URL):
        env = await RemoteSandboxService._init_environment(stub, _spec(), 'sid')

    assert LLM_API_KEY_REFRESH_URL_VARIABLE not in env
    assert LLM_API_KEY_REFRESH_HEADERS_VARIABLE not in env
    assert LLM_API_KEY_REFRESH_BASE_URLS_VARIABLE not in env


@pytest.mark.asyncio
async def test_remote_init_environment_skips_base_urls_without_managed_url():
    """The base-urls allow-list is gated on a configured managed proxy URL.

    When ``LITE_LLM_API_URL`` is empty the URL/headers still go out (so the
    caller can try), but the base-urls var must be *absent* rather than
    present-and-empty -- an empty allow-list is a different contract than "no
    allow-list" to the agent-server consumer.
    """
    stub = SimpleNamespace(web_url=WEB_URL)
    with patch('server.constants.LITE_LLM_API_URL', ''):
        env = await RemoteSandboxService._init_environment(stub, _spec(), 'sid')

    assert (
        env[LLM_API_KEY_REFRESH_URL_VARIABLE]
        == f'{WEB_URL}/api/keys/llm/managed/current'
    )
    assert LLM_API_KEY_REFRESH_BASE_URLS_VARIABLE not in env
