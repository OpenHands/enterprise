# Feature landing tracker format

The Linear feature ticket is the durable state store. GitHub and Slack render
that state. The synchronous presubmit comment uses
`<!-- landing-checklist:v1 -->`; the asynchronous lifecycle comment uses
`<!-- landing-tracker:v2 -->` so the two writers never race.

## Stage bar

```text
Review -> Merged -> Testable -> Bug Bash -> Council Review -> Council Approved -> Production Enabled -> GA
```

`Testable` means every environment in the feature's `test_targets` has ready
evidence. `Production Enabled` means every `final_target` has ready evidence,
the council approved the feature, and its production flag was independently
verified.

## Environment matrix

Render all five environments beneath the stage bar. The SaaS and Replicated
tracks are parallel and may advance in either order.

```text
Environment readiness
✅ SaaS staging — chart 0.53.0 — test
⬜ SaaS production
✅ Replicated unstable — sequence 1462 — test
🔄 Replicated beta — deploying
⬜ Replicated stable
```

Each ready row links to the environment and the workflow or Argo CD evidence.
The active notification must also include a `How to test` section with either:

1. a verified E2E test link at the released commit, or
2. clearly labelled suggested steps grounded in the PR's `How to Test` section
   or the linked Linear ticket's acceptance criteria.

Never present generated suggestions as an existing automated test.

## GitHub comment

```markdown
<!-- landing-tracker:v2 -->
### Feature landing tracker

✅ Review → ✅ Merged → 🔄 Testable → ⬜ Bug Bash → ⬜ Council Review → ⬜ Council Approved → ⬜ Production Enabled → ⬜ GA

**Environment readiness**
- ✅ SaaS staging — `0.53.0` — [open environment](...) · [release evidence](...)
- ⬜ SaaS production
- ✅ Replicated unstable — sequence `1462` — [open environment](...) · [release evidence](...)
- 🔄 Replicated beta — deploying
- ⬜ Replicated stable

**How to test**
- Verified E2E: [`test_org_migration.py::test_migrate`](...)
- Suggested manual check: Create a test organization, run migration, and verify memberships. Source: [Linear FEAT-42](...).

Linear: [FEAT-42](...) · Slack: [thread](...)
```

Slack and email render the same environment and test guidance. Slack uses
Block Kit and email uses semantic HTML, but both are generated from the same
notification model.
