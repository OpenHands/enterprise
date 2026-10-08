#!/usr/bin/env python3
"""Test what secrets SaasSecretsStore.load() actually returns for a user.

This simulates the exact code path that runs when a conversation starts
to see what secrets would be injected into the runtime.

Usage:
    kubectl exec -it <openhands-pod> -n openhands -- \
      python3 /tmp/test_secrets_loading.py <user_keycloak_id> <org_id>
"""

import asyncio
import sys
from uuid import UUID

from storage.saas_secrets_store import SaasSecretsStore


async def test_secrets_loading(user_id: str, org_id: UUID):
    """Test what secrets load() returns for this user/org combination."""
    
    print(f"🧪 Testing SaasSecretsStore.load() for:")
    print(f"   User ID: {user_id}")
    print(f"   Org ID:  {org_id}\n")
    
    # Create the store (this is what the app does)
    store = await SaasSecretsStore.get_instance(
        user_id=user_id,
        effective_org_id=org_id,
    )
    
    print("1. Calling SaasSecretsStore.load()...")
    secrets = await store.load()
    
    if secrets is None or not secrets.custom_secrets:
        print("   ❌ No secrets returned!\n")
        return
    
    print(f"   ✅ Returned {len(secrets.custom_secrets)} secret(s)\n")
    
    # List all secrets
    print("2. Secrets that would be injected into the runtime:")
    print("-" * 80)
    
    secret_names = sorted(secrets.custom_secrets.keys())
    
    for name in secret_names:
        secret_info = secrets.custom_secrets[name]
        # Don't print the actual value, just check if it exists
        has_value = secret_info.get('secret') is not None
        description = secret_info.get('description', '')
        
        status = "✅" if has_value else "❌"
        desc_str = f" ({description[:50]})" if description else ""
        print(f"   {status} {name}{desc_str}")
    
    print("-" * 80)
    print(f"\nTotal: {len(secret_names)} secrets\n")
    
    # Check for specific secrets
    print("3. Checking for specific secrets:")
    expected = [
        'PROD_LITELLM_MASTER_KEY',
        'SAURYA_TEST_KEY',
        'TIMS_SECRET_VALUE',
        'E2E_BOT_GITHUB_TOKEN',
    ]
    
    for name in expected:
        if name in secrets.custom_secrets:
            print(f"   ✅ {name}")
        else:
            print(f"   ❌ {name} - MISSING")
    
    print("\n" + "=" * 80)
    print("RESULT:")
    if all(name in secrets.custom_secrets for name in expected):
        print("✅ All expected secrets are present in load() result")
        print("   → Issue is in runtime injection, not in SaasSecretsStore")
    else:
        missing = [n for n in expected if n not in secrets.custom_secrets]
        print(f"❌ Missing secrets: {', '.join(missing)}")
        print("   → Issue is in SaasSecretsStore.load() or database query")
    print("=" * 80)


async def main():
    if len(sys.argv) < 3:
        print("Usage: python3 test_secrets_loading.py <user_id> <org_id>")
        print("\nExample:")
        print("  python3 test_secrets_loading.py abc123 0d2fe560-b400-4833-aec0-1fd5433be1c5")
        sys.exit(1)
    
    user_id = sys.argv[1]
    
    try:
        org_id = UUID(sys.argv[2])
    except ValueError:
        print(f"❌ Invalid UUID: {sys.argv[2]}")
        sys.exit(1)
    
    await test_secrets_loading(user_id, org_id)


if __name__ == "__main__":
    asyncio.run(main())
