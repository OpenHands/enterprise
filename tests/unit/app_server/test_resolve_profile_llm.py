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


class TestProviderConnections:
    """A linked profile takes its key and base_url only from its connection."""

    CONNECTION_URL = 'https://llm-proxy.example.test'
    CONNECTION_KEY = SecretStr('sk-connection-key')

    def _connections(self, **overrides):
        from openhands.app_server.settings.provider_connections import (
            ProviderConnection,
            ProviderConnections,
        )

        fields = {
            'id': 'proxy',
            'display_name': 'Proxy',
            'api_key': self.CONNECTION_KEY,
            'base_url': self.CONNECTION_URL,
            **overrides,
        }
        return ProviderConnections(connections={'proxy': ProviderConnection(**fields)})

    def test_linked_profile_takes_the_connection_key_and_base_url(self):
        resolved = resolve_profile_llm(
            LLM(model='anthropic/claude-test', provider_connection_id='proxy'),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=CURRENT_KEY,
            connections=self._connections(),
        )
        assert resolved.base_url == self.CONNECTION_URL
        assert _key_of(resolved) == self.CONNECTION_KEY.get_secret_value()

    def test_connection_on_the_managed_proxy_keeps_the_connection_key(self):
        """The managed-proxy override must not replace a connection key."""
        resolved = resolve_profile_llm(
            LLM(model='openhands/claude-test', provider_connection_id='proxy'),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=CURRENT_KEY,
            connections=self._connections(base_url=MANAGED_URL),
        )
        assert resolved.base_url == MANAGED_URL
        assert _key_of(resolved) == self.CONNECTION_KEY.get_secret_value()

    def test_missing_connection_gets_no_key(self):
        from openhands.app_server.settings.provider_connections import (
            ProviderConnections,
        )

        resolved = resolve_profile_llm(
            LLM(model='anthropic/claude-test', provider_connection_id='gone'),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=CURRENT_KEY,
            connections=ProviderConnections(),
        )
        assert resolved.api_key is None

    def test_linked_profile_without_connections_gets_no_key(self):
        resolved = resolve_profile_llm(
            LLM(model='anthropic/claude-test', provider_connection_id='proxy'),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=CURRENT_KEY,
        )
        assert resolved.api_key is None

    def test_missing_connection_raises_when_strict(self):
        import pytest

        from openhands.app_server.settings.provider_connections import (
            ProviderConnectionNotFoundError,
            ProviderConnections,
        )

        with pytest.raises(ProviderConnectionNotFoundError):
            resolve_profile_llm(
                LLM(model='anthropic/claude-test', provider_connection_id='gone'),
                managed_proxy_url=MANAGED_URL,
                fallback_api_key=CURRENT_KEY,
                connections=ProviderConnections(),
                strict=True,
            )

    def test_unlinked_profile_ignores_connections(self):
        resolved = resolve_profile_llm(
            LLM(model='anthropic/claude-test', base_url=BYOR_URL),
            managed_proxy_url=MANAGED_URL,
            fallback_api_key=CURRENT_KEY,
            connections=self._connections(),
        )
        assert resolved.base_url == BYOR_URL
        assert _key_of(resolved) == CURRENT_KEY.get_secret_value()
