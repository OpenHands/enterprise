"""Store for organization-shared custom secrets.

Org-shared secrets are usable by all members of the org but can only be
created/edited/deleted by Admins and Owners (``MANAGE_ORG_SECRETS``).
The secret value is encrypted at rest and never exposed via the listing
API — only the name and description are returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from openhands.app_server.secrets.secrets_models import (
    CustomSecret,
    CustomSecretScope,
    CustomSecretWithoutValue,
)
from openhands.app_server.services.jwt_service import JwtService
from storage.database import a_session_maker
from storage.stored_custom_secrets import StoredCustomSecrets


class OrgSecretNotFoundError(Exception):
    """Raised when an org-shared secret is not found."""


class OrgSecretAlreadyExistsError(Exception):
    """Raised when creating an org-shared secret whose name is taken."""


@dataclass
class OrgSecretsStore:
    """Row-level CRUD for org-shared secrets.

    Unlike ``SaasSecretsStore`` (which does whole-replacement of a user's
    personal secrets), this store operates on individual rows so that
    shared secrets are independent of one another and of personal secrets.
    """

    org_id: UUID
    _jwt_svc: JwtService

    async def list_shared(self) -> list[CustomSecretWithoutValue]:
        """List all org-shared secrets (names + descriptions only)."""
        async with a_session_maker() as session:
            query = select(StoredCustomSecrets).filter(
                StoredCustomSecrets.org_id == self.org_id,
                StoredCustomSecrets.is_org_shared.is_(True),
            )
            result = await session.execute(query)
            rows = result.scalars().all()

            return [
                CustomSecretWithoutValue.model_construct(
                    name=row.secret_name,
                    description=self._jwt_svc.decrypt_value(row.description)
                    if row.description
                    else None,
                    scope=CustomSecretScope.ORGANIZATION,
                )
                for row in rows
            ]

    async def get_shared(self, name: str) -> StoredCustomSecrets | None:
        """Get a single org-shared secret row by name (raw, encrypted)."""
        async with a_session_maker() as session:
            query = select(StoredCustomSecrets).filter(
                StoredCustomSecrets.org_id == self.org_id,
                StoredCustomSecrets.is_org_shared.is_(True),
                StoredCustomSecrets.secret_name == name,
            )
            result = await session.execute(query)
            return result.scalars().first()

    async def create_shared(
        self,
        name: str,
        value: str,
        description: str | None,
        created_by_user_id: str,
    ) -> None:
        """Create a new org-shared secret.

        The value is encrypted before storage. Raises
        ``OrgSecretAlreadyExistsError`` if a shared secret with this name
        already exists in the org.
        """
        existing = await self.get_shared(name)
        if existing is not None:
            raise OrgSecretAlreadyExistsError(
                f'Org-shared secret {name!r} already exists'
            )

        encrypted_value = self._jwt_svc.encrypt_value(value)
        encrypted_description = (
            self._jwt_svc.encrypt_value(description) if description else None
        )
        async with a_session_maker() as session:
            secret = StoredCustomSecrets(
                keycloak_user_id=created_by_user_id,
                org_id=self.org_id,
                secret_name=name,
                secret_value=encrypted_value,
                description=encrypted_description,
                is_org_shared=True,
            )
            session.add(secret)
            try:
                await session.commit()
            except IntegrityError as exc:
                raise OrgSecretAlreadyExistsError(
                    f'Org-shared secret {name!r} already exists'
                ) from exc

    async def update_shared(
        self,
        current_name: str,
        new_name: str | None = None,
        description: str | None = None,
    ) -> None:
        """Update an org-shared secret's name and/or description.

        The secret value is never modified through this method — the
        write-once, never-read guarantee means only name/description can
        be changed after creation. To rotate the value, delete and
        re-create.
        """
        row = await self.get_shared(current_name)
        if row is None:
            raise OrgSecretNotFoundError(
                f'Org-shared secret {current_name!r} not found'
            )

        if new_name is not None and new_name != current_name:
            # Ensure the new name isn't already taken
            clash = await self.get_shared(new_name)
            if clash is not None:
                raise OrgSecretAlreadyExistsError(
                    f'Org-shared secret {new_name!r} already exists'
                )
            row.secret_name = new_name

        if description is not None:
            row.description = (
                self._jwt_svc.encrypt_value(description) if description else None
            )

        async with a_session_maker() as session:
            await session.merge(row)
            await session.commit()

    async def delete_shared(self, name: str) -> None:
        """Delete an org-shared secret by name."""
        row = await self.get_shared(name)
        if row is None:
            raise OrgSecretNotFoundError(f'Org-shared secret {name!r} not found')

        async with a_session_maker() as session:
            delete_query = delete(StoredCustomSecrets).filter(
                StoredCustomSecrets.id == row.id,
            )
            await session.execute(delete_query)
            await session.commit()

    async def load_shared_values(self) -> dict[str, CustomSecret]:
        """Load decrypted org-shared secrets for runtime use.

        Returns a dict of ``{name: CustomSecret}`` with decrypted values.
        This is the runtime path — never expose these values through the
        API listing endpoints.
        """
        async with a_session_maker() as session:
            query = select(StoredCustomSecrets).filter(
                StoredCustomSecrets.org_id == self.org_id,
                StoredCustomSecrets.is_org_shared.is_(True),
            )
            result = await session.execute(query)
            rows = result.scalars().all()

            secrets: dict[str, CustomSecret] = {}
            for row in rows:
                decrypted = self._jwt_svc.decrypt_value(row.secret_value)
                secrets[row.secret_name] = CustomSecret(
                    secret=SecretStr(decrypted),
                    description=self._jwt_svc.decrypt_value(row.description)
                    if row.description
                    else '',
                )
            return secrets

    @classmethod
    async def get_instance(cls, org_id: UUID) -> OrgSecretsStore:
        from storage.encrypt_utils import get_jwt_service

        return cls(org_id, get_jwt_service())
