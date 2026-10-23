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


# --- enterprise#632 regression guard (generic) ---------------------------------
# The original bug shipped green because the unit test monkeypatched the same
# variable the header referenced -- it validated the code against its own
# assumption, so it never noticed the *remote runtime* sets a different variable.
#
# The generic invariant this guards: any value the app injects into a sandbox may
# contain a ${VAR} reference (expanded in-sandbox by the agent-server) ONLY if VAR
# is a variable that runtime actually provides. Referencing anything else expands
# to an empty string in-sandbox (enterprise#632) -- not specific to the session
# key or this one header.
#
# RUNTIME_PROVIDED_VARS is ground truth, deliberately NOT imported from the code
# under test, so a wrong assumption in that code fails here. Keep it in sync with
# what each runtime exports inside the sandbox:
#   - docker -> OH_SESSION_API_KEYS_0 (docker_sandbox_service sets it in-env)
#   - remote -> SESSION_API_KEY (remote runtime sets it in-sandbox; verified live
#     on SaaS prod -- OH_SESSION_API_KEYS_0 is unset there)
RUNTIME_PROVIDED_VARS = {
    'docker': {'OH_SESSION_API_KEYS_0'},
    'remote': {'SESSION_API_KEY'},
}

# Static env values the app injects per runtime kind that may carry ${VAR} refs.
INJECTED_REFERENCING_VALUES = {
    'docker': [LLM_API_KEY_REFRESH_HEADERS_VALUE],
    'remote': [LLM_API_KEY_REFRESH_HEADERS_VALUE_REMOTE],
}

_ENV_VAR_REF = re.compile(r'\$\{(\w+)\}')


def _referenced_vars(*values):
    """Return every ${VAR} name referenced by the given env values."""
    refs: set[str] = set()
    for value in values:
        if isinstance(value, str):
            refs.update(_ENV_VAR_REF.findall(value))
    return refs


@pytest.mark.parametrize('runtime_kind', sorted(RUNTIME_PROVIDED_VARS))
def test_injected_values_only_reference_runtime_provided_vars(runtime_kind):
    """Generic guard: every ${VAR} the app injects for a runtime must be one that
    runtime provides in the sandbox, or it expands to empty (enterprise#632)."""
    unknown = (
        _referenced_vars(*INJECTED_REFERENCING_VALUES[runtime_kind])
        - RUNTIME_PROVIDED_VARS[runtime_kind]
    )
    assert not unknown, (
        f'{runtime_kind} injects ${{VAR}} refs the runtime does not provide: '
        f'{sorted(unknown)}; allowed: {sorted(RUNTIME_PROVIDED_VARS[runtime_kind])}'
    )


@pytest.mark.asyncio
async def test_remote_produced_env_only_references_runtime_provided_vars():
    """Same generic guard against the *dynamically built* remote env, so any newly
    injected reference is covered automatically -- not just the static header."""
    stub = SimpleNamespace(web_url=WEB_URL)
    with patch('server.constants.LITE_LLM_API_URL', MANAGED_BASE_URL):
        env = await RemoteSandboxService._init_environment(stub, _spec(), 'sid')
    unknown = _referenced_vars(*env.values()) - RUNTIME_PROVIDED_VARS['remote']
    assert not unknown, (
        f'remote env injects ${{VAR}} refs the runtime does not provide: '
        f'{sorted(unknown)}'
    )


def test_remote_refresh_header_is_byte_identical_to_warm_pool():
    """Warm claims match env exactly and this header key is not cleaned, so the
    app-injected remote header must equal the warm-pool header byte-for-byte
    (OpenHands-Cloud replicated/openhands.yaml and saas-deploy values;
    enterprise#632 Bug #1)."""
    warm_pool_remote_header = '{"X-Session-API-Key": "${SESSION_API_KEY}"}'
    assert LLM_API_KEY_REFRESH_HEADERS_VALUE_REMOTE == warm_pool_remote_header
