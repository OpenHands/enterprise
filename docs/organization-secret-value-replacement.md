# Replace an organization secret value without deleting it

The value-replacement API updates the existing secret in one database transaction.
Readers see either the old value or the committed new value; replacement does not
remove the secret in between. The existing metadata PUT still edits only names
and descriptions. Neither new endpoint returns a secret value.

## Request flow

1. Read `GET /api/organizations/{org_id}/secrets/{secret_name}/version`.
   The response is `{"version": "<opaque UUID>"}`. This requires the existing
   `VIEW_ORG_SETTINGS` permission. The UUID is random, not a value hash.
2. Generate a fresh UUID for this replacement's `request_id`. Send
   `PUT /api/organizations/{org_id}/secrets/{secret_name}/value`:

   ```json
   {
     "expected_version": "<version from step 1>",
     "request_id": "<fresh UUID for this operation>",
     "value": "<new secret value>"
   }
   ```

   Replacement requires `MANAGE_ORG_SECRETS`, the same permission used for
   organization-secret creation, editing and deletion. The path organization
   remains subject to the existing authentication and API-key organization checks.
3. A successful response is `200` with `{"version": "<new opaque UUID>"}`.
   The name, description, owner and row identity are preserved. The new value is
   encrypted before storage. A fresh replacement gets a new revision even if
   its submitted value happens to equal the existing value.

The two calls are deliberately separate: the submitted `expected_version` makes
another writer's intervening replacement detectable. Only one distinct writer
using a given current version can succeed; other writers receive `409` without
changing the value. An unconditional overwrite or create-if-missing is not provided.

## Retrying after a timeout

Keep the entire original request until its outcome is known. Retry with the same
`expected_version`, `request_id` and value. If that operation is still the latest
successful replacement, the server returns its original version without writing
again. Changing the value or expected version while reusing the latest request ID
returns `409`.

Only the latest receipt is retained. After another replacement, an older retry
returns `409` and does not restore its older value. Generate a fresh request ID for every new operation and never reuse it. The
server checks only the latest receipt, not a permanent organization-wide ledger;
old ID reuse is not detected after later operations.
No historic values or value hashes are retained for retries. Treat the request ID
and its original expected-version pair as sensitive: a privileged caller holding
that pair can use matching/mismatching retry responses to test a guessed value.
Do not log request IDs with their expected versions or request bodies. The version
returned by the metadata GET alone does not enable this check.

On `409`, inspect current metadata and reconcile with the source of truth. Do not
blindly obtain a fresh version and replay a stale source value. A timeout alone
does not establish whether the original write committed. A rename changes the
request URL; if the same row remains, its revision and latest receipt survive the
rename. Deletion/recreation starts a different revision and rejects stale requests.

## Errors and disclosure

- `401`/`403`: the existing authentication/permission checks denied the request.
- `404`: the shared secret is absent in that organization. Personal secrets are
  not targets of this API, even with the same owner or name.
- `409`: stale version, inconsistent latest retry, or duplicate shared rows with
  an ambiguous name. Reconcile duplicates rather than selecting an arbitrary row.
- `422`: invalid body, UUID or unsupported field. Only field locations, error
  types and messages are returned (unknown extra-field names are suppressed); submitted values and validation context are
  omitted. Do not log request bodies in sync clients.

Only syntactically valid JSON reaches normal permission checks; malformed JSON
can receive a sanitized `422` before authentication, following FastAPI's parsing
order. These response safeguards do not qualify every deployment log/audit sink.

## Runtime and rollout limits

This changes stored values. Running conversations may retain earlier static
secret snapshots; replacement is not live refresh, external-provider revocation,
or erasure of already delivered credentials. New loads of the store receive the
new value after commit. Choose a separate operational policy for restarting or
revoking existing consumers.

Migration 193 adds a random revision and the latest operation's two UUID markers
to the existing secret table; ciphertext is unchanged. Personal rows receive an
unused revision because they share that table. The database default also covers
inserts that omit the new fields. The migration takes table locks and backfills
existing rows; assess its duration on the installation's table size.

Apply the migration first, then upgrade **all** application writers before using
the replacement endpoint. Older metadata code can merge a detached old value
back into the row; mixed-version writes do not provide the full concurrency
contract. The new metadata implementation locks rows in ID order and changes only metadata
inside its transaction, using one database connection. Existing concurrent-create/rename name-uniqueness issues
are not redesigned here; ambiguous names fail closed in the replacement API.

Implementation sources: [value routes](../server/routes/org_secret_values.py),
[organization store](../storage/org_secrets_store.py),
[stored row](../storage/stored_custom_secrets.py), and
[migration](../migrations/versions/193_add_org_secret_value_versions.py).

For rollback, first stop using replacement and deploy the previous application
everywhere; only then downgrade the schema. New application code cannot read the
old schema. Downgrading and re-upgrading regenerates revisions and discards
receipts, so outstanding requests require reconciliation instead of replay.

The migration requires PostgreSQL 13 or newer for the built-in
[`gen_random_uuid()`](https://www.postgresql.org/docs/13/functions-uuid.html).
Repository development, migration CI and these tests use PostgreSQL 16
([test configuration](../tests/postgres_testdb.py),
[local database default](../Makefile)). Older installations require an explicit
compatibility decision; this PR does not install database extensions.
