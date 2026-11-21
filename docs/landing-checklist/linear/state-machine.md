# Feature landing state machine

## Visible stages

```text
Review -> Merged -> Testable -> Bug Bash -> Council Review -> Council Approved -> Production Enabled -> GA
```

The Linear ticket stores the current stage plus environment evidence. SaaS and
Replicated releases are parallel tracks, not additional sequential stages.

## Derived transitions

| Stage | Required evidence |
|---|---|
| Review | Feature PR is open. |
| Merged | Feature PR merge SHA is recorded. |
| Testable | Every configured `test_target` has a ready release event containing the merge SHA. |
| Bug Bash | The DRI started the structured bug bash. |
| Council Review | Bug bash completed and required defects are resolved or scheduled. |
| Council Approved | The deterministic Slack reaction gate reached quorum. |
| Production Enabled | Every `final_target` is ready and the production feature flag is independently verified. |
| GA | Public docs/social evidence is recorded and cleanup requirements are complete. |

## Environment evidence

Each environment record contains:

- event ID
- environment enum
- released component version or Replicated sequence
- released source ref
- environment URL
- workflow or Argo CD evidence URL
- release timestamp
- test guidance snapshot used in notifications

Evidence is immutable by event ID. A later release appends evidence rather than
rewriting an earlier release record.

## Delivery policy

The default policy requires SaaS staging plus Replicated unstable and beta for
`Testable`, then SaaS production plus Replicated stable for
`Production Enabled`. A feature may explicitly declare SaaS-only or
self-hosted-only targets. Missing targets fail closed.
