"""Integration test: verify org-scoped secrets are loaded into conversation runtimes.

This test reproduces the issue reported where:
- Org secrets are created successfully via POST /api/organizations/{org_id}/secrets
- They appear in GET /api/v1/secrets/search with scope="organization"  
- But they DON'T appear as environment variables in new conversations

Expected behavior: All org-shared secrets should be available as env vars in conversations.
"""

import pytest
from uuid import UUID

from storage.org_secrets_store import OrgSecretsStore
from storage.saas_secrets_store import SaasSecretsStore
from storage.stored_custom_secrets import StoredCustomSecrets
from storage.database import a_session_maker


@pytest.mark.asyncio
async def test_org_secrets_loaded_in_saas_secrets_store(
    test_user_id: str,
    test_org_id: UUID,
):
    """Verify that SaasSecretsStore.load() includes org-shared secrets.
    
    This is the critical path that provides secrets to conversation runtimes.
    """
    # Create an org-shared secret
    org_store = await OrgSecretsStore.get_instance(test_org_id)
    await org_store.create_shared(
        name="TEST_ORG_SECRET",
        value="org_secret_value",
        description="Test org secret",
        created_by_user_id=test_user_id,
    )
    
    # Create a personal secret for the same user
    saas_store = await SaasSecretsStore.get_instance(
        user_id=test_user_id,
        effective_org_id=test_org_id,
    )
    from openhands.app_server.secrets.secrets_models import Secrets, CustomSecret
    from pydantic import SecretStr
    
    personal_secrets = Secrets(
        custom_secrets={
            "TEST_PERSONAL_SECRET": CustomSecret(
                secret=SecretStr("personal_secret_value"),
                description="Test personal secret",
            )
        }
    )
    await saas_store.store(personal_secrets)
    
    # Load secrets (this is what happens when a conversation starts)
    loaded_secrets = await saas_store.load()
    
    assert loaded_secrets is not None
    assert "TEST_PERSONAL_SECRET" in loaded_secrets.custom_secrets
    assert "TEST_ORG_SECRET" in loaded_secrets.custom_secrets
    
    # Verify values
    personal_val = loaded_secrets.custom_secrets["TEST_PERSONAL_SECRET"]["secret"]
    org_val = loaded_secrets.custom_secrets["TEST_ORG_SECRET"]["secret"]
    
    assert personal_val == "personal_secret_value"
    assert org_val == "org_secret_value"


@pytest.mark.asyncio
async def test_multiple_org_secrets_all_loaded(
    test_user_id: str,
    test_org_id: UUID,
):
    """Verify that ALL org-shared secrets are loaded, not just one.
    
    This reproduces the reported issue where only TIMS_SECRET_VALUE appears
    but PROD_LITELLM_MASTER_KEY and SAURYA_TEST_KEY do not.
    """
    org_store = await OrgSecretsStore.get_instance(test_org_id)
    
    # Create multiple org secrets
    secrets_to_create = [
        ("FIRST_ORG_SECRET", "value1"),
        ("SECOND_ORG_SECRET", "value2"),
        ("THIRD_ORG_SECRET", "value3"),
    ]
    
    for name, value in secrets_to_create:
        await org_store.create_shared(
            name=name,
            value=value,
            description=f"Test {name}",
            created_by_user_id=test_user_id,
        )
    
    # Load via SaasSecretsStore
    saas_store = await SaasSecretsStore.get_instance(
        user_id=test_user_id,
        effective_org_id=test_org_id,
    )
    loaded_secrets = await saas_store.load()
    
    # ALL three should be present
    assert loaded_secrets is not None
    assert "FIRST_ORG_SECRET" in loaded_secrets.custom_secrets
    assert "SECOND_ORG_SECRET" in loaded_secrets.custom_secrets
    assert "THIRD_ORG_SECRET" in loaded_secrets.custom_secrets
    
    # Verify values
    assert loaded_secrets.custom_secrets["FIRST_ORG_SECRET"]["secret"] == "value1"
    assert loaded_secrets.custom_secrets["SECOND_ORG_SECRET"]["secret"] == "value2"
    assert loaded_secrets.custom_secrets["THIRD_ORG_SECRET"]["secret"] == "value3"


@pytest.mark.asyncio
async def test_org_secrets_have_is_org_shared_flag(
    test_user_id: str,
    test_org_id: UUID,
):
    """Verify that org secrets created via OrgSecretsStore have is_org_shared=True.
    
    If the database migration #163 hasn't been run, this column won't exist
    and secrets won't load correctly.
    """
    org_store = await OrgSecretsStore.get_instance(test_org_id)
    await org_store.create_shared(
        name="TEST_FLAG_CHECK",
        value="test_value",
        description=None,
        created_by_user_id=test_user_id,
    )
    
    # Query the database directly
    async with a_session_maker() as session:
        from sqlalchemy import select
        query = select(StoredCustomSecrets).filter(
            StoredCustomSecrets.org_id == test_org_id,
            StoredCustomSecrets.secret_name == "TEST_FLAG_CHECK",
        )
        result = await session.execute(query)
        row = result.scalars().first()
        
        assert row is not None
        assert hasattr(row, 'is_org_shared'), "Migration #163 not applied! is_org_shared column missing"
        assert row.is_org_shared is True, f"Expected is_org_shared=True, got {row.is_org_shared}"


@pytest.mark.asyncio  
async def test_org_secret_created_by_different_user_is_still_loaded(
    test_org_id: UUID,
):
    """Verify that org secrets created by User A are available to User B.
    
    This tests the core value proposition of org-scoped secrets.
    """
    user_a = "user_a_keycloak_id"
    user_b = "user_b_keycloak_id"
    
    # User A creates an org secret
    org_store = await OrgSecretsStore.get_instance(test_org_id)
    await org_store.create_shared(
        name="SHARED_BY_USER_A",
        value="shared_value",
        description="Created by A, used by B",
        created_by_user_id=user_a,
    )
    
    # User B loads secrets
    saas_store_b = await SaasSecretsStore.get_instance(
        user_id=user_b,
        effective_org_id=test_org_id,
    )
    loaded = await saas_store_b.load()
    
    # User B should see the secret
    assert loaded is not None
    assert "SHARED_BY_USER_A" in loaded.custom_secrets
    assert loaded.custom_secrets["SHARED_BY_USER_A"]["secret"] == "shared_value"
