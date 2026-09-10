"""Translate SDK failures at the compatibility boundary."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from tenacity import RetryError

from keycloak.exceptions import KeycloakConnectionError, KeycloakError
from server.auth.contracts import AuthenticationUnavailable, InvalidCredentials


def is_transient_keycloak_error(exc: BaseException) -> bool:
    while isinstance(exc, RetryError):
        retry_exc = exc.last_attempt.exception()
        if retry_exc is None:
            return False
        exc = retry_exc
    if isinstance(exc, (KeycloakConnectionError, AuthenticationUnavailable)):
        return True
    if isinstance(exc, KeycloakError) and exc.response_code is not None:
        return exc.response_code in (408, 429) or 500 <= exc.response_code < 600
    return False


P = ParamSpec('P')
T = TypeVar('T')


def translate_keycloak_errors(
    operation: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    @wraps(operation)
    async def translated(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await operation(*args, **kwargs)
        except (KeycloakError, RetryError) as exc:
            if is_transient_keycloak_error(exc):
                raise AuthenticationUnavailable(
                    'Authentication service temporarily unavailable'
                ) from None
            raise InvalidCredentials(
                'Invalid or expired Keycloak credentials'
            ) from None

    return translated
