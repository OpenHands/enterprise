# Verified Models Runbook

Use the verified-models admin API to manage the model metadata that Enterprise-backed Canvas deployments show in model search, LLM settings, onboarding, and profile pickers. This applies to OpenHands SaaS and to third-party Enterprise deployments that run the Enterprise backend with the verified-model database tables.

The API stores one row per `(provider, model_name)`. For OpenHands-hosted models, use `provider: "openhands"` and store the concrete model name without an `openhands/` prefix, for example `deepseek-v4-flash`.

## Runtime behavior

Canvas reads model metadata from `/api/v1/config/models/search`. In an Enterprise deployment, `SaaSLLMModelService` builds that response from enabled verified-model rows and attaches DB-backed flags to concrete model IDs such as `openhands/deepseek-v4-flash`.

| DB field | Effect |
| --- | --- |
| `is_enabled` | Controls whether the model is exposed to users. Admin list calls can still see disabled rows. |
| `is_verified` | Controls the verified mark. An enabled DB row can add a verified model or remove the verified mark from a static-list model. |
| `is_free` | Controls the free-model UI label and keeps LiteLLM free-model allowlists in sync for enabled OpenHands rows. |
| `is_default` | Marks the provider default. For `provider='openhands'`, this drives the virtual `Available Profiles -> Default` profile and new OpenHands model preselection. |

`Default` is a live pointer, not a persisted profile JSON rewrite. It resolves to the enabled row where `provider='openhands' AND is_default=true AND is_enabled=true`. Do not create or use an `openhands/Default` model alias. Resolved profiles should show and store real model names.

If an admin disables the current OpenHands default row, the backend ignores it immediately. Mark another enabled OpenHands row as `is_default: true` to restore the virtual `Default` profile.

## Authentication

The admin endpoints require an admin bearer token for the target deployment. Set these variables before running the examples:

```bash
export OPENHANDS_BASE_URL="https://app.all-hands.dev"
export OPENHANDS_ADMIN_TOKEN="your-admin-api-token"
```

For OpenHands staging, use:

```bash
export OPENHANDS_BASE_URL="https://staging.all-hands.dev"
export OPENHANDS_ADMIN_TOKEN="$SAAS_STAGING_MODEL_LIST_API_KEY"
```

## List models

Admin list calls include disabled rows so operators can repair stale or hidden state.

```bash
curl -sS "$OPENHANDS_BASE_URL/api/admin/verified-models?provider=openhands&limit=100" \
  -H "Authorization: Bearer $OPENHANDS_ADMIN_TOKEN" \
  | jq '.items[] | {name: .model_name, enabled: .is_enabled, verified: .is_verified, free: .is_free, default: .is_default}'
```

The user-facing search API should only expose enabled rows and should only default-mark enabled rows. It uses the same auth path as Canvas, so this example uses a logged-in browser session cookie:

```bash
export OPENHANDS_SESSION_COOKIE='...' # copy from the logged-in Canvas browser session

curl -sS "$OPENHANDS_BASE_URL/api/v1/config/models/search?provider__eq=openhands" \
  -H "Cookie: $OPENHANDS_SESSION_COOKIE" \
  | jq '.items[] | {id: (.provider + "/" + .name), verified, free, default, hidden, canonical}'
```

## Add a model

`is_enabled` and `is_verified` default to `true`. `is_free` and `is_default` default to `false`. Pass all flags explicitly when the model should be free, default, or intentionally unverified.

```bash
curl -X POST "$OPENHANDS_BASE_URL/api/admin/verified-models" \
  -H "Authorization: Bearer $OPENHANDS_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "openhands",
    "model_name": "glm-5.2",
    "is_enabled": true,
    "is_verified": true,
    "is_free": false,
    "is_default": false
  }'
```

## Enable or disable a model

Disabling a row removes it from user-facing model search. If the disabled row was the OpenHands default, `Default` no longer resolves to that model.

```bash
curl -X PUT "$OPENHANDS_BASE_URL/api/admin/verified-models/openhands/glm-5.2" \
  -H "Authorization: Bearer $OPENHANDS_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_enabled": false}'
```

Re-enable it with:

```bash
curl -X PUT "$OPENHANDS_BASE_URL/api/admin/verified-models/openhands/glm-5.2" \
  -H "Authorization: Bearer $OPENHANDS_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_enabled": true}'
```

## Set verified status

