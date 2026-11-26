# One settings model, sparse by ownership

**Decision proposal — not an implementation or release approval.** An organization and a member configure the same thing. Give them one Pydantic schema, one JSON representation, one read rule, and one write rule. An owner is identified by the database row, not by a field or subclass in the model.

## Start with the smallest example

Suppose the SDK default limit is 50 and the org sets it to 80. These are complete examples of the **stored contributions**, not abbreviated views of full rows:

| Owner | Stored JSON | Effective limit now | After org changes to 120 |
|---|---|---:|---:|
| Org | `{"conversation_settings":{"max_iterations":80}}` | 80 | 120 |
| Ana | `{}` | 80 | 120 |
| Bo | `{"conversation_settings":{"max_iterations":80}}` | 80 | 80 |

Ana can submit 80 any number of times: the submitted value is unchanged, so her row stays `{}`. Submitting 80 does not create a pin. Bo already has an override (for example, from migration); an unchanged submit preserves it. Bo can choose **Use organization default**. That deletes the local leaf, prunes the empty container, and writes `{}`. No parent or sibling row changes. “Minimal diff” means **only the values this owner explicitly owns**, not “values unequal to the parent.” Subtracting two resolved models loses the distinction between Ana and Bo. The org itself follows exactly the same rule relative to SDK or installation defaults.

## The data model

Define `Settings` once for **persistable preferences**. Reuse SDK field definitions, defaults, types, and discriminated variants. Do not introduce `OrgSettings`, `MemberSettings`, a recursive optional copy of `Settings`, or a second set of defaults in the frontend. Do not make a required field nullable just because a member can inherit it. Absence is about ownership; null is about the value's type.

Each existing owner row has one JSON object for these preferences. `{}` is a valid contribution at every scope. No per-field source tags, tombstones, stored operation list, or cached effective settings. Owner IDs, lock revisions, and schema migration bookkeeping are storage metadata, not preferences. A selected team, where supported, contributes another object of exactly the same shape; no team means no extra layer.

| Kind of field | Rule |
|---|---|
| Record with named preference fields | Overlay by field, recursively. Omitted leaves inherit. |
| Scalar, nullable value, array, or user-keyed map | Replace the entire value. `null`, `[]`, and `{}` are real values where the field type permits them. Do not prune an empty map that is itself a value. |
| Discriminated configuration | Overlay only within the same variant. An explicit kind change starts from the SDK's defaults for the new variant and uses its existing rules for shared fields. Never merge OpenHands-only keys into ACP. |

The schema, not the accident that two things are JSON objects, determines whether to recurse. Prefer atomic values unless there is an actual requirement to inherit inside them. Never infer array identity, invent per-entry deletion markers, or build a general JSON Patch engine.

## One read and one write

```text
read(Model, parent, local_json, context):
    return Model.model_validate(overlay(parent, local_json), context=context)

write(Model, parent, local_json, submitted, context):
    before = read(Model, parent, local_json, context)
    after = validate_submission(Model, before, submitted, context)
    candidate = copy(local_json)
    for path in changed_writable_fields(before, after, submitted):
        if after[path] == parent[path]: remove_local(candidate, path)
        else: assign_local(candidate, path, after[path])
    if candidate == local_json: return NO_WRITE
    return dump_owned(read(Model, parent, candidate, context), shape=candidate, context=context)
```

Compare canonical model values, not raw form strings or ciphertext. Omitted fields are untouched. An unchanged value preserves its previous ownership: no special intent needs to be inferred. A changed value equal to the effective parent removes the override; a changed value different from the parent becomes an override. The parent here is the actual inherited value for this scope, not necessarily the SDK factory default. Check revisions before comparing so an old form cannot pin a stale default.

These are semantic operations, not three new services. `overlay`, local edit, and serialization belong together in the SDK; a file store and Enterprise should pass the same contract tests. Start with SDK defaults and fold installation → org → selected team → member. Reads do not write back, seed profiles, rotate keys, or persist SDK defaults. A launch may consume the resolved `Settings`; a settings writer must never accept that launch snapshot as its local document.

**The subtle part is serialization.** A sparse contribution need not be valid by itself: a team can set `worktree_base="ref"` while the org supplies `worktree_base_ref="main"`. Calling the full model constructor on the team JSON can reject a valid configuration. Conversely, dumping the merged model with `exclude_unset=True` can copy the parent's explicit fields into the child. Validate **after** composition. Serialize the validated model through the candidate's field selection (a Pydantic `include` tree for record fields, whole-value selection for atomic fields). The local JSON already supplies that selection; do not store a separate mask. Keep explicit nulls and equal-to-default values. No `exclude_none`, `exclude_defaults`, blanket `model_construct`, or use of assignment side effects to track ownership. Test custom serializers and union branches explicitly before relying on `include` for an SDK type.

For example, after merging a parent's explicit `max_iterations=80` with a child's explicit `worktree_base_ref=null`, the model knows **both** fields were set. `exclude_unset=True` emits both. `model_dump(mode="json", include={"conversation_settings": {"worktree_base_ref"}})` emits just `{"conversation_settings":{"worktree_base_ref":null}}`. This was checked on a minimal nested Pydantic model; the SDK serializer still needs its contract tests.

Database code does just this: load authorized rows and revisions, pass their JSON to the SDK, and replace the target JSON with its returned serialization in one transaction. No column-by-column settings logic in a repository. The same model describes both owners; **only the resolved object is promised to be independently valid**. Requiring every partial row to construct a fully valid model in isolation would force either duplicate values or weaker validation.

