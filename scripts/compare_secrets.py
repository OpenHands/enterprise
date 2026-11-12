#!/usr/bin/env python3
"""Compare working vs non-working org secrets to find the difference.

This script compares TIMS_SECRET_VALUE (working) against the others (not working)
to identify what's different about it.
"""

import asyncio
import sys
from uuid import UUID

from sqlalchemy import select
from storage.database import a_session_maker
from storage.stored_custom_secrets import StoredCustomSecrets


async def compare_secrets(org_id: UUID):
    """Compare working vs non-working secrets."""
    
    print(f"🔍 Comparing secrets in org {org_id}\n")
    
    working = "TIMS_SECRET_VALUE"
    not_working = [
        "PROD_LITELLM_MASTER_KEY",
        "SAURYA_TEST_KEY", 
        "E2E_BOT_GITHUB_TOKEN",
        "REPORTPORTAL_API_KEY_2",
        "SLACK_BOT_TOKEN_2",
    ]
    
    async with a_session_maker() as session:
        # Get all org secrets
        query = select(StoredCustomSecrets).filter(
            StoredCustomSecrets.org_id == org_id,
            StoredCustomSecrets.secret_name.in_([working] + not_working)
        )
        result = await session.execute(query)
        secrets = {s.secret_name: s for s in result.scalars().all()}
        
        if working not in secrets:
            print(f"❌ Working secret '{working}' not found!")
            return
        
        working_secret = secrets[working]
        
        print(f"📋 Working secret: {working}")
        print(f"   ID: {working_secret.id}")
        print(f"   keycloak_user_id: {working_secret.keycloak_user_id}")
        print(f"   org_id: {working_secret.org_id}")
        print(f"   is_org_shared: {working_secret.is_org_shared}")
        print(f"   description: {working_secret.description[:50] if working_secret.description else None}")
        print()
        
        print("📋 Non-working secrets:\n")
        
        differences = []
        
        for name in not_working:
            if name not in secrets:
                print(f"   ❌ {name} - NOT FOUND IN DATABASE")
                continue
            
            secret = secrets[name]
            print(f"   {name}:")
            print(f"      ID: {secret.id}")
            print(f"      keycloak_user_id: {secret.keycloak_user_id}")
            print(f"      org_id: {secret.org_id}")
            print(f"      is_org_shared: {secret.is_org_shared}")
            print(f"      description: {secret.description[:50] if secret.description else None}")
            
            # Compare fields
            if secret.keycloak_user_id != working_secret.keycloak_user_id:
                differences.append(f"{name}: Different keycloak_user_id ({secret.keycloak_user_id} vs {working_secret.keycloak_user_id})")
            
            if secret.org_id != working_secret.org_id:
                differences.append(f"{name}: Different org_id")
            
            if secret.is_org_shared != working_secret.is_org_shared:
                differences.append(f"{name}: Different is_org_shared ({secret.is_org_shared} vs {working_secret.is_org_shared})")
            
            print()
        
        print("=" * 80)
        if differences:
            print("⚠️  DIFFERENCES FOUND:")
            for diff in differences:
                print(f"   - {diff}")
        else:
            print("✅ All secrets have identical field values")
            print("   → The difference is NOT in the database")
            print("   → Issue must be in code logic or runtime injection")
        print("=" * 80)
        
        # Now test what SaasSecretsStore.load() would return
        print("\n🧪 Testing SaasSecretsStore.load()...")
        
        # We need a user_id - let's try the one from the working secret
        test_user_id = working_secret.keycloak_user_id
        
        from storage.saas_secrets_store import SaasSecretsStore
        
        store = await SaasSecretsStore.get_instance(
            user_id=test_user_id,
            effective_org_id=org_id,
        )
        
        loaded_secrets = await store.load()
        
        if loaded_secrets and loaded_secrets.custom_secrets:
            print(f"\n✅ SaasSecretsStore.load() returned {len(loaded_secrets.custom_secrets)} secrets:")
            
            if working in loaded_secrets.custom_secrets:
                print(f"   ✅ {working} (working secret) - PRESENT")
            else:
                print(f"   ❌ {working} (working secret) - MISSING")
            
            for name in not_working:
                if name in loaded_secrets.custom_secrets:
                    print(f"   ✅ {name} - PRESENT")
                else:
                    print(f"   ❌ {name} - MISSING")
            
            print("\n📊 All secrets returned by load():")
            for name in sorted(loaded_secrets.custom_secrets.keys()):
                print(f"      - {name}")
        else:
            print("❌ SaasSecretsStore.load() returned no secrets!")


async def main():
    if len(sys.argv) < 2:
        print("Usage: python3 compare_secrets.py <org_id>")
        print("\nExample:")
        print("  python3 compare_secrets.py 0d2fe560-b400-4833-aec0-1fd5433be1c5")
        sys.exit(1)
    
    try:
        org_id = UUID(sys.argv[1])
    except ValueError:
        print(f"❌ Invalid UUID: {sys.argv[1]}")
        sys.exit(1)
    
    await compare_secrets(org_id)


if __name__ == "__main__":
    asyncio.run(main())
