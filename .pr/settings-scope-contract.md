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

## Compatibility boundary in this PR

The existing `*_settings_diff` transport uses a sparse object: omitted means no edit, null requests inheritance, other submitted leaves are explicit even when equal to the parent. The adapter retains the submitted paths through typed validation; the two existing JSON columns are storage projections of the agent/conversation blocks. This patch is not a physical single-document cutover. Existing materialized overrides cannot safely be deleted by comparing them with today's parent.

Next, move each remaining settings family behind the same scope read/write boundary before replacing legacy columns with a versioned document per scope. Keep account preferences outside org/team precedence, authorize explicit team membership before enabling team layers, and compare both UI and launch reads during migration. Never treat an old representation and a new document as independently writable sources of truth.
