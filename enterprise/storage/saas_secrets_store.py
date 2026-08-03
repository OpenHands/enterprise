from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import delete, select
from storage.database import a_session_maker
from storage.org_member_store import OrgMemberStore
from storage.stored_custom_secrets import StoredCustomSecrets
from storage.user_store import UserStore

from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.secrets.secrets_store import (
    PROTECTED_CREDENTIAL_NAMES,
    CredentialVersionConflict,
    SecretsStore,
)
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.logger import openhands_logger as logger


def _credential_version(row: StoredCustomSecrets) -> str:
    state = f'{row.id}\0{row.secret_value}'.encode()
    return hashlib.sha256(state).hexdigest()


@dataclass
class SaasSecretsStore(SecretsStore):
    user_id: str
    _jwt_svc: JwtService = field(repr=False)
    # When set, overrides the user's `current_org_id` for both load and
    # store. Used to honor a request's effective org (api_key_org_id >
    # X-Org-Id header > user.current_org_id). Secrets are stored per
    # (user_id, org_id), so the effective org must flow through here for
    # the right rows to be read/written.
    effective_org_id: UUID | None = None

    @property
    def supports_protected_credentials(self) -> bool:
        return True

    async def load(self) -> Secrets | None:
        if not self.user_id:
            return None
        user = await UserStore.get_user_by_id(self.user_id)
        org_id = self.effective_org_id or (user.current_org_id if user else None)

        async with a_session_maker() as session:
            query = select(StoredCustomSecrets).filter(
                StoredCustomSecrets.keycloak_user_id == self.user_id
            )
            if org_id is not None:
                query = query.filter(StoredCustomSecrets.org_id == org_id)
            result = await session.execute(query.order_by(StoredCustomSecrets.id))
            settings = result.scalars().all()

            if not settings:
                return Secrets()

            kwargs = {}
            for secret in settings:
                kwargs[secret.secret_name] = {
                    'secret': secret.secret_value,
                    'description': secret.description,
                }

            self._decrypt_kwargs(kwargs)

            return Secrets(custom_secrets=kwargs)  # type: ignore[arg-type]

    async def store(self, item: Secrets):
        user = await UserStore.get_user_by_id(self.user_id)
        if user is None:
            raise ValueError(f'User not found: {self.user_id}')
        org_id = self.effective_org_id or user.current_org_id

        kwargs = item.model_dump(context={'expose_secrets': True})
        del kwargs['provider_tokens']
        # Structural, not conditional: protected names are dropped from the
        # submitted document and their rows excluded from the delete, so no
        # read-modify-write can restore a value a runtime has rotated.
        kwargs['custom_secrets'] = {
            name: info
            for name, info in kwargs.get('custom_secrets', {}).items()
            if name not in PROTECTED_CREDENTIAL_NAMES
        }
        secrets_json = kwargs['custom_secrets']

        async with a_session_maker() as session:
            await session.execute(
                delete(StoredCustomSecrets).filter(
                    StoredCustomSecrets.keycloak_user_id == self.user_id,
                    StoredCustomSecrets.org_id == org_id,
                    StoredCustomSecrets.secret_name.not_in(PROTECTED_CREDENTIAL_NAMES),
                )
            )

            self._encrypt_kwargs(kwargs)

            for secret_name, secret_info in secrets_json.items():
                session.add(
                    StoredCustomSecrets(
                        keycloak_user_id=self.user_id,
                        org_id=org_id,
                        secret_name=secret_name,
                        secret_value=secret_info.get('secret'),
                        description=secret_info.get('description'),
                    )
                )

            await session.commit()

    async def replace_protected_credential(
        self,
        name: str,
        value: str,
        description: str | None = None,
    ) -> None:
        org_id = await self._writable_organization_id()
        encrypted = self._jwt_svc.encrypt_value(value)
        encrypted_description = (
            self._jwt_svc.encrypt_value(description)
            if description is not None
            else None
        )
        async with a_session_maker() as session:
            result = await session.execute(
                self._versioned_query(name, org_id).with_for_update()
            )
            rows = result.scalars().all()
            if rows:
                for row in rows:
                    row.secret_value = encrypted
                    if description is not None:
                        row.description = encrypted_description
            else:
                session.add(
                    StoredCustomSecrets(
                        keycloak_user_id=self.user_id,
                        org_id=org_id,
                        secret_name=name,
                        secret_value=encrypted,
                        description=encrypted_description,
                    )
                )
            await session.commit()

    async def set_protected_credential_description(
        self,
        name: str,
        description: str,
    ) -> None:
        org_id = await self._writable_organization_id()
        encrypted_description = self._jwt_svc.encrypt_value(description)
        async with a_session_maker() as session:
            result = await session.execute(
                self._versioned_query(name, org_id).with_for_update()
            )
            for row in result.scalars().all():
                row.description = encrypted_description
            await session.commit()

    async def delete_protected_credential(self, name: str) -> None:
        org_id = await self._writable_organization_id()
        async with a_session_maker() as session:
            await session.execute(
                delete(StoredCustomSecrets).filter(
                    StoredCustomSecrets.keycloak_user_id == self.user_id,
                    StoredCustomSecrets.org_id == org_id,
                    StoredCustomSecrets.secret_name == name,
                )
            )
            await session.commit()

    async def _writable_organization_id(self) -> UUID:
        """Resolve the org exactly as ``store`` does, not as the callback does.

        The callback rechecks membership because it is authorized by a runtime
        token; these paths are already inside an authenticated user request.
        """
        if self.effective_org_id is not None:
            return self.effective_org_id
        user = await UserStore.get_user_by_id(self.user_id)
        if user is None:
            raise ValueError(f'User not found: {self.user_id}')
        return user.current_org_id

    async def load_versioned(
        self,
        name: str,
        organization_id: UUID | None = None,
    ) -> tuple[str, str]:
        org_id = await self._require_organization_id(name, organization_id)
        async with a_session_maker() as session:
            result = await session.execute(self._versioned_query(name, org_id).limit(1))
            row = result.scalars().first()
            if row is None:
                raise KeyError(name)
            return self._jwt_svc.decrypt_value(row.secret_value), _credential_version(
                row
            )

    async def replace_versioned(
        self,
        name: str,
        expected_version: str,
        value: str,
        organization_id: UUID | None = None,
    ) -> str:
        org_id = await self._require_organization_id(name, organization_id)
        async with a_session_maker() as session:
            result = await session.execute(
                self._versioned_query(name, org_id).with_for_update()
            )
            rows = result.scalars().all()
            if not rows:
                result = await session.execute(
                    self._versioned_query(name, org_id).limit(1)
                )
                if result.scalars().first() is not None:
                    raise CredentialVersionConflict
                raise KeyError(name)

            current_version = _credential_version(rows[0])
            if not hmac.compare_digest(
                current_version.encode(),
                expected_version.encode(errors='surrogatepass'),
            ):
                raise CredentialVersionConflict

            encrypted = self._jwt_svc.encrypt_value(value)
            for row in rows:
                row.secret_value = encrypted
            successor = _credential_version(rows[0])
            await session.commit()
            return successor

    async def _require_organization_id(
        self,
        name: str,
        organization_id: UUID | None,
    ) -> UUID:
        org_id = organization_id or self.effective_org_id
        if org_id is None:
            user = await UserStore.get_user_by_id(self.user_id)
            org_id = user.current_org_id if user else None
        if org_id is None:
            raise KeyError(name)
        try:
            user_id = UUID(self.user_id)
        except (TypeError, ValueError) as exc:
            raise KeyError(name) from exc
        if await OrgMemberStore.get_org_member(org_id, user_id) is None:
            raise KeyError(name)
        return org_id

    def _versioned_query(self, name: str, organization_id: UUID):
        return (
            select(StoredCustomSecrets)
            .filter(
                StoredCustomSecrets.keycloak_user_id == self.user_id,
                StoredCustomSecrets.org_id == organization_id,
                StoredCustomSecrets.secret_name == name,
            )
            .order_by(StoredCustomSecrets.id.desc())
        )

    def _decrypt_kwargs(self, kwargs: dict):
        for key, value in kwargs.items():
            if isinstance(value, dict):
                self._decrypt_kwargs(value)
                continue

            if value is None:
                kwargs[key] = value
            else:
                kwargs[key] = self._jwt_svc.decrypt_value(value)

    def _encrypt_kwargs(self, kwargs: dict):
        for key, value in kwargs.items():
            if isinstance(value, dict):
                self._encrypt_kwargs(value)
                continue

            if value is None:
                kwargs[key] = value
            else:
                kwargs[key] = self._jwt_svc.encrypt_value(value)

    @classmethod
    async def get_instance(  # type: ignore[override]
        cls,
        user_id: str,
        effective_org_id: UUID | None = None,
    ) -> SaasSecretsStore:
        """Get a SaasSecretsStore instance for the given user.

        Args:
            user_id: Keycloak user id.
            effective_org_id: Optional org id resolved from the request
                (see SaasUserAuth.get_effective_org_id). When None the
                store falls back to ``user.current_org_id`` to preserve
                legacy behavior for background / non-request callers
                (e.g. webhook resolvers).

        TODO: This method should be replaced with dependency injection.
        """
        logger.debug(f'saas_secrets_store.get_instance::{user_id}')
        from storage.encrypt_utils import get_jwt_service

        return SaasSecretsStore(
            user_id,
            get_jwt_service(),
            effective_org_id=effective_org_id,
        )
