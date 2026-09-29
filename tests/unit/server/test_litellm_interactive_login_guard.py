"""Tests for the LiteLLM interactive-login guard (OpenHands/enterprise#565).

These assert that once the guard is installed, resolving an interactive-login
model (``chatgpt/...`` / ``github_copilot/...``) through LiteLLM never reaches the
blocking device-login flow, on any code path that calls ``get_llm_provider`` or
``get_api_base`` (which covers SDK agent-settings validation on both the
maintenance runner and request paths).
"""

from __future__ import annotations

import importlib

import litellm
import pytest

from server.utils import litellm_interactive_login_guard as guard


@pytest.fixture
def installed_guard():
    """Install the guard, then restore the original methods afterwards.

    The guard patches LiteLLM authenticator classes process-wide; restoring keeps
    the change from leaking into other tests in the session.
    """
    originals: list[tuple[type, str, object]] = []
    for _prefix, module_path, method_name in guard._INTERACTIVE_LOGIN_AUTHENTICATORS:
        try:
            module = importlib.import_module(module_path)
        except Exception:
            continue
        authenticator_cls = module.Authenticator
        originals.append(
            (authenticator_cls, method_name, getattr(authenticator_cls, method_name))
        )

    guard._installed = False
    guard.install_litellm_interactive_login_guard()
    try:
        yield guard
    finally:
        for authenticator_cls, method_name, original in originals:
            setattr(authenticator_cls, method_name, original)
        guard._installed = False


def test_is_interactive_login_model():
    assert guard.is_interactive_login_model('chatgpt/gpt-5-codex')
    assert guard.is_interactive_login_model('github_copilot/gpt-4o')
    assert guard.is_interactive_login_model('CHATGPT/gpt-5')  # case-insensitive
    assert guard.is_interactive_login_model(' chatgpt/gpt-5 ')  # surrounding whitespace
    assert not guard.is_interactive_login_model('openhands/deepseek-v4-flash')
    assert not guard.is_interactive_login_model('anthropic/claude-sonnet-4-5')
    assert not guard.is_interactive_login_model(None)
    assert not guard.is_interactive_login_model('')


def test_guard_blocks_chatgpt_device_login(installed_guard):
    from litellm.llms.chatgpt.authenticator import Authenticator

    def _fail(self):  # pragma: no cover - must never run
        raise AssertionError('interactive device login was triggered')

    # If the guard did NOT neutralize get_access_token, this would be reached.
    Authenticator._login_device_code = _fail  # type: ignore[method-assign]

    _model, provider, _key, _base = litellm.get_llm_provider('chatgpt/gpt-5-codex')
    assert provider == 'chatgpt'


def test_guard_blocks_github_copilot_device_login(installed_guard):
    from litellm.llms.github_copilot.authenticator import Authenticator

    def _fail(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError('interactive device login was triggered')

    Authenticator._login = _fail  # type: ignore[method-assign]

    _model, provider, _key, _base = litellm.get_llm_provider('github_copilot/gpt-4o')
    assert provider == 'github_copilot'


def test_guard_covers_get_api_base(installed_guard):
    from litellm.llms.chatgpt.authenticator import Authenticator

    def _fail(self):  # pragma: no cover - must never run
        raise AssertionError('interactive device login was triggered')

    Authenticator._login_device_code = _fail  # type: ignore[method-assign]

    # get_api_base() internally resolves the provider; it must not block either.
    litellm.get_api_base(model='chatgpt/gpt-5-codex', optional_params={})


def test_install_is_idempotent(installed_guard):
    from litellm.llms.chatgpt.authenticator import Authenticator

    guarded = Authenticator.get_access_token
    guard.install_litellm_interactive_login_guard()
    # Re-installing must not wrap an already-guarded method again.
    assert Authenticator.get_access_token is guarded


def test_reinstall_after_reset_preserves_guarded_method(installed_guard):
    """The per-method ``_GUARD_ATTR`` skip keeps an already-guarded method intact.

    ``_installed`` short-circuits ``install()`` in normal use, but if it is reset
    (e.g. re-imported/re-run in-process) the per-method guard attribute must still
    prevent re-wrapping, so method identity is preserved.
    """
    from litellm.llms.chatgpt.authenticator import Authenticator

    guarded = Authenticator.get_access_token
    guard._installed = False
    guard.install_litellm_interactive_login_guard()
    assert Authenticator.get_access_token is guarded


def test_guarded_credential_is_empty(installed_guard):
    """A neutralized credential method returns '' (i.e. 'no credential').

    A non-empty return would be handed downstream as if it were a real key.
    """
    from litellm.llms.chatgpt.authenticator import Authenticator as ChatGPTAuth
    from litellm.llms.github_copilot.authenticator import Authenticator as CopilotAuth

    assert ChatGPTAuth.get_access_token(object()) == ''
    assert CopilotAuth.get_api_key(object()) == ''


def test_blocking_emits_warning(installed_guard, monkeypatch):
    """The guard logs a warning when it blocks a login (operator-visible signal).

    The logger doesn't propagate to root, so assert on the call directly rather
    than via ``caplog``.
    """
    from litellm.llms.chatgpt.authenticator import Authenticator as ChatGPTAuth

    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(guard.logger, 'warning', lambda *a, **k: calls.append((a, k)))

    ChatGPTAuth.get_access_token(object())

    assert calls and calls[0][0][0] == 'litellm_interactive_login_blocked'
    assert calls[0][1]['extra'] == {
        'provider': 'chatgpt',
        'method': 'get_access_token',
    }


def test_missing_provider_is_tolerated(monkeypatch):
    """A LiteLLM version missing one provider must not break install().

    The ``except Exception: continue`` path is the version-tolerance safety net:
    if a provider module can't be imported, install() must skip it silently and
    still guard the providers that are present.
    """
    real_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == 'litellm.llms.chatgpt.authenticator':
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    from litellm.llms.github_copilot.authenticator import Authenticator as CopilotAuth

    original = CopilotAuth.get_api_key
    monkeypatch.setattr(guard.importlib, 'import_module', fake_import)
    guard._installed = False
    try:
        guard.install_litellm_interactive_login_guard()  # must not raise
        assert getattr(CopilotAuth.get_api_key, guard._GUARD_ATTR, False) is True
    finally:
        CopilotAuth.get_api_key = original
        guard._installed = False
