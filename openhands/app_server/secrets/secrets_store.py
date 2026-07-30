from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from openhands.app_server.secrets.secrets_models import Secrets


class CredentialVersionConflict(Exception):
    pass


# Names whose value a whole-document ``store`` must never write. A runtime can
# rotate these behind the app's back, so a request that echoes back the document
# it read would otherwise restore a consumed credential.
PROTECTED_CREDENTIAL_NAMES = frozenset({'CODEX_AUTH_JSON'})


def is_protected_credential(name: str) -> bool:
    return name in PROTECTED_CREDENTIAL_NAMES


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
        """Store secrets, carrying ``PROTECTED_CREDENTIAL_NAMES`` forward untouched."""

    async def replace_protected_credential(
        self,
        name: str,
        value: str,
        description: str | None = None,
    ) -> None:
        """Write a protected credential, the only user-facing way to change one."""
        raise NotImplementedError

    async def delete_protected_credential(self, name: str) -> None:
        raise NotImplementedError

    async def load_versioned(
        self,
        name: str,
        organization_id: UUID | None = None,
    ) -> tuple[str, str]:
        raise NotImplementedError

    async def replace_versioned(
        self,
        name: str,
        expected_version: str,
        value: str,
        organization_id: UUID | None = None,
    ) -> str:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    async def get_instance(cls, user_id: str | None) -> SecretsStore:
        """Get a store for the user represented by the token given.

        TODO: This method should be replaced with dependency injection.
        """
