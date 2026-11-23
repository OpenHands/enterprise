# Cloud trajectory archive worker

This worker drains the SDK journal from a platform-retained POSIX filesystem. Its
Cloud identity reads pinned legacy objects and writes generation-guarded shadow
objects. A run produces a seal and a private comparison report. A failed or
incomplete conversation is held and does not prevent later conversations from
being processed.

## Run a pilot

Use an Enterprise image with the SDK and Agent Server versions containing
[SDK #7071](https://github.com/OpenHands/software-agent-sdk/pull/7071). Run the
worker in the trusted Cloud environment with Application Default Credentials.
Give its identity read access to the legacy bucket and read and write access
to the private shadow archive prefix. Enable GCS object version retention.

Provide separate journal and witness directories plus a Cloud-owned enrollment
file. Each row includes the conversation UUID, independently recorded run IDs
in UUID hex format, origin, and a pinned legacy inventory object reference.
The legacy inventory and its referenced event objects are resolved in the
legacy bucket. The worker holds a conversation without a verified legacy copy.

```bash
python -m openhands.app_server.trajectory_archive_worker \
  --spool /spool/journal --witness /spool/witness \
  --enrollment /pilot/enrollment.json \
  --bucket "$TRAJECTORY_BUCKET" --prefix "$TRAJECTORY_PREFIX" \
  --legacy-bucket "$LEGACY_BUCKET"
```

The worker must be able to reopen the databases and their WAL files. Keep them
together and retain them until the acknowledged range can be recovered. While
capture is active, share the spools with the writer on the same POSIX host.
The production sandbox workspace uses GCS-backed FUSE. The platform must
provide a separate durable handoff before enrolling traffic.

The command emits a JSON line per enrolled conversation and exits nonzero if
any conversation is held. Detailed evidence stays in the private archive.
Repeat the command after a storage interruption. Generation preconditions and
content verification make an identical retry safe.

## Production validation

Before enabling a cohort, exercise the actual handoff across sandbox replacement
and session-key revocation. Run a live GCS lost-acknowledgment and missing-generation
drill. Use the independent enrollment manifest as the denominator and review
both stores and the witness evidence. Follow the observation and cutover gates in
[SDK #7036](https://github.com/OpenHands/software-agent-sdk/issues/7036).
