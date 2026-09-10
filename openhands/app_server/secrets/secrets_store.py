from __future__ import annotations

from abc import ABC, abstractmethod

from openhands.app_server.integrations.provider import PROVIDER_TOKEN_TYPE
from openhands.app_server.secrets.secrets_models import Secrets


class SecretsStore(ABC):
    """Abstract base class for storing user secrets.

    This is an extension point in OpenHands that allows applications to customize how
    user secrets are stored. Applications can substitute their own implementation by:
    1. Creating a class that inherits from SecretsStore
    2. Implementing all required methods
    3. Setting server_config.secret_store_class to the fully qualified name of the class

    The class is instantiated via get_impl() in openhands.app_server.shared.py.

    The implementation may or may not support multiple users depending on the environment.
    """

    @abstractmethod
    async def load(self) -> Secrets | None:
        """Load secrets."""

    @abstractmethod
    async def store(self, secrets: Secrets) -> None:
        """Store secrets."""

    async def store_provider_tokens(self, provider_tokens: PROVIDER_TOKEN_TYPE) -> None:
        """Validate and replace provider connections under the caller's write lock."""
        from openhands.app_server.errors import AuthError
        from openhands.app_server.integrations.utils import validate_provider_token

        existing = await self.load() or Secrets()
        updated = {}
        for provider, incoming in provider_tokens.items():
            previous = existing.provider_tokens.get(provider)
            token = incoming.token or (previous.token if previous else None)
            if token and (
                incoming.token or (previous and previous.host != incoming.host)
            ):
                confirmed = await validate_provider_token(token, incoming.host)
                if confirmed != provider:
                    raise AuthError(
                        f'Invalid token. Please make sure it is a valid {provider.value} token.'
                    )
            updated[provider] = incoming.model_copy(update={'token': token})
        await self.store(existing.model_copy(update={'provider_tokens': updated}))

    async def delete_provider_tokens(self) -> None:
        """Remove provider connections without requiring a working provider token."""
        existing = await self.load()
        if existing:
            await self.store(existing.model_copy(update={'provider_tokens': {}}))

    @classmethod
    @abstractmethod
    async def get_instance(cls, user_id: str | None) -> SecretsStore:
        """Get a store for the user represented by the token given.

        TODO: This method should be replaced with dependency injection.
        """
