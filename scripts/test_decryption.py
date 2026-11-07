#!/usr/bin/env python3
"""Test if decryption is failing for specific secrets.

This will attempt to decrypt each secret individually to see if
decryption failures are causing secrets to disappear.
"""

import asyncio
import sys
from uuid import UUID

from sqlalchemy import select
from storage.database import a_session_maker
from storage.stored_custom_secrets import StoredCustomSecrets
from storage.encrypt_utils import get_jwt_service


async def test_decryption(org_id: UUID):
    """Test decryption for each org-shared secret."""
    
    print(f"🔍 Testing decryption for org {org_id}\n")
    
    jwt_svc = get_jwt_service()
    
    target_names = ['PROD_LITELLM_MASTER_KEY', 'SAURYA_TEST_KEY', 'E2E_BOT_GITHUB_TOKEN']
    
    async with a_session_maker() as session:
        query = select(StoredCustomSecrets).filter(
            StoredCustomSecrets.org_id == org_id,
            StoredCustomSecrets.secret_name.in_(target_names)
        )
        result = await session.execute(query)
        secrets = result.scalars().all()
        
        print(f"Testing decryption for {len(secrets)} secret(s):\n")
        print("-" * 80)
        
        for secret in secrets:
            print(f"\n{secret.secret_name}:")
            print(f"  ID: {secret.id}")
            print(f"  is_org_shared: {secret.is_org_shared}")
            
            # Test secret value decryption
            try:
                decrypted_value = jwt_svc.decrypt_value(secret.secret_value)
                value_len = len(decrypted_value) if decrypted_value else 0
                print(f"  ✅ Secret value: decrypted successfully ({value_len} chars)")
            except Exception as e:
                print(f"  ❌ Secret value: DECRYPTION FAILED!")
                print(f"     Error: {type(e).__name__}: {e}")
            
            # Test description decryption
            if secret.description:
                try:
                    decrypted_desc = jwt_svc.decrypt_value(secret.description)
                    print(f"  ✅ Description: decrypted successfully")
                except Exception as e:
                    print(f"  ❌ Description: DECRYPTION FAILED!")
                    print(f"     Error: {type(e).__name__}: {e}")
            else:
                print(f"  ℹ️  Description: NULL (no decryption needed)")
        
        print("\n" + "=" * 80)
        print("DIAGNOSIS:")
        
        # Count failures
        value_failures = []
        desc_failures = []
        
        for secret in secrets:
            try:
                jwt_svc.decrypt_value(secret.secret_value)
            except Exception:
                value_failures.append(secret.secret_name)
            
            if secret.description:
                try:
                    jwt_svc.decrypt_value(secret.description)
                except Exception:
                    desc_failures.append(secret.secret_name)
        
        if value_failures:
            print(f"❌ Secrets with failed value decryption:")
            for name in value_failures:
                print(f"   - {name}")
            print("\n   → These secrets cannot be loaded due to decryption failures")
            print("   → Possible causes:")
            print("     1. Secrets were encrypted with a different JWT key")
            print("     2. Database corruption")
            print("     3. Migration issue")
        elif desc_failures:
            print(f"⚠️  Secrets with failed description decryption:")
            for name in desc_failures:
                print(f"   - {name}")
            print("\n   → Description decryption failed, but values might be OK")
        else:
            print("✅ All secrets decrypt successfully")
            print("   → Decryption is NOT the issue")
            print("   → Problem must be elsewhere in the code path")
        
        print("=" * 80)


async def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_decryption.py <org_id>")
        print("\nExample:")
        print("  python3 test_decryption.py 0d2fe560-b400-4833-aec0-1fd5433be1c5")
        sys.exit(1)
    
    try:
        org_id = UUID(sys.argv[1])
    except ValueError:
        print(f"❌ Invalid UUID: {sys.argv[1]}")
        sys.exit(1)
    
    await test_decryption(org_id)


if __name__ == "__main__":
    asyncio.run(main())
