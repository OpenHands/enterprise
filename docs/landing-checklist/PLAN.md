# Feature Landing Checklist - Build Plan

## Goal

Move the team from "PR merged = launched" to a durable landing process that
covers documentation, E2E regression protection, self-hosted availability,
production release, a three-person bug bash, tech-council approval, public
launch communication, and eventual feature-flag removal.

The process spans months, so the durable source of truth is one Linear issue
per feature in the shared `Feature Launches` project. GitHub and Slack render
that state but do not replace it.

## Production repository allowlist

`repos.yml` is the expandable source of truth. It currently contains seven
production repositories:

1. `OpenHands/OpenHands`
2. `OpenHands/enterprise` - initial pilot
3. `OpenHands/agent-canvas`
4. `OpenHands/software-agent-sdk`
5. `OpenHands/saas-deploy`
6. `OpenHands/OpenHands-Cloud`
7. `All-Hands-AI/infra`

The central copy lives at
`OpenHands/enterprise:.github/landing-checklist/repos.yml` — see "Central
artifact location" below for why this moved off `OpenHands/OpenHands`. Each
production repo gets a small caller workflow that invokes the reusable
required check. For `OpenHands/enterprise` itself this is a same-repo
relative reference; any other repo onboarding today would need to either
vendor its own copy of the reusable workflow or be granted cross-repo
Actions access to the private `OpenHands/enterprise` repo. GitHub webhook
filters cannot fetch configuration, so onboarding also requires adding the
repo to the explicit filters in Automations 1 and 2; both prompts still
validate against `repos.yml`.

Note: `OpenHands/OpenHands` itself remains in the `production_repos` list
below — i.e. its own `(feat)` PRs are still subject to the checklist. What
changed is only where the *shared artifacts* (`repos.yml`, the reusable
workflow) are hosted, not whether `OpenHands/OpenHands` is a production repo.
Whether it should stay on this list at all, now that the team is moving away
from treating it as the main/shared repo, is a separate, bigger decision this
plan does not make — flag it with the team before onboarding it for real.

`OpenHands/docs` is a supporting integration repository. It is not part of the
production allowlist, but the automation needs access to verify and later
reveal feature documentation.

## Workflow architecture

| Lifecycle step | Mechanism | Artifact |
|---|---|---|
| PR title starts `(feat)` | Existing title check | Existing workflow |
| Review checklist: docs, E2E, flag/self-hosted wiring | Required GitHub status check | `workflows/landing-checklist-reusable.yml` and caller |
| Create durable feature ticket | GitHub event automation | `automations/01-create-feature-ticket.md` |
| Record merge commit | GitHub event automation | `automations/02-merge-sync.md` |
| Verify production release/deployment | Daily OpenHands automation | `automations/03-prod-tracker.md` |
| Remind PR author to schedule bug bash | Daily OpenHands automation | `automations/04-bug-bash-reminder.md` |
| Gate on bug-bash issue list and request council review | Daily OpenHands automation | `automations/05-bug-bash-gate.md` |
| Count council Slack reactions | Five-minute deterministic no-LLM automation | `automations/06-council-reaction-gate.md` |
| Verify approved flag is enabled in production | Production reconciler | Not yet implemented |
| Reveal docs, collect social evidence, and remind flag cleanup | Weekly OpenHands automation | `automations/07-flag-cleanup-reminder.md` |

## Eight-stage feature state

The Linear state machine and the GitHub/Slack tracker use these visible stages:

```text
Review -> Merged -> In Prod -> Bug Bash -> Council Review
       -> Council Approved -> Flag On -> GA
```

Bug-bash pending and active are separate Linear labels but collapse into the
single visible `Bug Bash` segment. See `linear/state-machine.md` and
`tracker-format.md`.

The `Council Approved` stage intentionally separates human approval from
technical evidence that the feature is enabled. Council reactions must never
move a ticket directly to `Flag On`.

## Council approval in Slack

Automation 5 posts one explicit approval request in the feature's existing
`#tech-council` thread and writes this marker to Linear:

```text
council-approval-message: <channel-id>/<message-ts>
council-pr-author-slack-user: <slack-user-id>
```

Automation 6 evaluates reactions on that exact message:

- `white_check_mark` (`✅`) means approve.
- `no_entry_sign` (`🚫`) means block.
- Two distinct human members of `#tech-council` are required to approve.
- Any valid blocking reaction wins, regardless of approval count, until the
  blocking reaction is removed.
- Reactions from bots, app users, deleted users, Slackbot, users outside the
  channel, or the original `(feat)` PR author do not count.

When quorum is reached, Automation 6 moves the ticket from
`stage:council-review` to `stage:council-approved`, records the approvers,
updates the PR tracker, and replies in the Slack thread. It does not reveal
documentation or start the cleanup timer.

