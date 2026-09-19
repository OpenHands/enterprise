"""Tests for resolve_profile_llm in openhands.app_server.settings.llm_profiles."""

from pydantic import SecretStr

from openhands.app_server.settings.llm_profiles import resolve_profile_llm
from openhands.sdk.llm import LLM

MANAGED_URL = 'http://openhands-litellm.openhands.svc.cluster.local:4000'
BYOR_URL = 'https://api.anthropic.com'

CURRENT_KEY = SecretStr('sk-current-managed-key')
ROTATED_OUT_KEY = SecretStr('sk-rotated-out-managed-key')


def _key_of(llm: LLM) -> str | None:
    if llm.api_key is None:
        return None
    return (
        llm.api_key.get_secret_value()
        if isinstance(llm.api_key, SecretStr)
        else str(llm.api_key)
    )


class TestManagedProxyProfiles:
    """Managed profiles always take the org's current key."""

    def test_stale_stored_key_on_managed_profile_is_overridden(self):
        """A real-but-rotated-out key must not shadow the current managed key.

        This is the regression: a managed profile that somehow persisted a real
        key (bypassing the org-profiles masking) kept it forever, so every run
        pinned to that profile hit the proxy with a deleted virtual key and got
        a 401 that no rotation could heal.
        """
        resolved = resolve_profile_llm(
            LLM(
                model='litellm_proxy/claude-sonnet-4-5-20250929',
                base_url=MANAGED_URL,
                api_key=ROTATED_OUT_KEY,
                usage_id='test',
            ),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=CURRENT_KEY,
        )
        assert _key_of(resolved) == CURRENT_KEY.get_secret_value()

    def test_openhands_prefix_infers_managed_url_and_takes_current_key(self):
        """``openhands/`` models resolve to the managed url, so same rule applies."""
        resolved = resolve_profile_llm(
            LLM(
                model='openhands/claude-sonnet-4-5-20250929',
                api_key=ROTATED_OUT_KEY,
                usage_id='test',
            ),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=CURRENT_KEY,
        )
        assert resolved.base_url == MANAGED_URL
        assert _key_of(resolved) == CURRENT_KEY.get_secret_value()

    def test_keyless_managed_profile_still_gets_fallback(self):
        """Pre-existing behaviour: masked/absent key is filled from the fallback."""
        resolved = resolve_profile_llm(
            LLM(model='openhands/claude-opus-4-8', usage_id='test'),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=CURRENT_KEY,
        )
        assert _key_of(resolved) == CURRENT_KEY.get_secret_value()

    def test_no_fallback_leaves_stored_key_untouched(self):
        """Without a fallback there is nothing better to use; don't blank the key."""
        resolved = resolve_profile_llm(
            LLM(
                model='openhands/claude-opus-4-8',
                api_key=ROTATED_OUT_KEY,
                usage_id='test',
            ),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=None,
        )
        assert _key_of(resolved) == ROTATED_OUT_KEY.get_secret_value()


class TestByorFallbackMustNotPoisonManagedProfiles:
    """A BYOR/custom effective key must never be handed to a managed profile.

    Regression: activating a broken custom ("dummy") model made the member's
    effective key that dummy key, which then flowed as the fallback into
    ``resolve_profile_llm`` for *every* profile — including the managed default.
    The managed-profile override branch replaced the real ``sk-`` virtual key
    with the dummy, so the default model hit the LiteLLM proxy with the dummy
    key and got a 401 ("LiteLLM Virtual Key expected"). The override is only
    safe when the fallback is itself the org/member managed key.
    """

    DUMMY_BYOR_KEY = SecretStr('dummy_model')

    def test_byor_fallback_does_not_override_stored_managed_key(self):
        """A real persisted managed key must survive a BYOR fallback."""
        real_managed_key = SecretStr('sk-real-managed-key')
        resolved = resolve_profile_llm(
            LLM(
                model='openhands/claude-sonnet-4-5-20250929',
                api_key=real_managed_key,
                usage_id='test',
            ),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=self.DUMMY_BYOR_KEY,
            fallback_is_managed_key=False,
        )
        assert _key_of(resolved) == real_managed_key.get_secret_value()

    def test_byor_fallback_does_not_fill_keyless_managed_profile(self):
        """A keyless managed profile is not poisoned with a BYOR key.

        Sending no credential is preferable to sending a third-party key the
        proxy will reject; a stale/absent managed key is healed by the runtime
        rotation-refresh path, whereas a poisoned key is not.
        """
        resolved = resolve_profile_llm(
            LLM(model='openhands/claude-opus-4-8', usage_id='test'),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=self.DUMMY_BYOR_KEY,
            fallback_is_managed_key=False,
        )
        assert _key_of(resolved) is None

    def test_managed_fallback_still_overrides_when_flag_true(self):
        """Default flag preserves the rotation-safety override for managed keys."""
        real_managed_key = SecretStr('sk-real-managed-key')
        resolved = resolve_profile_llm(
            LLM(
                model='openhands/claude-sonnet-4-5-20250929',
                api_key=real_managed_key,
                usage_id='test',
            ),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=CURRENT_KEY,
            fallback_is_managed_key=True,
        )
        assert _key_of(resolved) == CURRENT_KEY.get_secret_value()


class TestByorProfiles:
    """BYOR profiles keep their own key — unchanged behaviour."""

    def test_byor_key_is_preserved(self):
        byor_key = SecretStr('sk-customer-anthropic-key')
        resolved = resolve_profile_llm(
            LLM(
                model='anthropic/claude-sonnet-4-5-20250929',
                base_url=BYOR_URL,
                api_key=byor_key,
                usage_id='test',
            ),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=CURRENT_KEY,
        )
        assert _key_of(resolved) == byor_key.get_secret_value()

    def test_keyless_byor_profile_falls_back(self):
        """Unchanged: a keyless profile takes the fallback regardless of routing."""
        resolved = resolve_profile_llm(
            LLM(
                model='anthropic/claude-sonnet-4-5-20250929',
                base_url=BYOR_URL,
                usage_id='test',
            ),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=CURRENT_KEY,
        )
        assert _key_of(resolved) == CURRENT_KEY.get_secret_value()


class TestStreamingIsForced:
    def test_stream_forced_on(self):
        resolved = resolve_profile_llm(
            LLM(model='openhands/claude-opus-4-8', usage_id='test'),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=CURRENT_KEY,
        )
        assert resolved.stream is True
