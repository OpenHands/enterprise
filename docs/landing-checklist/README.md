# Feature landing notifications

This pilot connects feature landing state to five internal release targets:

```text
SaaS:        staging -> production
Replicated:  unstable -> beta -> stable
```

The implementation is intentionally split into stacked changes:

1. This foundation defines the policy, event schema, lifecycle, tracker format,
   and deterministic presubmit check.
2. A release consumer validates environment events and attributes included PRs.
3. A guidance engine links verified E2E tests or derives labelled suggestions
   from PR and Linear evidence.
4. A delivery layer renders idempotent Slack and email notifications.

The environment producers remain in their owning repositories. The policy file
references the expected workflows and GitOps paths without attempting to deploy
cross-repository changes from this repo.

## Source of truth

- `.github/landing-checklist/repos.yml`: environment, delivery, and notification
  policy.
- `.github/landing-checklist/tracker-format.md`: GitHub, Slack, and email display
  contract.
- `docs/landing-checklist/linear/state-machine.md`: lifecycle and evidence rules.
- `enterprise/server/services/landing_notifications/`: tested deterministic
  models and policy logic.

## Security boundary

Release producers run only after trusted environment deployments. Notification
secrets never run in pull-request workflows. Cross-repository metadata should be
read through a narrowly scoped GitHub App. External contributors are not emailed
without an explicit address and opt-in.
