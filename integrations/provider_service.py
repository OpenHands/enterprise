"""Keep repository authentication failures separate from browser sessions."""

from typing import Any, NoReturn

import httpx

from openhands.app_server.integrations.service_types import (
    AuthenticationError,
    ProviderTimeoutError,
    RateLimitError,
    UnknownException,
)
from server.auth.contracts import AuthenticationUnavailable, ProviderReconnectRequired
from server.auth.provider_credentials import ProviderCredentialService


class ProviderCredentialErrorMixin:
    async def _make_request(self, *args: Any, **kwargs: Any) -> tuple[Any, dict]:
        try:
            return await super()._make_request(*args, **kwargs)  # type: ignore[misc]
        except (
            AuthenticationError,
            ProviderTimeoutError,
            RateLimitError,
            UnknownException,
        ) as exc:
            self._raise_credential_error(exc)

    async def execute_graphql_query(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return await super().execute_graphql_query(*args, **kwargs)  # type: ignore[misc]
        except (
            AuthenticationError,
            ProviderTimeoutError,
            RateLimitError,
            UnknownException,
        ) as exc:
            self._raise_credential_error(exc)

    @staticmethod
    def _raise_credential_error(exc: Exception) -> NoReturn:
        cause = exc.__cause__
        if isinstance(cause, httpx.HTTPStatusError):
            ProviderCredentialService._raise_provider_error(cause)
        if isinstance(exc, AuthenticationError) and not isinstance(
            cause, httpx.RequestError
        ):
            raise ProviderReconnectRequired(
                'Provider token is invalid or revoked. Reconnect the provider.'
            ) from exc
        raise AuthenticationUnavailable(
            'Git provider is temporarily unavailable'
        ) from exc