Use `is_verified` to control whether Canvas marks the model as verified. This is separate from `is_enabled`; an enabled model can remain visible but intentionally unverified.

```bash
curl -X PUT "$OPENHANDS_BASE_URL/api/admin/verified-models/openhands/glm-5.2" \
  -H "Authorization: Bearer $OPENHANDS_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_verified": true}'
```

Remove the verified mark while keeping the model enabled:

```bash
curl -X PUT "$OPENHANDS_BASE_URL/api/admin/verified-models/openhands/glm-5.2" \
  -H "Authorization: Bearer $OPENHANDS_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_verified": false}'
```

## Set the free badge

Set `is_free: true` to show the free label in Canvas for enabled model IDs returned by the backend. For OpenHands rows, changing `is_free` or `is_enabled` also syncs LiteLLM free-model allowlists.

```bash
curl -X PUT "$OPENHANDS_BASE_URL/api/admin/verified-models/openhands/glm-5.2" \
  -H "Authorization: Bearer $OPENHANDS_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_free": true}'
```

Remove the free label:

```bash
curl -X PUT "$OPENHANDS_BASE_URL/api/admin/verified-models/openhands/glm-5.2" \
  -H "Authorization: Bearer $OPENHANDS_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_free": false}'
```

## Set the OpenHands default model

Set `is_default: true` on the concrete enabled OpenHands row that should back `Available Profiles -> Default`. The service clears any previous default for the same provider before saving the new default, and the database also enforces a single default row per provider.

```bash
curl -X PUT "$OPENHANDS_BASE_URL/api/admin/verified-models/openhands/glm-5.2" \
  -H "Authorization: Bearer $OPENHANDS_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_enabled": true, "is_default": true}'
```

Verify the result in the admin list:

```bash
curl -sS "$OPENHANDS_BASE_URL/api/admin/verified-models?provider=openhands&limit=100" \
  -H "Authorization: Bearer $OPENHANDS_ADMIN_TOKEN" \
  | jq '.items[] | select(.is_default) | {name: .model_name, enabled: .is_enabled, default: .is_default}'
```

Then reload Canvas LLM settings and confirm `Available Profiles -> Default` resolves to the concrete model, for example `openhands/glm-5.2`.

## Remove a model row

Deleting a row removes the DB override entirely. Do this only when you no longer need the row for admin repair or audit visibility.

```bash
curl -X DELETE "$OPENHANDS_BASE_URL/api/admin/verified-models/openhands/glm-5.2" \
  -H "Authorization: Bearer $OPENHANDS_ADMIN_TOKEN"
```

## Smoke test a deployment

Run these checks after changing model metadata or deploying a new Enterprise backend/Canvas frontend pair.

```bash
# Confirm the backend is serving.
curl -i "$OPENHANDS_BASE_URL/health"

# Confirm the API schemas expose the verified-model flags.
curl -sS "$OPENHANDS_BASE_URL/openapi.json" | python -c 'import json,sys; schemas=json.load(sys.stdin).get("components",{}).get("schemas",{}); print({name: sorted({"is_enabled","is_verified","is_free","is_default"} & set(schemas.get(name,{}).get("properties",{}))) for name in ("VerifiedModel","VerifiedModelCreate","VerifiedModelUpdate")})'
```

Manual UI smoke test:

1. Open Canvas LLM settings.
2. Confirm `Available Profiles -> Default` resolves to the current enabled OpenHands DB default row.
3. Mark another enabled OpenHands row as `is_default: true`.
4. Reload LLM settings and confirm `Default` resolves to the new concrete model name.
5. Disable that default row and reload. Confirm user-facing model search no longer exposes/default-marks that disabled row.
6. Toggle `is_free` on an enabled row and reload. Confirm the free label follows backend metadata.

## Troubleshooting

If `Default` disappears or does not resolve, list OpenHands rows and check that exactly one enabled row has `is_default: true`:

```bash
curl -sS "$OPENHANDS_BASE_URL/api/admin/verified-models?provider=openhands&limit=100" \
  -H "Authorization: Bearer $OPENHANDS_ADMIN_TOKEN" \
  | jq '.items[] | select(.is_default or .is_enabled == false) | {name: .model_name, enabled: .is_enabled, default: .is_default}'
```

If the admin list shows a disabled default, either re-enable it or set another enabled OpenHands row as default. The backend will not resolve a disabled row through the virtual `Default` profile.