This is a deterministic high-frequency check, so it is implemented as a
stdlib-only custom script rather than invoking an LLM up to 288 times/day.

## Enterprise pilot

The pilot runs in the private `OpenHands/enterprise` repository. It has the
needed lifecycle surfaces:

- Frontend E2E workflow: `.github/workflows/fe-e2e-tests.yml`
- Feature flags: `frontend/src/utils/feature-flags.ts`
- Release creation: `.github/workflows/release.yml`
- Release image tagging: `.github/workflows/tag-image.yml`
- Helm chart bump: `.github/workflows/bump-chart.yml`
- Development promotion: `.github/workflows/promote-to-development.yml`

For this pilot, a merged Enterprise feature is considered in production only
after the full chain is verified:

```text
Enterprise feature merge
-> Enterprise semver release
-> ghcr.io/openhands/enterprise-server release image
-> OpenHands/OpenHands-Cloud chart image tag and appVersion bump
-> OpenHands/saas-deploy production chart pin
-> production GitOps state
```

A release tag, image, or chart PR by itself is not sufficient.

## Central artifact location — scoped to enterprise for now

The original design put the reusable presubmit workflow and `repos.yml` in
`OpenHands/OpenHands`, on the reasoning that it's the one repo every
production repo already depends on. The team is moving away from treating
`OpenHands/OpenHands` as the shared/main repo, so both artifacts instead
live directly in `OpenHands/enterprise` — the only production repo with a
caller installed so far — rather than in `OpenHands/OpenHands` or a new
dedicated repo:

- `.github/workflows/landing-checklist.yml` (caller) and
  `.github/workflows/landing-checklist-reusable.yml` (the real logic) both
  live in `OpenHands/enterprise`. The caller invokes the reusable workflow
  via a same-repo relative path (`./.github/workflows/landing-checklist-
  reusable.yml`), so there is no cross-repo Actions reference to maintain
  for the pilot.
- `.github/landing-checklist/repos.yml` also lives in `OpenHands/enterprise`.
- Because `OpenHands/enterprise` is **private** (unlike the public
  `OpenHands/OpenHands` the original design assumed), every automation that
  reads `repos.yml` or `tracker-format.md` must fetch them via the
  authenticated GitHub Contents API with `GITHUB_TOKEN` (e.g. `gh api
  repos/OpenHands/enterprise/contents/<path>` with `Accept:
  application/vnd.github.raw`) rather than a public
  `raw.githubusercontent.com` URL, which will not work against a private
  repo. Automations 1, 2, 3, 4, 5, and 7 have all been updated accordingly.

This is deliberately scoped to the pilot, not a permanent home. If/when a
second production repo needs the reusable workflow, there are two options,
neither decided yet:

1. That repo vendors its own copy of `landing-checklist-reusable.yml` (no
   cross-repo dependency, but logic drifts across copies over time), or
2. `OpenHands/enterprise` grants that repo's Actions cross-repo access under
   Settings → Actions → Access (keeps one shared implementation, but means a
   private repo is now serving reusable workflows to other repos, which is
   worth a deliberate decision rather than defaulting into).

`OpenHands/OpenHands` remains in the `production_repos` allowlist — this
change does not remove its own `(feat)` PRs from the checklist. It only
stops it being the *host* for the shared artifacts.

## Self-hosted install testing during the bug bash

Checklist item 3 (flag + Helm/embedded-cluster wiring) is a pre-merge code
check: it confirms the wiring exists, not that anyone has actually installed
and exercised the feature that way. Real install testing happens during the
mandatory bug bash instead, where 3+ people and a real environment are
already required:

- **Helm install test — required today.** A bug bash is not valid without a
  non-empty `helm-test:` field in its `bug-bash-report`, recording a real
  Helm-based install (against `OpenHands/OpenHands-Cloud`'s
  `charts/openhands` chart) with the flag turned on.
- **Replicated / embedded-cluster install test — required once that
  capability ships.** `repos.yml`'s `capabilities.replicated_preview_supported`
  gates this: while `false`, `n/a-pending-support` is accepted for the
  `replicated-test:` field; once flipped to `true`, a real note/link is
  required, same as `helm-test`.

There is currently no scripted or documented equivalent, for either Helm or
Replicated, of the SaaS-side feature-preview flow that
`OpenHands/enterprise#92` added (which spins up a `saas-deploy` staging
namespace via ArgoCD's PR-generator ApplicationSet — a different mechanism
from a self-hosted Helm or Replicated install). Until an equivalent
self-hosted preview tool exists, bug-bash participants stand up and tear down
Helm installs by hand; this is a known gap worth closing with tooling similar
in spirit to `enterprise#92`'s preview-environment skill, scoped to
self-hosted rather than SaaS.

