from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import delete, select

from openhands.app_server.integrations.provider import (
    PROVIDER_TOKEN_TYPE,
    CustomSecret,
    ProviderType,
)
from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.secrets.secrets_store import SecretsStore
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.settings.settings_models import POSTProviderModel
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.auth_config import ENABLE_KEYCLOAK
from storage.database import a_session_maker
from storage.stored_custom_secrets import StoredCustomSecrets
from storage.user_store import UserStore

type SecretTree = dict[str, str | None | SecretTree]


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

    async def load(self) -> Secrets | None:
        if not self.user_id:
            return None
        user = await UserStore.get_user_by_id(self.user_id)
        org_id = self.effective_org_id or (user.current_org_id if user else None)

        async with a_session_maker() as session:
            # Fetch all secrets for the given user ID
            query = select(StoredCustomSecrets).filter(
                StoredCustomSecrets.keycloak_user_id == self.user_id
            )
            if org_id is not None:
                query = query.filter(StoredCustomSecrets.org_id == org_id)
            result = await session.execute(query)
            settings = result.scalars().all()

            custom_secrets = {
                secret.secret_name: CustomSecret(
                    secret=SecretStr(self._jwt_svc.decrypt_value(secret.secret_value)),
                    description=self._jwt_svc.decrypt_value(secret.description)
                    if secret.description is not None
                    else '',
                )
                for secret in settings
            }

            provider_tokens: PROVIDER_TOKEN_TYPE = {}
            if not ENABLE_KEYCLOAK:
                from server.services.native_git_credentials import (
                    get_native_git_service,
                )

                provider_tokens = await get_native_git_service().get_provider_tokens(
                    self.user_id
                )
            return Secrets(
                custom_secrets=custom_secrets, provider_tokens=provider_tokens
            )

    async def store_native_provider_tokens(self, item: POSTProviderModel) -> None:
        from server.services.native_git_credentials import get_native_git_service
        from server.services.native_git_provider import GitCredentialError

        service = get_native_git_service()
        for provider, credential in (item.provider_tokens or {}).items():
            if not credential.token:
                raise GitCredentialError('credential_required')
            token = credential.token.get_secret_value()
            email = None
            if provider == ProviderType.BITBUCKET:
                if ':' not in token:
                    raise GitCredentialError('bitbucket_email_required')
                email, token = token.split(':', 1)
            await service.connect_manual(
                self.user_id, provider.value, token, credential.host, email
            )

    async def unset_native_provider_tokens(self) -> None:
        from server.services.native_git_credentials import get_native_git_service

        service = get_native_git_service()
        for connection in (await service.list_connections(self.user_id))['connections']:
            await service.disconnect(self.user_id, connection['provider'])

    async def store(self, item: Secrets) -> None:
        user = await UserStore.get_user_by_id(self.user_id)
        if user is None:
            raise ValueError(f'User not found: {self.user_id}')
        org_id = self.effective_org_id or user.current_org_id

        async with a_session_maker() as session:
            # Incoming secrets are always the most updated ones
            # Delete existing records for this user AND organization only
            # org_id is always set: it's either the effective org from
            # the request or the user's non-nullable current_org_id.
            delete_query = delete(StoredCustomSecrets).filter(
                StoredCustomSecrets.keycloak_user_id == self.user_id,
                StoredCustomSecrets.org_id == org_id,
            )
            await session.execute(delete_query)

            for secret_name, secret in item.custom_secrets.items():
                session.add(
                    StoredCustomSecrets(
                        keycloak_user_id=self.user_id,
                        org_id=org_id,
                        secret_name=secret_name,
                        secret_value=self._jwt_svc.encrypt_value(
                            secret.secret.get_secret_value()
                        ),
                        description=self._jwt_svc.encrypt_value(secret.description)
                        if secret.description is not None
                        else None,
                    )
                )

            await session.commit()

    def _decrypt_kwargs(self, kwargs: SecretTree) -> None:
        for key, value in kwargs.items():
            if isinstance(value, dict):
                self._decrypt_kwargs(value)
                continue

            if value is None:
                kwargs[key] = value
            else:
                kwargs[key] = self._jwt_svc.decrypt_value(value)

    def _encrypt_kwargs(self, kwargs: SecretTree) -> None:
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
