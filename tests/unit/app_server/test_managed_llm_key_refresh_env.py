"""Tests for the sandbox-side managed-key refresh env contract (#5189).

The app server injects three env vars so the in-sandbox agent-server can
re-resolve a rotated managed LiteLLM key on a 401 and retry in place. The names
and header shape are the contract consumed by the agent-server's
``register_managed_llm_key_refresh`` (software-agent-sdk#5222). These tests pin
that contract: the OH_ prefixed names, the URL, and — critically — that the auth
header references the session key by name (which the agent-server expands
in-sandbox) rather than embedding a value the app server cannot know. Docker
runtimes reference ``${OH_SESSION_API_KEYS_0}``; remote runtimes reference
``${SESSION_API_KEY}`` (enterprise#632).
"""

import json
import os
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openhands.app_server.sandbox.remote_sandbox_service import RemoteSandboxService
from openhands.app_server.sandbox.sandbox_service import (
    LLM_API_KEY_REFRESH_BASE_URLS_VARIABLE,
    LLM_API_KEY_REFRESH_HEADERS_VALUE,
    LLM_API_KEY_REFRESH_HEADERS_VALUE_REMOTE,
    LLM_API_KEY_REFRESH_HEADERS_VARIABLE,
    LLM_API_KEY_REFRESH_URL_VARIABLE,
    REMOTE_SESSION_API_KEY_VARIABLE,
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


def test_remote_refresh_header_value_references_session_api_key():
    """Remote runtimes set SESSION_API_KEY (not OH_SESSION_API_KEYS_0)."""
    assert REMOTE_SESSION_API_KEY_VARIABLE == 'SESSION_API_KEY'
    assert json.loads(LLM_API_KEY_REFRESH_HEADERS_VALUE_REMOTE) == {
        'X-Session-API-Key': '${' + REMOTE_SESSION_API_KEY_VARIABLE + '}'
    }


def test_remote_refresh_header_value_expands_to_session_key(monkeypatch):
    """A remote sandbox holding SESSION_API_KEY resolves the reference.

    This is the enterprise#632 fix: the old reference to ${OH_SESSION_API_KEYS_0}
    (unset in remote sandboxes) expanded to empty, so the refresh call was
    unauthenticated (401). ${SESSION_API_KEY} resolves to the real key.
    """
    monkeypatch.setenv(REMOTE_SESSION_API_KEY_VARIABLE, 'sess-remote-123')
    headers = json.loads(LLM_API_KEY_REFRESH_HEADERS_VALUE_REMOTE)
    expanded = {k: os.path.expandvars(v) for k, v in headers.items()}
    assert expanded == {'X-Session-API-Key': 'sess-remote-123'}


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
    # Remote runtimes expand ${SESSION_API_KEY}, so the remote path must inject the
    # remote header value (not the shared docker/OH_SESSION_API_KEYS_0 one).
    assert (
        env[LLM_API_KEY_REFRESH_HEADERS_VARIABLE]
        == LLM_API_KEY_REFRESH_HEADERS_VALUE_REMOTE
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


# --- enterprise#632 regression guard -------------------------------------------
# The original bug shipped green because the unit test monkeypatched the same
# variable the header referenced -- it validated the code against its own
# assumption, so it could never notice that the *remote runtime* actually sets a
# different variable. These literals are the ground truth of what each runtime
# provides inside the sandbox, deliberately NOT imported from sandbox_service so a
# wrong assumption in the code (e.g. remote pointing back at OH_SESSION_API_KEYS_0)
# fails here:
#   - docker runtimes export OH_SESSION_API_KEYS_0 (docker_sandbox_service sets it
#     in the env it builds)
#   - remote runtimes export SESSION_API_KEY inside the sandbox (verified live on
#     SaaS prod, enterprise#632); OH_SESSION_API_KEYS_0 is unset there.
RUNTIME_PROVIDES_SESSION_KEY_VAR = {
    'docker': 'OH_SESSION_API_KEYS_0',
    'remote': 'SESSION_API_KEY',
}
HEADER_VALUE_BY_RUNTIME = {
    'docker': LLM_API_KEY_REFRESH_HEADERS_VALUE,
    'remote': LLM_API_KEY_REFRESH_HEADERS_VALUE_REMOTE,
}
# Byte-for-byte header the warm pool is created with (OpenHands-Cloud
# replicated/openhands.yaml and the saas-deploy runtime-api values). It must equal
# the app-injected remote header exactly: OH_LLM_API_KEY_REFRESH_HEADERS is not in
# runtime-api's KEYS_TO_CLEAN, so any drift breaks warm claiming (enterprise#632
# Bug #1).
WARM_POOL_REMOTE_REFRESH_HEADER = '{"X-Session-API-Key": "${SESSION_API_KEY}"}'


@pytest.mark.parametrize('runtime_kind', ['docker', 'remote'])
def test_refresh_header_references_var_the_runtime_actually_sets(runtime_kind):
    """Each runtime's refresh header must reference the session-key variable that
    runtime provides in the sandbox; otherwise the agent-server expands it to an
    empty X-Session-API-Key and the refresh call 401s (enterprise#632).
    """
    header = json.loads(HEADER_VALUE_BY_RUNTIME[runtime_kind])
    match = re.fullmatch(r'\$\{(\w+)\}', header['X-Session-API-Key'])
    assert match, f'X-Session-API-Key must be a single ${{VAR}}, got {header!r}'
    assert match.group(1) == RUNTIME_PROVIDES_SESSION_KEY_VAR[runtime_kind]


def test_docker_and_remote_use_different_session_key_vars():
    """Pin the asymmetry so a refactor cannot collapse both paths onto one
    variable (which would re-break whichever runtime does not set it).
    """
    assert (
        RUNTIME_PROVIDES_SESSION_KEY_VAR['docker']
        != RUNTIME_PROVIDES_SESSION_KEY_VAR['remote']
    )


def test_remote_refresh_header_is_byte_identical_to_warm_pool():
    """Warm claims match env exactly and this header key is not cleaned, so the
    app-injected remote header must equal the warm-pool header byte-for-byte
    (enterprise#632 Bug #1).
    """
    assert LLM_API_KEY_REFRESH_HEADERS_VALUE_REMOTE == WARM_POOL_REMOTE_REFRESH_HEADER
