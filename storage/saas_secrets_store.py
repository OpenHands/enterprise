from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import delete, select

from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.secrets.secrets_store import SecretsStore
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.composition import get_auth_services
from storage.database import a_session_maker
from storage.stored_custom_secrets import StoredCustomSecrets


def _resolve_unique_name(name: str, taken: set[str]) -> str:
    """Return ``name`` if not in ``taken``, else append ``_2``, ``_3``, …

    The stored name is left untouched — this is a display/runtime-only
    deduplication so that a personal secret and an org-shared secret with
    the same base name are both usable as distinct env vars.
    """
    if name not in taken:
        return name
    suffix = 2
    while f'{name}_{suffix}' in taken:
        suffix += 1
    return f'{name}_{suffix}'


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
        user = await get_auth_services().accounts.get_user_by_id(self.user_id)
        org_id = self.effective_org_id or (user.current_org_id if user else None)

        async with a_session_maker() as session:
            # Fetch the user's personal secrets (is_org_shared=False)
            personal_query = select(StoredCustomSecrets).filter(
                StoredCustomSecrets.keycloak_user_id == self.user_id,
                StoredCustomSecrets.is_org_shared.is_(False),
            )
            if org_id is not None:
                personal_query = personal_query.filter(
                    StoredCustomSecrets.org_id == org_id
                )
            personal_result = await session.execute(personal_query)
            personal_secrets = personal_result.scalars().all()

            # Fetch org-shared secrets (is_org_shared=True) for this org.
            # Available to all members; the value is decrypted and surfaced
            # to the runtime but never exposed via the listing API.
            shared_secrets: list[StoredCustomSecrets] = []
            if org_id is not None:
                shared_query = select(StoredCustomSecrets).filter(
                    StoredCustomSecrets.org_id == org_id,
                    StoredCustomSecrets.is_org_shared.is_(True),
                )
                shared_result = await session.execute(shared_query)
                shared_secrets = shared_result.scalars().all()

            all_secrets = list(personal_secrets) + list(shared_secrets)

            if not all_secrets:
                return Secrets()

            # Merge personal + shared, applying suffix dedup on name
            # collisions. Personal secrets keep the bare name; org-shared
            # secrets that collide get ``_2``, ``_3``, … appended.
            kwargs: dict[str, dict[str, str | None]] = {}
            taken_names: set[str] = set()

            # Personal first — they win the bare name.
            for secret in personal_secrets:
                effective_name = _resolve_unique_name(secret.secret_name, taken_names)
                kwargs[effective_name] = {
                    'secret': secret.secret_value,
                    'description': secret.description,
                }
                taken_names.add(effective_name)

            # Shared next — suffixed on collision.
            for secret in shared_secrets:
                effective_name = _resolve_unique_name(secret.secret_name, taken_names)
                kwargs[effective_name] = {
                    'secret': secret.secret_value,
                    'description': secret.description,
                }
                taken_names.add(effective_name)

            self._decrypt_kwargs(kwargs)

            return Secrets(custom_secrets=kwargs)  # type: ignore[arg-type]

    async def list_personal(
        self,
    ) -> list[tuple[str, str | None]]:
        """Return ``(name, description)`` for the user's personal secrets.

        Org-shared secrets are excluded. Used by the listing API to show
        scope badges — the runtime ``load()`` merges both, but the listing
        API needs to distinguish them.
        """
        if not self.user_id:
            return []
        user = await get_auth_services().accounts.get_user_by_id(self.user_id)
        org_id = self.effective_org_id or (user.current_org_id if user else None)

        async with a_session_maker() as session:
            query = select(StoredCustomSecrets).filter(
                StoredCustomSecrets.keycloak_user_id == self.user_id,
                StoredCustomSecrets.is_org_shared.is_(False),
            )
            if org_id is not None:
                query = query.filter(StoredCustomSecrets.org_id == org_id)
            result = await session.execute(query)
            rows = result.scalars().all()
            return [
                (
                    row.secret_name,
                    self._jwt_svc.decrypt_value(row.description)
                    if row.description
                    else None,
                )
                for row in rows
            ]

    async def store(self, item: Secrets) -> None:
        user = await get_auth_services().accounts.get_user_by_id(self.user_id)
        if user is None:
            raise ValueError(f'User not found: {self.user_id}')
        org_id = self.effective_org_id or user.current_org_id

        async with a_session_maker() as session:
            # Incoming secrets are always the most updated ones.
            # Delete existing **personal** records for this user AND
            # organization only — org-shared secrets (is_org_shared=True)
            # must survive personal-secret rewrites.
            # org_id is always set: it's either the effective org from
            # the request or the user's non-nullable current_org_id.
            delete_query = delete(StoredCustomSecrets).filter(
                StoredCustomSecrets.keycloak_user_id == self.user_id,
                StoredCustomSecrets.org_id == org_id,
                StoredCustomSecrets.is_org_shared.is_(False),
            )
            await session.execute(delete_query)

            # Prepare the new secrets data
            kwargs = item.model_dump(context={'expose_secrets': True})
            del kwargs[
                'provider_tokens'
            ]  # Assuming provider_tokens is not part of custom_secrets
            self._encrypt_kwargs(kwargs)

            secrets_json = kwargs.get('custom_secrets', {})

            # Extract the secrets into tuples for insertion or updating
            secret_tuples = []
            for secret_name, secret_info in secrets_json.items():
                secret_value = secret_info.get('secret')
                description = secret_info.get('description')

                secret_tuples.append((secret_name, secret_value, description))

            # Add the new secrets
            for secret_name, secret_value, description in secret_tuples:
                new_secret = StoredCustomSecrets(
                    keycloak_user_id=self.user_id,
                    org_id=org_id,
                    secret_name=secret_name,
                    secret_value=secret_value,
                    description=description,
                )
                session.add(new_secret)

            await session.commit()

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
