-- =============================================================================
-- Backfill: add the current $0-cost model set to free-tier LiteLLM teams.
-- (OHE-3155)
-- =============================================================================
--
-- WHY
--   PR #143 materializes FREE_LLM_MODELS into each free tier (no-credit) team's
--   LiteLLM ``models`` allowlist at provisioning time. The managed default model
--   was subsequently renamed from ``kimi-k3`` to ``deepseek-v4-flash`` without
--   the free-tier default list being updated, so existing free teams' stored
--   allowlists are missing the new default and LiteLLM returns
--   ``team_model_access_denied`` (HTTP 403).
--
--   This script fixes existing teams already stored in LiteLLM. Newly
--   provisioned / re-provisioned teams are fixed by the code change in this PR
--   (`deepseek-v4-flash` added to `_DEFAULT_FREE_LLM_MODELS`).
--
-- TARGET DATABASE
--   This runs against the **LiteLLM proxy** Postgres, NOT the enterprise SaaS
--   database. These are different databases; point psql at the proxy's URL.
--
-- PROPERTIES (safe to re-run)
--   * additive  -- unions the free models into ``models``, never removing
--                  anything already present.
--   * idempotent -- a team already carrying the full free set is a no-op.
--   * scoped    -- only teams that are unambiguously "free tier" are touched.
--
-- TWO FREE-TIER TEAM SHAPES EXIST IN THE WILD
--   (A) post-#143: ``max_budget IS NULL`` and ``models`` is a non-empty subset
--       of the free set (budget enforcement cleared, models restricted).
--   (B) pre-#143:  ``max_budget <= 0`` and ``models`` empty (budget-gated at 0,
--       all models allowed) -- the state #143 migrates on next provisioning.
--
--   ``models = '{}'`` (empty) is LiteLLM's "all models" sentinel, so we only
--   add to it when the team is provably free (case B), never for a generic
--   NULL-budget unlimited team.
--
-- CACHE INVALIDATION (required)
--   LiteLLM caches team objects in-memory + Redis with a 60s default TTL
--   (``general_settings.user_api_key_cache_ttl``). A direct DB write is
--   invisible to auth until that cache expires or is flushed.
--
--     * If the deployment TTL is left at 60s, a one-time wait (~1 minute)
--       suffices.
--     * If a longer TTL is configured, flush the team cache (cache key
--       ``team_id:<team_id>`` per updated team), restart the proxy pods, or run
--       team updates via the LiteLLM admin API (which invalidates the cache
--       itself -- but see "Why not REST" below).
--
-- WHY NOT THE LITELLM REST API
--   ``/team/update`` would invalidate the cache automatically, but it is one
--   POST per team (~47,794 teams): hours-to-days of serial REST traffic with
--   rate-limit risk. A single ``UPDATE`` over the proxy's own Postgres is
--   milliseconds and idempotent; the only cost is the cache-invalidation note
--   above.
--
-- PROCEDURE
--   1. Preflight (read-only): run the "PREFLIGHT COUNT" query below on staging.
--      It returns the affected row counts without changing anything.
--   2. Run this script on staging:
--        psql "$LITELLM_DATABASE_URL" -v ON_ERROR_STOP=1 \
--          -f scripts/backfill_litellm_free_models.sql
--   3. Verify: re-run "PREFLIGHT COUNT". Both counts should now be 0. Spot-check
--      a free team can run ``deepseek-v4-flash``.
--   4. Handle cache invalidation (see above).
--   5. Repeat on production, ideally during a maintenance window given the
--      cache-expiry wait.
--
-- KEEP IN SYNC
--   The free-model set below must match ``_DEFAULT_FREE_LLM_MODELS`` in
--   ``storage/lite_llm_manager.py`` (and the ``FREE_LLM_MODELS`` env override).
--   Additive union means renamed/legacy names (e.g. ``kimi-k3``) can stay in
--   this list harmlessly; they simply never match a live model.
-- =============================================================================

-- =============================================================================
-- PREFLIGHT COUNT (read-only). Run first to size the affected rows.
--   case_a_still_missing      -- post-#143 free teams missing a free model
--   case_b_still_budget_gated -- pre-#143 free teams still budget-gated
--   total_affected            -- union of both (what the UPDATEs write)
-- =============================================================================
-- SELECT
--   count(*) FILTER (
--     WHERE t.max_budget IS NULL
--       AND COALESCE(cardinality(t.models), 0) > 0
--       AND t.models <@ fm.models
--       AND NOT (t.models @> fm.models)
--   ) AS case_a_still_missing,
--   count(*) FILTER (
--     WHERE t.max_budget IS NOT NULL
--       AND t.max_budget <= 0
--   ) AS case_b_still_budget_gated,
--   count(*) FILTER (
--     (t.max_budget IS NULL
--        AND COALESCE(cardinality(t.models), 0) > 0
--        AND t.models <@ fm.models
--        AND NOT (t.models @> fm.models))
--     OR
--     (t.max_budget IS NOT NULL AND t.max_budget <= 0)
--   ) AS total_affected
-- FROM "LiteLLM_TeamTable" AS t
-- CROSS JOIN (
--   SELECT ARRAY[
--     'glm-5.2',
--     'minimax-m2.5',
--     'minimax-m2.7',
--     'kimi-k3',
--     'deepseek-v4-flash'
--   ]::text[] AS models
-- ) AS fm;

BEGIN;

WITH free_models AS (
    SELECT ARRAY[
        'glm-5.2',
        'minimax-m2.5',
        'minimax-m2.7',
        'kimi-k3',
        'deepseek-v4-flash'
    ]::text[] AS models
)

-- Case A: post-#143 free teams (budget cleared, models restricted to a
-- partial free subset). Union in any missing free models.
UPDATE "LiteLLM_TeamTable" AS t
SET models = ARRAY(
    SELECT DISTINCT unnest(COALESCE(t.models, '{}') || fm.models)
)
FROM free_models fm
WHERE t.max_budget IS NULL
  AND COALESCE(cardinality(t.models), 0) > 0
  AND t.models <@ fm.models        -- currently a subset of the free set
  AND NOT (t.models @> fm.models); -- but missing at least one free model

-- Case B: pre-#143 free teams (budget-gated at 0). Convert to the canonical
-- free state: clear budget enforcement and (additively) install the free set.
UPDATE "LiteLLM_TeamTable" AS t
SET models = ARRAY(
        SELECT DISTINCT unnest(COALESCE(t.models, '{}') || fm.models)
    ),
    max_budget = NULL
FROM free_models fm
WHERE t.max_budget IS NOT NULL
  AND t.max_budget <= 0;

COMMIT;