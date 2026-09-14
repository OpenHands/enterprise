# Budget ownership and adoption contract

Status: accepted for implementation planning; the coordinated product and chart
changes described here are not yet implemented or certified for release.

## Authority

OpenHands stores intended recurring allowances. LiteLLM meters usage and enforces
caps, including usage through SDK clients outside the OpenHands UI. Observations
must identify their timestamp, enforcement layer, and counter; a stored desired
limit must never be presented as proof of enforcement.

| Control mode | Authority | Ordinary budget writes |
| --- | --- | --- |
| `managed` | OpenHands | Authorized through the controller |
| `external` | An administrator or another system | Forbidden |
| `needs_adoption` | Not established | Forbidden |

Mode, policy enablement, reconciliation health, and operation progress are
independent. Missing or malformed ownership fails closed for budget mutation,
not for read-only diagnostics. A feature flag cannot bypass this invariant.
Personal-workspace billing is a separate policy domain and must remain explicitly
distinguished from organization-budget management.

## Transitions and operations

The first release supports keeping external management, immediate adoption, and
handoff from managed to external. Scheduled adoption is deferred.

An administrator with organization-settings permission previews the actual live
policy and explicitly confirms both the current-cycle remaining allowance and
future recurring limits. Neither an old desired value nor an absolute LiteLLM cap
determines that intent. Preview fingerprints cover policy, roster, enforcement
identities, and reset epochs, not ordinary spend growth.

Adoption uses a durable authorized operation while the organization remains
non-managed. The operation records its actor, generation, idempotency key,
counter identities, baseline snapshot, and immutable target caps. Commit this
intent before any remote mutation; a flush is not a commit. On retry, including
after a lost response or process death, apply the same targets. Do not resnapshot
spend and grant another allowance. Cycle rollover and baseline repair need the
same durability, not a weaker ad hoc path.

Apply targets deterministically and read back every owned field before marking
the operation complete and the organization managed. Failed operations can have
partial external effects; show them truthfully and offer retry. Handoff stops
future writes without clearing current caps and must serialize against in-flight
operations. Do not silently undo a partial operation using old caps after spend
has advanced.

LiteLLM does not participate in the OpenHands database transaction or provide a
shared compare-and-swap boundary. We cannot guarantee atomic adoption against a
simultaneous external policy editor. Confirmation explicitly transfers authority;
readback detects mismatches, and subsequent managed reconciliation restores
authorized targets. Read failures alone must not remove existing enforcement.

## Counter and field safety

Caps are tied to enforcement counters. Summed key spend for a roster-only user
is not the baseline of a newly created membership counter, which starts at zero.
Keep counter provenance and reset configuration. A native LiteLLM reset can
invalidate a cumulative baseline; normalize supported reset policy with explicit
consent or reject an unsupported transition with an actionable explanation.

Distinguish team total, default member, private member, and key/model constraints.
Unknown policy must not be represented as unlimited or zero. Preserve unrelated
model allowlists and operator-owned blocks. Key rotation must not remove a cap,
change its counter silently, or discard independent key policy.

All writers share per-organization exclusion and ownership checks, including:

- Policy updates, overrides, enable/disable/clear, rollover, repair, and preflight.
- Team creation/upsert, member provisioning, and billing bulk updates.
- Membership/user deletion and managed-key repair/regeneration.

Access revocation remains possible without treating revocation as permission to
reset a budget. Transport-level negative tests must cover direct internal helpers
as well as service entrypoints. General and budget maintenance runners must claim
tasks atomically; CronJob `Forbid` only excludes executions of the same CronJob.

## Upgrade and rollback

Classification performs no LiteLLM writes. Existing enabled organizations become
`needs_adoption`; disabled or missing settings do not imply ownership. Matching
caps cannot prove authorization. This also pauses automatic rollover for healthy
legacy customers, requiring coordinated adoption notices before rollout.

The chart must quiesce old application and maintenance writers before migration,
then start ownership-aware code and resume maintenance only after schema/image
readiness. Account for active Jobs and the general runner, not only the budget
CronJob. A short starting deadline and a post-upgrade hook alone are insufficient.
Expected non-managed skips are not preflight or maintenance errors.

Rollback is supported only to ownership-aware versions that retain the journal
and barrier. Do not advertise rollback to older writers as safe merely because
one CronJob is suspended. Prevent unsupported writers from starting, or block
the downgrade. Keep read-only diagnostics available throughout failures.

## Release gate

Ship the backend, UI, and chart safety as one usable release, even when reviewed
as independent or stacked PRs. Unit tests alone are not customer certification.
Use actual pinned LiteLLM, PostgreSQL, and the exact packaged Enterprise image to
test preservation on upgrade, preview/adoption, direct SDK spend, team/member
denial, current allowance, rollover, counter changes, partial failures, response
loss, concurrent workers, restart, handoff, and supported rollback.

Certify both healthy legacy state and customer-shaped state with divergent caps,
missing member baselines, roster-only/private memberships, accumulated spend,
and a suspended CronJob. Capture sanitized before/after evidence and exercise
login and adoption in the real UI. Normal customer activation must require no
SQL or Kubernetes commands.

The native LiteLLM recurrence investigation remains bounded and separate: it may
simplify future scheduling, but does not eliminate ownership, authorization,
counter identity, or durable transition requirements.
