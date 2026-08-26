# Environment release events

A deployment producer emits one `environment-release` event only after an environment is ready for developer verification. The versioned contract is [`environment-release.schema.json`](../../.github/landing-checklist/environment-release.schema.json). Consumers must deduplicate on `event_id` and channel delivery keys.

## Release lanes

| Environment | Success signal | Producer change |
| --- | --- | --- |
| `saas-staging` | Argo CD reports the staging application healthy and synced after the promotion PR merges | Add a post-sync notification or hook in `OpenHands/saas-deploy`; a merge alone is not release success |
| `saas-production` | Argo CD reports the production application healthy and synced after the reviewed promotion merges | Add the same post-sync event with the production environment and URL |
| `replicated-unstable` | `OpenHands/OpenHands-Cloud` [`release-replicated-unstable.yml`](https://github.com/OpenHands/OpenHands-Cloud/blob/main/.github/workflows/release-replicated-unstable.yml) completes publication | Emit after the existing release job succeeds |
| `replicated-beta` | `OpenHands/OpenHands-Cloud` [`release-replicated-beta.yml`](https://github.com/OpenHands/OpenHands-Cloud/blob/main/.github/workflows/release-replicated-beta.yml) completes publication | Emit after the existing release job succeeds |
| `replicated-stable` | A stable artifact is published and its KOTS cursor is available | Add a dedicated stable promotion workflow; generic manual deployment is not a reliable release signal |

`OpenHands/enterprise` owns consumption, contributor attribution, landing-tracker updates, test guidance, and email/Slack delivery. Producer repositories should only emit the shared event. Cross-repository changes are intentionally not implemented in this repository.

## Required payload

```json
{
  "schema_version": 1,
  "event_id": "openhands-cloud:replicated-beta:1450",
  "environment": "replicated-beta",
  "status": "ready",
  "released_at": "2026-08-26T18:04:00Z",
  "producer_repo": "OpenHands/OpenHands-Cloud",
  "producer_sha": "0123456789abcdef0123456789abcdef01234567",
  "run_url": "https://github.com/OpenHands/OpenHands-Cloud/actions/runs/123",
  "environment_url": "https://beta.example.com",
  "artifact": {
    "kind": "replicated-release",
    "version": "1.2.3",
    "sequence": 1450,
    "kots_cursor": null
  },
  "components": [
    {
      "repo": "OpenHands/enterprise",
      "previous_ref": "1111111111111111111111111111111111111111",
      "released_ref": "2222222222222222222222222222222222222222"
    }
  ]
}
```

Each component range is the source of truth for GitHub PR attribution. Producers must not attempt to identify developers or tests.

## Consumer behavior

1. Validate the event against schema version 1 and reject non-`ready` statuses.
2. Resolve merged PRs in every component range and exclude automated changes and bot accounts.
3. Match registered feature PRs to their Linear landing trackers.
4. Store release evidence and derive the next landing stage from configured test and final targets.
5. Discover declared or changed E2E tests at the released commit. Clearly label PR- or Linear-derived steps as suggestions rather than verified tests.
6. Notify each contributor according to per-environment email and optional Slack preferences.
7. Update the Linear tracker with the environment URL, artifact, release run, and stage transition.
8. Record `event_id:github_login:channel` only after provider success so retries remain safe.

Provider execution defaults to dry-run. Live delivery requires Resend and Slack credentials; Linear updates require a Linear API key. Recipient addresses and channel preferences belong in a secret-backed runtime directory, not in source control.

## Rollout order

1. Deploy the Enterprise consumer in dry-run and replay one recent event from each automated lane.
2. Enable Linear comments and verify event markers prevent repeated tracker evidence.
3. Enable email for an internal pilot group, then optional Slack DMs.
4. Add SaaS post-sync emitters.
5. Add Replicated unstable and beta emitters.
6. Create the stable promotion workflow and enable the final lane.
