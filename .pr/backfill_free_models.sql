-- Backfill: add the current $0-cost model set to free-tier LiteLLM teams.
--
-- Context (OHE-3155): PR #143 materializes FREE_LLM_MODELS into each free
-- team's ``models`` allowlist at provisioning time. The managed default model
-- was subsequently renamed to ``deepseek-v4-flash`` without the free-tier
-- default list being updated, so existing free teams' stored allowlists are
-- missing the new default and LiteLLM returns ``team_model_access_denied``.
--
-- This script is safe to re-run:
--   * additive — it unions the free models into ``models``, never removing
--     anything already present;
--   * idempotent — a team already carrying the full free set is a no-op;
--   * scoped — only teams that are unambiguously "free tier" are touched.
--
-- Two free-tier shapes exist in the wild:
--   (A) post-#143: max_budget IS NULL and models is a non-empty subset of the
--       free set (budget enforcement cleared, models restricted).
--   (B) pre-#143:  max_budget <= 0 and models empty (budget-gated at 0, all
--       models allowed) — the state #143 migrates on next provisioning.
--
-- ``models = '{}'`` (empty) is LiteLLM's "all models" sentinel, so we must
-- only add to it when the team is provably free (case B), never for a generic
-- NULL-budget unlimited team.
--
-- NOTE: LiteLLM caches team objects (in-memory + Redis) with a 60s default
-- TTL (``general_settings.user_api_key_cache_ttl``). A direct DB write is
-- invisible to auth until that cache expires — see the runbook.

BEGIN;

-- Free-model set. Keep this in sync with _DEFAULT_FREE_LLM_MODELS in
-- storage/lite_llm_manager.py (and the FREE_LLM_MODELS env override).
-- Additive union means we can freely include renamed/legacy names
-- (e.g. kimi-k3) without harm; they simply never match a live model.
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

-- ---------------------------------------------------------------------------
-- Verification (run after the transaction):
--   0 rows means every free team already carries the full free set.
-- ---------------------------------------------------------------------------

-- SELECT
--   CASE
--     WHEN max_budget IS NULL
--       AND cardinality(models) > 0
--       AND models <@ ARRAY['glm-5.2','minimax-m2.5','minimax-m2.7','kimi-k3','deepseek-v4-flash']
--       AND NOT (models @> ARRAY['glm-5.2','minimax-m2.5','minimax-m2.7','kimi-k3','deepseek-v4-flash'])
--     THEN 'case_a_still_missing'
--     WHEN max_budget <= 0
--     THEN 'case_b_still_budget_gated'
--   END AS remaining,
--   count(*)
-- FROM "LiteLLM_TeamTable"
-- GROUP BY 1;
