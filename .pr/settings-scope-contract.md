# Settings have one write owner

A scope owns a JSON object, not a copy of its effective settings. An organization owns a base object. A team and a person each own a sparse object. Read inside one authorized context:

    effective = overlay(overlay(org, team), person)

Write inside one transaction:

    local = edit(local, operations)
    validate(overlay(parent, local))
    save(scope, local)

`edit` cannot enumerate children or write its parent. `overlay` cannot access storage or credentials. Do not compute edits by comparing two effective documents: setting a limit to today's inherited value and choosing to follow future defaults produce identical JSON today but different behavior tomorrow.

Use explicit `set(path, value)` and `inherit(path)` in the canonical API. Inherit removes only a local contribution; it never deletes a parent value. `set` can express a literal null on nullable fields. Catalog entries use immutable IDs, arrays are atomic, and policy determines which paths may be written. Validate the resolved document under those permissions before committing. Use scope and ancestor revisions to reject stale edits. Keep provenance and permissions outside the values document.

Settings contain credential references only. Credential rotation is a command against the owning credential store, not a side effect of serializing resolved settings. An organization key is stored once; a personal save never distributes a key. Server-issued managed credentials are provisioned for an individual identity separately from inheritance.

## Implemented boundary

Org and org-member agent/conversation settings move to a single JSON column on each owner in migration 223. The old attribute names are projections, not writable copies. Coordinate the schema cutover with the application rollout: an old worker still queries the removed columns. Downgrade the application and schema together. Run the migration credential preflight before the maintenance window.

Migration 224 adds teams and tenant-bound membership. A request without `X-Team-Id` keeps existing behavior; with the header it must identify a team the caller belongs to in the effective org. The team settings endpoint exposes a revision-checked sparse edit to authorized admins, initially for execution limits. Account preferences, encrypted credentials, catalogs and launch-only state are not generic team-editable paths. Existing materialized pins are preserved.

Conversation settings accept explicit `set` and `inherit` operations, not `conversation_settings_diff`; old writers must upgrade at cutover. Agent-specific `agent_settings_diff` still translates legacy nulls at its boundary. A resolved launch view must never become a write payload.
