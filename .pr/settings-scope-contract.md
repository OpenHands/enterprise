# Settings have one write owner

A scope owns a JSON object, not a copy of its effective settings. An organization owns a base object. A team and a person each own a sparse object. Read inside one authorized context:

    effective = overlay(overlay(org, team), person)

Ordinary submits compare canonical submitted values with the current effective values under the owner lock. An unchanged submit preserves the existing local contribution; changing a value to its parent removes the override. The database never receives a resolved snapshot. See `docs/scoped-settings-upgrade.md` for the shipped contract and migration procedure.

## Implemented boundary

Org and org-member agent/conversation settings move to a single JSON column on each owner in migration 223. The old attribute names are projections, not writable copies. Coordinate the schema cutover with the application rollout: an old worker still queries the removed columns. Downgrade the application and schema together. Run the migration credential preflight before the maintenance window.

Migration 224 adds teams and tenant-bound membership. A request without `X-Team-Id` keeps existing behavior; with the header it must identify a team the caller belongs to in the effective org. The team settings endpoint exposes a revision-checked sparse edit to authorized admins, initially for execution limits. Account preferences, encrypted credentials, catalogs and launch-only state are not generic team-editable paths. Existing materialized pins are preserved.

Conversation settings and the tool concurrency limit accept ordinary values. Other agent preferences still use the existing agent patch writer.
