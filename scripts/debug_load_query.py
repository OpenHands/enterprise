#!/usr/bin/env python3
"""Debug the exact SQL query that SaasSecretsStore.load() executes.

This will show us the actual query and all rows it returns.
"""

import asyncio
import sys
from uuid import UUID

from sqlalchemy import select
from storage.database import a_session_maker
from storage.stored_custom_secrets import StoredCustomSecrets


async def debug_load_query(org_id: UUID, user_id: str):
    """Debug the exact query that SaasSecretsStore.load() runs."""
    
    print(f"🔍 Debugging SaasSecretsStore.load() query")
    print(f"   User ID: {user_id}")
    print(f"   Org ID:  {org_id}\n")
    
    async with a_session_maker() as session:
        # This is the EXACT query from SaasSecretsStore.load()
        print("1. Fetching personal secrets (is_org_shared=False)...")
        personal_query = select(StoredCustomSecrets).filter(
            StoredCustomSecrets.keycloak_user_id == user_id,
            StoredCustomSecrets.is_org_shared.is_(False),
        )
        if org_id is not None:
            personal_query = personal_query.filter(
                StoredCustomSecrets.org_id == org_id
            )
        
        personal_result = await session.execute(personal_query)
        personal_secrets = personal_result.scalars().all()
        
        print(f"   Found {len(personal_secrets)} personal secret(s):")
        for s in personal_secrets:
            print(f"     - {s.secret_name}")
        print()
        
        # This is the org-shared query
        print("2. Fetching org-shared secrets (is_org_shared=True)...")
        shared_query = select(StoredCustomSecrets).filter(
            StoredCustomSecrets.org_id == org_id,
            StoredCustomSecrets.is_org_shared.is_(True),
        )
        
        # Print the compiled SQL
        from sqlalchemy.dialects import postgresql
        compiled = shared_query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        print(f"   SQL: {compiled}\n")
        
        shared_result = await session.execute(shared_query)
        shared_secrets = shared_result.scalars().all()
        
        print(f"   Found {len(shared_secrets)} org-shared secret(s):")
        for s in shared_secrets:
            print(f"     - {s.secret_name} (ID: {s.id})")
        print()
        
        # Now check specifically for the missing ones
        print("3. Checking for specific secrets in the query result...")
        target_names = ['PROD_LITELLM_MASTER_KEY', 'SAURYA_TEST_KEY', 'E2E_BOT_GITHUB_TOKEN']
        
        for name in target_names:
            found = any(s.secret_name == name for s in shared_secrets)
            status = "✅ FOUND" if found else "❌ MISSING"
            print(f"   {status}: {name}")
        print()
        
        # Now query those specific records directly
        print("4. Querying those 3 records directly from the database...")
        direct_query = select(StoredCustomSecrets).filter(
            StoredCustomSecrets.org_id == org_id,
            StoredCustomSecrets.secret_name.in_(target_names)
        )
        
        direct_result = await session.execute(direct_query)
        direct_secrets = direct_result.scalars().all()
        
        print(f"   Direct query found {len(direct_secrets)} record(s):")
        for s in direct_secrets:
            print(f"     - {s.secret_name}")
            print(f"       ID: {s.id}")
            print(f"       org_id: {s.org_id}")
            print(f"       is_org_shared: {s.is_org_shared}")
            print(f"       keycloak_user_id: {s.keycloak_user_id}")
            
            # Check if this would match the shared_query
            matches = (s.org_id == org_id and s.is_org_shared is True)
            print(f"       Would match shared_query: {matches}")
            print()
        
        # Compare
        print("=" * 80)
        print("DIAGNOSIS:")
        
        direct_names = {s.secret_name for s in direct_secrets}
        shared_names = {s.secret_name for s in shared_secrets}
        
        missing = direct_names - shared_names
        
        if missing:
            print(f"❌ These secrets exist in DB but NOT in shared_query result:")
            for name in missing:
                print(f"   - {name}")
                record = next(s for s in direct_secrets if s.secret_name == name)
                print(f"     org_id matches: {record.org_id == org_id}")
                print(f"     is_org_shared: {record.is_org_shared}")
                print(f"     is_org_shared is True: {record.is_org_shared is True}")
                print(f"     is_org_shared.is_(True) would match: ???")
        else:
            print("✅ All secrets found in shared_query")
        print("=" * 80)


async def main():
    if len(sys.argv) < 3:
        print("Usage: python3 debug_load_query.py <org_id> <user_id>")
        print("\nExample:")
        print("  python3 debug_load_query.py 0d2fe560-b400-4833-aec0-1fd5433be1c5 user123")
        sys.exit(1)
    
    try:
        org_id = UUID(sys.argv[1])
    except ValueError:
        print(f"❌ Invalid UUID: {sys.argv[1]}")
        sys.exit(1)
    
    user_id = sys.argv[2]
    
    await debug_load_query(org_id, user_id)


if __name__ == "__main__":
    asyncio.run(main())
