#!/usr/bin/env python3
"""Check if missing secrets have NULL descriptions."""

import asyncio
from uuid import UUID
import sys

from sqlalchemy import select
from storage.database import a_session_maker
from storage.stored_custom_secrets import StoredCustomSecrets


async def check_descriptions(org_id: UUID):
    """Check descriptions for the target secrets."""
    
    target_names = ['PROD_LITELLM_MASTER_KEY', 'SAURYA_TEST_KEY', 'E2E_BOT_GITHUB_TOKEN']
    
    async with a_session_maker() as session:
        query = select(StoredCustomSecrets).filter(
            StoredCustomSecrets.org_id == org_id,
            StoredCustomSecrets.secret_name.in_(target_names)
        )
        result = await session.execute(query)
        secrets = result.scalars().all()
        
        print("Secret descriptions:")
        print("-" * 80)
        for secret in secrets:
            desc_status = "NULL" if secret.description is None else f"'{secret.description[:50]}...'"
            print(f"{secret.secret_name}: {desc_status}")
        print("-" * 80)
        
        null_descriptions = [s.secret_name for s in secrets if s.description is None]
        
        if null_descriptions:
            print(f"\n❌ Secrets with NULL description (will be dropped by Pydantic validation):")
            for name in null_descriptions:
                print(f"   - {name}")
            print("\n   → BUG CONFIRMED: CustomSecret model expects str, not None")
        else:
            print("\n✅ All secrets have non-NULL descriptions")


asyncio.run(check_descriptions(UUID(sys.argv[1])))
