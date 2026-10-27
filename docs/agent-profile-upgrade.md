# Upgrade Enterprise Agent Profiles

This release adopts the SDK 1.58.1 launch contract. The Enterprise Python SDK,
agent-server and tools packages, the lint environment, and the bundled sandbox
image must use the same released version. External Helm/runtime-api image pins
must be updated by their deployment owner as well.

## Before deploying

1. Back up the database, encryption key configuration and current application/image versions.
2. Freeze settings and profile writes before starting any new application pods.
   Stop integration workers that can save settings too. Keep the freeze in place
   until every old application pod and writer has stopped.
3. Run `uv run python -m scripts.audit_agent_profiles` against the target database
   with the new release's normal database and encryption configuration. It is
   read-only and prints only organization/profile ids and finding counts.
   An exit status of 2 requires repair or a compatible newer release before
   proceeding. Never delete opaque profiles to make the audit pass.
4. Run `uv run alembic heads` and verify one head. The profile-tools migration is
   revision 199 after 198. Do not reuse or edit the released revision 175.

A rolling update with unrestricted old writers is unsafe: older releases can
remove profiles they cannot parse when saving an envelope. The new release
preserves those opaque entries, but cannot change writers that still run old
code. This upgrade therefore requires a write freeze and a complete writer
cutover, rather than relying on mixed-version saves.

## Migration and cutover

Run `uv run alembic upgrade head` with the writes still frozen. Revision 199
changes only OpenHands profiles at a known legacy schema version (missing, 1 or
2) whose `tools` is `[]`: those old Cloud profiles always launched the standard
set, so they become `tools: null`. Current profiles at version 3 retain a
purposefully empty list. Nonempty tool selections, ids, active pointers, future
versions and invalid version markers are preserved.

The migration reports scanned, migrated and unreadable organization counts and
logs only ids for unreadable envelopes. A successful Alembic run with unreadable
rows is not a clean preflight. Rerun the read-only audit and resolve every blocking
finding before enabling profile writes. Invalid active profiles now reject
launches instead of falling back to broader settings; ordinary settings remain
readable for repair.

Start the new application pods and workers, verify their installed packages and
sandbox images, then unfreeze writes. Validate a profile with no tools, a profile
with selected tools, an ACP profile, a scoped secret list and a missing reference.
Check materialize versus actual launch, persona/suffix preservation and child
conversation profile inheritance.

Existing conversations remain readable. New launches into older/custom sandboxes
require SDK >=1.54.0 and return an actionable upgrade error otherwise. Replace
those sandboxes using the normal lifecycle; do not assume changing the default
image upgrades an already running sandbox.

## Rollback

After profile writes resume, do not roll back to an older application image that
cannot preserve version-3 or opaque entries. Keep writers frozen and roll forward
to a compatible image. If an older image is unavoidable, restore the pre-upgrade
database snapshot and matching encryption configuration together, accounting for
all writes since that backup. Alembic downgrade does not reconstruct the meaning
of an old empty tool list and is not a data rollback.

## Compatibility boundaries

OpenHands launches use the supported raw-agent transport because SDK 1.58.1
settings cannot carry the web sandbox's `cli_mode: false` security prompt flag,
PLAN presets or request-level raw prompts. The app builds from resolved profile
settings and uses the sandbox's advertised browser availability with the SDK tool
resolver. Explicit tool selections remain authoritative; defaults retain model
switching even when the optional child-conversation launcher is enabled. The
sandbox still finalizes runtime context (including time, memory and skills).
ACP launches continue to forward resolved settings for runtime finalization.

Profile secret selection filters stored, integration and request-provided secrets.
The Azure DevOps credential-helper token written during repository setup remains
registered for output masking even when omitted from that selection. Profile
secret selection does not revoke credentials already present in the workspace.

Profile MCP selection filters the user's configured MCP catalog, as defined by
the SDK. Enterprise's platform MCP integration remains a separate serving-layer
integration. Exact parent MCP inheritance within task/workflow delegates is
upstream work in software-agent-sdk#5743; this release preserves the SDK's
existing delegation scope rules.

Canvas's default-profile migration and editor gating are owned by
OpenHands/OpenHands#17991. This backend release does not remove the Canvas default
branch or claim that adding client skills produces catalog parity.
