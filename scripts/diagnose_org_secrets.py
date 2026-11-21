#!/usr/bin/env python3
"""Diagnostic script to check org-scoped secrets configuration.

Run this from inside the enterprise-server pod to check if secrets have the
correct is_org_shared flag. This script uses the application's own database
connection, so it works on Replicated instances where direct DB access is locked down.

Usage:
    kubectl exec -it <enterprise-server-pod> -n openhands -- python3 /tmp/diagnose_org_secrets.py <org_id>

Example:
    kubectl exec -it enterprise-server-0 -n openhands -- python3 /tmp/diagnose_org_secrets.py 0d2fe560-b400-4833-aec0-1fd5433be1c5
"""

import asyncio
import sys
from uuid import UUID

# Import the application's storage classes
from sqlalchemy import select, text
from storage.database import a_session_maker
from storage.stored_custom_secrets import StoredCustomSecrets


async def diagnose_org_secrets(org_id: UUID):
    """Check the is_org_shared flag for all secrets in an org."""
    
    print(f"🔍 Diagnosing org secrets for org_id: {org_id}\n")
    
    async with a_session_maker() as session:
        # Check if the is_org_shared column exists
        print("1. Checking if is_org_shared column exists...")
        try:
            result = await session.execute(
                text("""
                    SELECT column_name, data_type, column_default
                    FROM information_schema.columns
                    WHERE table_name = 'custom_secrets'
                    AND column_name = 'is_org_shared'
                """)
            )
            column_info = result.fetchone()
            if column_info:
                print(f"   ✅ Column exists: {column_info[0]} ({column_info[1]}, default={column_info[2]})")
            else:
                print("   ❌ Column does NOT exist! Migration #163 not applied.")
                return
        except Exception as e:
            print(f"   ❌ Error checking column: {e}")
            return
        
        print()
        
        # Get all secrets for this org
        print("2. Fetching all secrets for this org...")
        query = select(StoredCustomSecrets).filter(
            StoredCustomSecrets.org_id == org_id
        ).order_by(StoredCustomSecrets.created_at.desc())
        
        result = await session.execute(query)
        secrets = result.scalars().all()
        
        if not secrets:
            print(f"   ⚠️  No secrets found for org {org_id}")
            return
        
        print(f"   Found {len(secrets)} secret(s)\n")
        
        # Analyze each secret
        print("3. Analyzing secrets:")
        print("-" * 80)
        print(f"{'Secret Name':<35} {'is_org_shared':<15} {'Status':<20}")
        print("-" * 80)
        
        org_shared_count = 0
        personal_count = 0
        
        for secret in secrets:
            status = "✅ ORG-SHARED" if secret.is_org_shared else "❌ PERSONAL (WRONG!)"
            if secret.is_org_shared:
                org_shared_count += 1
            else:
                personal_count += 1
            print(f"{secret.secret_name:<35} {str(secret.is_org_shared):<15} {status:<20}")
        
        print("-" * 80)
        print(f"\nSummary:")
        print(f"  - Org-shared (correct): {org_shared_count}")
        print(f"  - Personal (incorrect): {personal_count}")
        
        print()
        
        # Check what SaasSecretsStore.load() would return
        print("4. Simulating SaasSecretsStore.load() query...")
        
        # Query for org-shared secrets (what the app actually loads)
        org_query = select(StoredCustomSecrets).filter(
            StoredCustomSecrets.org_id == org_id,
            StoredCustomSecrets.is_org_shared.is_(True)
        )
        org_result = await session.execute(org_query)
        org_secrets = org_result.scalars().all()
        
        print(f"   SaasSecretsStore.load() would return {len(org_secrets)} org secret(s):")
        for secret in org_secrets:
            print(f"     ✅ {secret.secret_name}")
        
        if personal_count > 0:
            print(f"\n   ⚠️  {personal_count} secret(s) have is_org_shared=FALSE and will NOT appear in conversations!")
            print("   These secrets were likely created before migration #163 or through the wrong API.")
        
        print()
        
        # Provide fix instructions
        if personal_count > 0:
            print("5. FIX: Update secrets to be org-shared")
            print("-" * 80)
            print("Run this SQL to fix the broken secrets:\n")
            
            personal_names = [s.secret_name for s in secrets if not s.is_org_shared]
            names_sql = ", ".join(f"'{name}'" for name in personal_names)
            
            print(f"""UPDATE custom_secrets
SET is_org_shared = TRUE
WHERE org_id = '{org_id}'
  AND secret_name IN ({names_sql});
""")
            print("\nOr delete and recreate them via POST /api/organizations/{org_id}/secrets")
            print("-" * 80)


async def main():
    if len(sys.argv) < 2:
        print("Usage: python3 diagnose_org_secrets.py <org_id>")
        print("\nExample:")
        print("  python3 diagnose_org_secrets.py 0d2fe560-b400-4833-aec0-1fd5433be1c5")
        sys.exit(1)
    
    try:
        org_id = UUID(sys.argv[1])
    except ValueError:
        print(f"❌ Invalid UUID: {sys.argv[1]}")
        sys.exit(1)
    
    await diagnose_org_secrets(org_id)


if __name__ == "__main__":
    asyncio.run(main())