## Hidden documentation lifecycle

Feature documentation is checked into `OpenHands/docs` during development with
Mintlify frontmatter `hidden: true`. This removes the page from navigation,
search, sitemap, and assistant context, but it is not access control: a person
with the direct URL can still view it.

The page remains hidden through `stage:council-approved`. It is revealed only
after a production reconciler independently advances the ticket to
`stage:flag-on`. Automation 7 opens a reveal PR removing the `hidden` field and
enables auto-merge after normal required checks pass; it must not bypass branch
protection.

See `docs-visibility.md` for details.

## What is built in this workspace

- Expandable production repo config with `OpenHands/enterprise` added.
- Reusable GitHub presubmit and per-repo caller workflow.
- PR checklist template snippet.
- Linear state machine with `stage:council-approved`.
- Shared eight-stage GitHub and Slack tracker format.
- Prompt-preset specifications for Automations 1-5 and 7.
- Deterministic council reaction poller, tests, and deployment specification.
- Enterprise-specific production release detection in Automation 3.
- Post-release evidence checks for real docs, E2E coverage, self-hosted wiring,
  bug-bash completion, release availability, and social launch URL.
- Explicit Helm install-test requirement (and a Replicated install-test
  requirement, gated behind a `repos.yml` capability flag) wired into the
  bug-bash report format and Automation 5's validation.
- Hidden-doc verification and reveal lifecycle.

Nothing is deployed, committed, or pushed from this workspace yet.

## Remaining implementation gap

Build the production reconciler:

```text
stage:council-approved -> stage:flag-on
```

For the Enterprise pilot it must:

1. Read the exact feature-flag name from the Linear ticket.
2. Identify the approved flag-enablement change.
3. Verify it is present in the released Enterprise artifact.
4. Verify the corresponding production state in `OpenHands/saas-deploy`.
5. Record release, chart, commit, and timestamp evidence on Linear.
6. Update the PR tracker and Slack thread.
7. Advance to `stage:flag-on` and trigger the docs reveal.

The reconciler must fail closed when evidence is missing or ambiguous.

## Deployment prerequisites

1. Deploy Automations 1 and 2 on a publicly reachable OpenHands Automations
   host. This local environment has no `RUNTIME_URL` and cannot receive GitHub
   webhooks; use polling only for local testing.
2. Create the shared Linear `Feature Launches` project and every label in
   `linear/state-machine.md`, including `stage:council-approved`.
3. Provision `LINEAR_API_KEY`, `GITHUB_TOKEN`, and `SLACK_BOT_TOKEN` on the
   automation agent server.
4. Give the Slack bot `chat:write`, `reactions:read`, `channels:read`,
   `users:read`, and `users:read.email`; add `groups:read` if `#tech-council`
   is private.
5. Invite the bot to `#tech-council`.
6. Give the GitHub integration access to private `OpenHands/enterprise`,
   `OpenHands/saas-deploy`, and `All-Hands-AI/infra`, plus the required docs
   permissions described above.
7. Add the PR template snippet and caller workflow to `OpenHands/enterprise`.
8. Run the caller once, then select its stable `landing-checklist` job from the
   repository's branch-protection check picker and make that emitted check
   required for the Enterprise pilot (do not guess the fully qualified UI name).
9. Decide whether the PR author or a marketing/comms partner owns the X/LinkedIn
   post. Automation 7 currently reminds the PR author and requires the public
   post URL before `stage:ga`.

## Pilot rollout

1. ~~Add the central artifacts to `OpenHands/OpenHands`~~ — superseded: the
   central artifacts (both workflow files, `repos.yml`) already live directly
   in `OpenHands/enterprise`, so there is no separate central-repo step for
   the pilot. See "Central artifact location" above.
2. Install only the Enterprise caller and required check first.
3. Create a synthetic or low-risk Enterprise `(feat)` PR with a hidden docs
   page and an E2E test.
4. Run it through ticket creation, merge, release, chart bump, and production
   verification.
5. Exercise both Slack paths: one approval plus waiting, two approvals, and a
   blocking reaction that overrides quorum.
6. Verify docs remain hidden at `stage:council-approved` and are revealed only
   after `stage:flag-on` production evidence exists.
7. Record a public social-post URL, verify checklist item 6 is ticked from that
   evidence, and verify missing evidence blocks `stage:ga`.
8. Verify the three-month cleanup clock starts from the production-verified
   flag-on timestamp.
9. Observe for one release cycle, then roll the caller and automations out to
   the other production repositories.

Before broad rollout, decide whether `All-Hands-AI/infra` should use the full
product checklist or a scoped platform-feature variant.
