# Free-model backfill runbook (OHE-3155)

## Problem

PR #143 lets free-tier (no-credit) teams run $0-cost models by materializing
`FREE_LLM_MODELS` into each team's LiteLLM `models` allowlist. The managed
default model was renamed to `deepseek-v4-flash`, but the free-tier default
list wasn't updated, so existing free teams 403 on the new default.

## Fix

Two parts:

1. **Code (this PR)** — add `deepseek-v4-flash` to
   `_DEFAULT_FREE_LLM_MODELS` in `storage/lite_llm_manager.py`. This only
   affects *newly provisioned / re-provisioned* teams.
2. **Data (SQL, out of band)** — backfill *existing* teams in the LiteLLM
   proxy database. The script is `.pr/backfill_free_models.sql`.

## Backfill procedure

### 0. Preflight

Run the verification query at the bottom of the script first on staging to
size the affected rows. Expect two shapes:

| shape | `max_budget` | `models` | meaning |
|---|---|---|---|
| A (post-#143) | `NULL` | non-empty subset of free set | already restricted, missing a model |
| B (pre-#143) | `<= 0` | empty (`'{}'`) | budget-gated, all models |

### 1. Stage

```bash
psql "$LITELLM_DATABASE_URL" -v ON_ERROR_STOP=1 -f .pr/backfill_free_models.sql
```

`LITELLM_DATABASE_URL` points at the **LiteLLM proxy** Postgres, not the
enterprise SaaS database.

### 2. Verify on staging

Run the verification query (uncommented) after the transaction. Confirm:

- `case_a_still_missing` is `NULL`/absent (0 rows).
- `case_b_still_budget_gated` is `NULL`/absent (0 rows).
- A spot-check team can now run `deepseek-v4-flash`.

### 3. Cache invalidation (required)

LiteLLM caches team objects in-memory + Redis with a **60s default TTL**
(`general_settings.user_api_key_cache_ttl`). A direct DB write is invisible
to auth until that cache expires or is invalidated.

- If the deployment TTL is left at 60s, a one-time wait (~1 minute) suffices.
- If a longer TTL is configured, flush the team cache. The cache key is
  `team_id:<team_id>` for every updated team. Safer alternative: restart the
  proxy pods (or run the team-update via the LiteLLM admin API, which
  invalidates the cache itself — see "Why not REST" below).

### 4. Repeat on production

Same steps, ideally during a maintenance window given the cache-expiry wait.

## Why not the LiteLLM REST API

The `/team/update` admin endpoint would invalidate the cache automatically,
but it is one POST per team (~47,794 teams). At LiteLLM's per-call latency
that's hours-to-days of serial REST traffic and risks rate limiting. A single
`UPDATE` over the proxy's own Postgres is milliseconds and trivially
idempotent — the only cost is the cache-expiry note above.

## Repeatability

The script is additive and idempotent:

- It unions into `models` (never removes), so a team that already has some or
  all free models only gains the missing ones.
- Stragglers (teams created after the PR ships but before the backfill, or
  teams that only later flip to free tier) are picked up on any subsequent
  run.
- `kimi-k3` is kept in the set even though the default model moved off it, so
  older teams that still list it aren't harmed; the union is a superset of
  everything that has ever been free.
