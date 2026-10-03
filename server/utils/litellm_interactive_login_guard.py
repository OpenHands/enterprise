"""Prevent LiteLLM providers from starting an interactive OAuth device login.

Some LiteLLM providers (``chatgpt``, ``github_copilot``) resolve their provider
info by performing an interactive OAuth *device-code* login: they print a
"sign in ..." prompt and then block (synchronous ``time.sleep`` + ``httpx``)
for up to ~15 minutes polling for authorization. That flow makes no sense in a
headless server or CronJob, where nobody can complete the login -- it wedges the
process (see OpenHands/enterprise#565).

Any code path that constructs/validates an SDK ``LLM`` with such a model
(agent-settings validation on both request and maintenance paths) reaches
``litellm.get_llm_provider("<provider>/<model>")`` -> the provider config's
``_get_openai_compatible_provider_info(...)`` -> the provider ``Authenticator``'s
credential method, which is where the block happens.

This module installs a process-wide guard that neutralizes those credential
methods so provider resolution returns immediately with an empty credential
instead of launching an interactive login. It is idempotent and safe to call
from every server / cron entrypoint.

Note: this is the enterprise-side mitigation. A server must never perform an
interactive device login, so disabling these flows is the correct behavior here;
the longer-term home for "don't do interactive auth during validation" is the
SDK/LiteLLM layer.
"""

from __future__ import annotations

import importlib

from server.logger import logger

# (provider prefix, Authenticator module path, credential method that blocks)
#
# Both providers' ``_get_openai_compatible_provider_info`` call ``get_api_base()``
# (a safe, static lookup) and then the method below to obtain the credential,
# which is the call that launches the blocking device login.
_INTERACTIVE_LOGIN_AUTHENTICATORS: tuple[tuple[str, str, str], ...] = (
    ('chatgpt', 'litellm.llms.chatgpt.authenticator', 'get_access_token'),
    ('github_copilot', 'litellm.llms.github_copilot.authenticator', 'get_api_key'),
)

INTERACTIVE_LOGIN_PROVIDERS: frozenset[str] = frozenset(
    prefix for prefix, _, _ in _INTERACTIVE_LOGIN_AUTHENTICATORS
)

_GUARD_ATTR = '_openhands_interactive_login_disabled'
_installed = False


def is_interactive_login_model(model: str | None) -> bool:
    """Return True if ``model`` targets a provider that uses interactive login.

    Uses only the ``provider/`` prefix, so it never itself calls LiteLLM and can
    be used safely on request paths (e.g. to reject such a model on save).
    """
    if not model:
        return False
    provider = model.split('/', 1)[0].strip().lower()
    return provider in INTERACTIVE_LOGIN_PROVIDERS


def install_litellm_interactive_login_guard() -> None:
    """Disable LiteLLM interactive device-login flows for this process.

    Idempotent. A LiteLLM version that lacks one of these providers is tolerated
    (the missing provider is skipped) so startup never breaks on a version bump.
    """
    global _installed
    if _installed:
        return

    for prefix, module_path, method_name in _INTERACTIVE_LOGIN_AUTHENTICATORS:
        try:
            module = importlib.import_module(module_path)
            authenticator_cls = module.Authenticator
        except Exception:
            # Provider not present in this LiteLLM version -- nothing to guard.
            continue

        original = getattr(authenticator_cls, method_name, None)
        if original is None or getattr(original, _GUARD_ATTR, False):
            continue

        setattr(
            authenticator_cls,
            method_name,
            _make_guarded_credential_method(prefix, method_name),
        )

    _installed = True


def _make_guarded_credential_method(provider: str, method_name: str):
    """Build a no-op replacement for a provider's credential method."""

    def _guarded(self, *args, **kwargs) -> str:  # noqa: ANN001, ARG001
        logger.warning(
            'litellm_interactive_login_blocked',
            extra={'provider': provider, 'method': method_name},
        )
        return ''

    setattr(_guarded, _GUARD_ATTR, True)
    return _guarded
