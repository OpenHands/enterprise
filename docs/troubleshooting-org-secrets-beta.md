# Troubleshooting: Org-Scoped Secrets Not Appearing in Beta Conversations

## Issue Summary

**Reporter:** Saurya  
**Date:** 2026-09-29  
**Environment:** `app.beta.staging.all-hands-testing.dev`

### Symptoms

1. ✅ Org secrets ARE created successfully via `POST /api/organizations/{org_id}/secrets`
2. ✅ They appear in `GET /api/v1/secrets/search` with `scope: "organization"`
3. ❌ They do NOT appear as environment variables in conversation runtimes
4. ⚠️  Exception: `TIMS_SECRET_VALUE` DOES appear in conversations

### Affected Secrets

Working:
- `TIMS_SECRET_VALUE` ✓

Missing from conversations:
- `PROD_LITELLM_MASTER_KEY` ✗
- `SAURYA_TEST_KEY` ✗
- `E2E_BOT_GITHUB_TOKEN` ✗
- `REPORTPORTAL_API_KEY_2` ✗
- `SLACK_BOT_TOKEN_2` ✗
- `EVAL_LITELLM_MASTER_KEY` ✗
- `STAGING_LITELLM_MASTER_KEY` ✗

## Root Cause Analysis

### Code Status

The fix IS in the codebase:

- **PR #391** (2026-09-14): Org-scoped secrets feature added
- **PR #515** (2026-09-24): Fix for duplicate secrets
- **Release 1.66.0** (2026-09-28): Contains both fixes
- **Chart openhands/0.72.0+**: Uses enterprise-server 1.66.0

The `SaasSecretsStore.load()` method correctly fetches BOTH:
```python
# Fetch personal secrets (is_org_shared=False)
personal_query = select(StoredCustomSecrets).filter(
    StoredCustomSecrets.keycloak_user_id == self.user_id,
    StoredCustomSecrets.is_org_shared.is_(False),
)

# Fetch org-shared secrets (is_org_shared=True)  
shared_query = select(StoredCustomSecrets).filter(
    StoredCustomSecrets.org_id == org_id,
    StoredCustomSecrets.is_org_shared.is_(True),
)
```

### Deployment Status - NEEDS VERIFICATION

**Current beta runtime:**
- Agent-server: `v1.49.6` (SHA: `fcc102a697874d54a357e36004e02c95040dbdc0`)
- Enterprise-server: **UNKNOWN** ⚠️

**Required versions:**
- Enterprise-server: `>= 1.66.0`
- Database migration: `#163` must be applied

## Verification Steps

### 1. Check Enterprise-Server Version on Beta

```bash
# SSH to beta backend pod
kubectl exec -it <backend-pod> -n <namespace> -- env | grep VERSION

# Or check logs for startup version info
kubectl logs <backend-pod> -n <namespace> | grep version
```

**Expected:** `>= 1.66.0`

### 2. Verify Database Migration #163

```bash
# Connect to beta database
psql <connection-string>

# Check if is_org_shared column exists
\d custom_secrets

# Expected output should include:
# is_org_shared | boolean | not null | default false
```

### 3. Check Actual Secret Records

```sql
-- View all org secrets
SELECT 
    secret_name,
    keycloak_user_id,
    org_id,
    is_org_shared,
    created_at
FROM custom_secrets
WHERE org_id = '0d2fe560-b400-4833-aec0-1fd5433be1c5'  -- Beta org ID
  AND is_org_shared IS NOT NULL
ORDER BY created_at DESC;
```

**Expected:** All org secrets should have `is_org_shared = TRUE`

### 4. Test Secret Loading Directly

```python
# Run in beta backend pod
from storage.saas_secrets_store import SaasSecretsStore
from uuid import UUID

# Use Saurya's user ID and org ID
store = await SaasSecretsStore.get_instance(
    user_id="<saurya-keycloak-id>",
    effective_org_id=UUID("0d2fe560-b400-4833-aec0-1fd5433be1c5"),
)
secrets = await store.load()
print("Loaded secrets:", list(secrets.custom_secrets.keys()))
```

**Expected:** Should include all 8 org secrets + personal secrets

## Possible Causes

### Hypothesis A: Old Backend Version Deployed
- Beta is running enterprise-server `< 1.66.0`
- **Fix:** Deploy latest chart (openhands/0.73.0+)
- **Likelihood:** HIGH ⚠️

### Hypothesis B: Migration Not Run
- Database migration #163 wasn't applied to beta
- `is_org_shared` column doesn't exist or has wrong default
- **Fix:** Run `alembic upgrade head` on beta DB
- **Likelihood:** MEDIUM

### Hypothesis C: Partial Deployment
- API endpoints are running new code
- Conversation startup path is running old code
- Different pods/services at different versions
- **Fix:** Force redeploy all services
- **Likelihood:** LOW

### Hypothesis D: TIMS_SECRET_VALUE is Special
- It was created before migration #163
- It has a different schema or was migrated manually
- Other secrets were created after migration but incorrectly
- **Fix:** Check `TIMS_SECRET_VALUE` record in DB
- **Likelihood:** LOW

## Resolution Checklist

- [ ] Verify beta is running enterprise-server >= 1.66.0
- [ ] Confirm migration #163 is applied to beta DB
- [ ] Check all org secret records have `is_org_shared = TRUE`
- [ ] Run integration tests (tests/integration/test_org_secrets_in_conversations.py)
- [ ] Start a fresh conversation and verify ALL org secrets appear
- [ ] Document which version was actually deployed vs expected

## Related Files

- `storage/saas_secrets_store.py` - Secret loading logic
- `storage/org_secrets_store.py` - Org secret CRUD
- `server/routes/org_secrets.py` - API endpoints
- `migrations/versions/163_add_is_org_shared_to_custom_secrets.py` - DB schema
- `tests/unit/test_saas_secrets_store.py` - Unit tests

## Contact

If this doesn't resolve the issue, escalate to:
- @tofarr (authored PR #515)
- Platform team (deployment verification)
