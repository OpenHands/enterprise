"""Unit tests for OrgSecretsStore (org-shared secrets CRUD)."""

from uuid import UUID

import pytest
from pydantic import SecretStr

from openhands.app_server.secrets.secrets_models import CustomSecretScope
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from storage.org_secrets_store import (
    OrgSecretAlreadyExistsError,
    OrgSecretNotFoundError,
    OrgSecretsStore,
)


def _make_jwt_service() -> JwtService:
    key = EncryptionKey(kid='test', key=SecretStr('test_secret'), active=True)
    return JwtService(keys=[key])


@pytest.fixture
def jwt_svc():
    return _make_jwt_service()


@pytest.fixture
def org_secrets_store(async_session_maker, jwt_svc, create_org):
    """OrgSecretsStore with test session maker and a real org."""
    import storage.org_secrets_store as store_module

    store_module.a_session_maker = async_session_maker
    org = create_org(id=UUID('c3333333-3333-3333-3333-333333333333'))
    return OrgSecretsStore(org.id, jwt_svc)


class TestOrgSecretsStoreCRUD:
    @pytest.mark.asyncio
    async def test_create_and_list(self, org_secrets_store):
        """Create a shared secret, then list it."""
        await org_secrets_store.create_shared(
            name='ORG_TOKEN',
            value='secret_value',
            description='org-wide token',
            created_by_user_id='admin-id',
        )

        items = await org_secrets_store.list_shared()
        assert len(items) == 1
        assert items[0].name == 'ORG_TOKEN'
        assert items[0].description == 'org-wide token'
        assert items[0].scope == CustomSecretScope.ORGANIZATION

    @pytest.mark.asyncio
    async def test_create_duplicate_raises(self, org_secrets_store):
        """Creating a duplicate name raises OrgSecretAlreadyExistsError."""
        await org_secrets_store.create_shared(
            name='DUP_TOKEN',
            value='val1',
            description=None,
            created_by_user_id='admin-id',
        )

        with pytest.raises(OrgSecretAlreadyExistsError):
            await org_secrets_store.create_shared(
                name='DUP_TOKEN',
                value='val2',
                description=None,
                created_by_user_id='admin-id',
            )

    @pytest.mark.asyncio
    async def test_update_name_and_description(self, org_secrets_store):
        """update_shared changes name and description without touching the value."""
        await org_secrets_store.create_shared(
            name='OLD_NAME',
            value='original_value',
            description='old desc',
            created_by_user_id='admin-id',
        )

        await org_secrets_store.update_shared(
            current_name='OLD_NAME',
            new_name='NEW_NAME',
            description='new desc',
        )

        # Old name should be gone
        old = await org_secrets_store.get_shared('OLD_NAME')
        assert old is None

        # New name should exist
        new = await org_secrets_store.get_shared('NEW_NAME')
        assert new is not None
        assert new.description == 'new desc'

        # Value should be unchanged (decrypted)
        decrypted = org_secrets_store._jwt_svc.decrypt_value(new.secret_value)
        assert decrypted == 'original_value'

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(self, org_secrets_store):
        """Updating a non-existent secret raises OrgSecretNotFoundError."""
        with pytest.raises(OrgSecretNotFoundError):
            await org_secrets_store.update_shared(
                current_name='NONEXISTENT',
                new_name='WHATEVER',
            )

    @pytest.mark.asyncio
    async def test_update_to_existing_name_raises(self, org_secrets_store):
        """Renaming to an existing name raises OrgSecretAlreadyExistsError."""
        await org_secrets_store.create_shared(
            name='SECRET_A',
            value='val_a',
            description=None,
            created_by_user_id='admin-id',
        )
        await org_secrets_store.create_shared(
            name='SECRET_B',
            value='val_b',
            description=None,
            created_by_user_id='admin-id',
        )

        with pytest.raises(OrgSecretAlreadyExistsError):
            await org_secrets_store.update_shared(
                current_name='SECRET_A',
                new_name='SECRET_B',
            )

    @pytest.mark.asyncio
    async def test_delete(self, org_secrets_store):
        """Delete removes the secret."""
        await org_secrets_store.create_shared(
            name='TO_DELETE',
            value='val',
            description=None,
            created_by_user_id='admin-id',
        )

        await org_secrets_store.delete_shared('TO_DELETE')

        result = await org_secrets_store.get_shared('TO_DELETE')
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, org_secrets_store):
        """Deleting a non-existent secret raises OrgSecretNotFoundError."""
        with pytest.raises(OrgSecretNotFoundError):
            await org_secrets_store.delete_shared('NONEXISTENT')

    @pytest.mark.asyncio
    async def test_list_shared_returns_no_values(self, org_secrets_store):
        """list_shared must never include secret values."""
        await org_secrets_store.create_shared(
            name='SECRET_WITH_VALUE',
            value='super_secret_value_123',
            description='desc',
            created_by_user_id='admin-id',
        )

        items = await org_secrets_store.list_shared()
        assert len(items) == 1
        # CustomSecretWithoutValue has no 'value' or 'secret' field
        assert not hasattr(items[0], 'value')
        assert not hasattr(items[0], 'secret')
        # Ensure the value is not in the model dump
        dumped = items[0].model_dump()
        assert 'value' not in dumped
        assert 'secret' not in dumped
        assert 'super_secret_value_123' not in str(dumped)

    @pytest.mark.asyncio
    async def test_load_shared_values_returns_decrypted(self, org_secrets_store):
        """load_shared_values returns decrypted CustomSecret objects for runtime use."""
        await org_secrets_store.create_shared(
            name='RUNTIME_SECRET',
            value='runtime_value',
            description='for runtime',
            created_by_user_id='admin-id',
        )

        secrets = await org_secrets_store.load_shared_values()
        assert 'RUNTIME_SECRET' in secrets
        assert secrets['RUNTIME_SECRET'].secret.get_secret_value() == 'runtime_value'
        assert secrets['RUNTIME_SECRET'].description == 'for runtime'

    @pytest.mark.asyncio
    async def test_list_empty(self, org_secrets_store):
        """list_shared returns empty list when no shared secrets exist."""
        items = await org_secrets_store.list_shared()
        assert items == []

    @pytest.mark.asyncio
    async def test_personal_secret_not_in_shared_list(
        self, org_secrets_store, async_session_maker
    ):
        """A personal secret (is_org_shared=False) must not appear in list_shared."""
        from storage.stored_custom_secrets import StoredCustomSecrets

        # Insert a personal secret in the same org
        async with async_session_maker() as session:
            personal = StoredCustomSecrets(
                keycloak_user_id='some-user',
                org_id=org_secrets_store.org_id,
                secret_name='PERSONAL_ONLY',
                secret_value=org_secrets_store._jwt_svc.encrypt_value('val'),
                description='personal',
                is_org_shared=False,
            )
            session.add(personal)
            await session.commit()

        items = await org_secrets_store.list_shared()
        names = [item.name for item in items]
        assert 'PERSONAL_ONLY' not in names