## Ordinary submits need no edit protocol

Accept the form's writable values, full or partial. Org and personal forms use the same generic comparison above. Do not require dirty flags, per-field ownership flags, or a sequence of commands to save an ordinary form. Reject unknown or forbidden fields; omit unedited secrets rather than echoing a redacted placeholder. If an existing override already equals its parent and the user explicitly chooses **Use default**, remove that local field directly. That reset is distinct from an ordinary unchanged submit; there is no requirement to offer a “pin this identical value” command.

## Decisions under stress

- **Parent changes.** An inherited child sees the new value on its next read. A parent's valid edit can still invalidate a descendant's cross-field constraint. Do not fan out rewrites or silently fall back to defaults. Report the offending paths, block launch of the invalid combination, and allow an authorized repair against the raw local contribution without first requiring a successful resolved read. If a field group must *never* exhibit this behavior, declare it atomic instead of introducing descendant-wide transactions.
- **Variant changes.** If a member owns fields specific to a variant, bind that subtree to the variant (store its discriminator with the first variant-specific leaf). Otherwise a later org switch could reinterpret the member's data. Inheriting the entire variant removes the binding. If only shared fields are owned, do not pin a discriminator. Migrate legacy ambiguous subtrees against their actual parent, not a guessed SDK default.
- **Concurrency and access.** Resolve the effective tenant and check membership/permissions before loading layers. Check the revision of the local row and the parent revisions on which the edit was based; reject stale edits and reload. Validate against those same rows in the transaction. No second settings copy for concurrency control.
- **Secret serialization.** A secret owned by a settings row can be a normal SDK `SecretField`; it need not become a reference just because it is secret. Use the SDK cipher context for database validation and serialization, with strict decryption for read-modify-write. Require a configured cipher before persisting secret fields: without one, the SDK can redact on dump. Never accept a redacted placeholder as a replacement value. Persist only the owner's selected fields, so inheriting a credential never copies even its ciphertext into a child row. Client reads use the SDK redaction context, not the persistence context. Enterprise supplies key configuration and enforces access; it does not implement another cipher or field codec.
- **Resource ownership.** If a provider connection or gateway already owns a credential and manages its lifecycle, settings hold a reference, not another copy. Enforced limits, billing, membership, and consent records are not inheritable preferences. Legacy Enterprise credential columns use existing encryption helpers; their formats and keys are not assumed SDK-compatible. Verify old-key reads and SDK encrypted round trips on a restored copy before removing those helpers from settings persistence. Unrelated consumers are outside this deletion.

## What disappears, and in what order

1. **Prove the SDK boundary locally.** SDK 1.63.1 already has `validate_agent_settings`, `ConversationSettings.from_persisted`, and `apply_agent_settings_diff`. Reuse variant validation and migration. Do not adopt its legacy merge-patch semantics as the new contract: null means removal there, and the result is a resolved model. Extract shared internals if necessary. Keep a public SDK compatibility adapter only for independently supported SDK consumers; Enterprise should not call it to reconstruct local ownership.
2. **Cut over one real org and personal client together.** Replace the `agent_settings_diff` builder in `SdkSectionPage` and the conversation operation encoder with one ordinary form submission. Remove `_operations_from_patch` and the merge/dump/reparse chain in `Settings.update`, `OrgStore.apply_agent_patch`, and member-specific tool/variant edit reconstruction in `SaasSettingsStore._persist`. Replace `storage/settings_overlay.py`'s preference composition and the duplicate scoped operations with SDK calls. Remove legacy ORM projections when their last caller is gone. Move old-value conversions to migration; use SDK secret serialization and keep separately owned resources separate. Do not merely rename these branches inside the SDK.
3. **Cut over storage in a maintenance window.** Inventory every legacy field and writer, including workers and CLI clients. Preflight unknown fields, ambiguous variants, and inline credentials; stop rather than discard them. Drain writers, take and restore-test a backup, migrate owner contributions once, then start only new binaries. Preserve historical pins even when equal to the parent. Compare old/new effective views *and* local ownership before reopening. No dual writes. Roll back before reopening by restoring the verified database and matching old binaries; after new writes, require a reconciliation decision. The previous rehearsal was not a rehearsal of this design.

## Acceptance and deletion budget

- Run the same table-driven cases through SDK, local file, and Enterprise paths: empty layer, unchanged inherited submit (no write), unchanged historical equal-value override, changed override returned to the parent, explicit null, inherit, atomic empty collection, invalid cross-field pair, variant change, and repeated read/write without acquiring parent fields. Verify that personal edits change only the authorized membership row. Test stale revisions, cross-tenant rejection, no plaintext or redacted sentinels in stored secret fields, strict failure with the wrong key, and no inherited ciphertext in child rows. Exercise the same editor on a non-settings Pydantic model with a required field and a default to prove it is not settings-specific.
- Migrate representative old data, including old tool switches and both agent variants. Verify behavior again **after changing the parent**. Equal effective values before and after migration alone do not prove ownership survived.
- One preference schema, one composition rule, one SDK editor, one serializer. No shipped caller sends a legacy diff; no owner has its own merge function. Record whole-PR and production-only additions/deletions against `main` **in both repositories**. Count moved code on both sides. Do not delete migration safety or meaningful tests to manufacture a negative number. If independent team/profile features keep the PR positive, show their cost separately and split their delivery rather than calling relocation a simplification.
