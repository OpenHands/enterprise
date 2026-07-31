# Feature Landing Checklist

This directory holds the full design for the engineering "landing checklist"
process: GitHub presubmit checks, Linear state machine, and the OpenHands
automations that carry a feature from `(feat)` PR merge through production
verification, bug bash, tech-council approval, docs reveal, and eventual
feature-flag removal.

Start with [`PLAN.md`](PLAN.md) for the overall architecture and rollout plan.

## What's live in this repo vs. reference-only

Originally the plan was to host the reusable checklist logic and the
canonical `repos.yml` allowlist in a single shared location
(`OpenHands/OpenHands`) so all production repos consume the same
implementation. The team is moving away from treating `OpenHands/OpenHands`
as that shared home, so for now — scoped to this pilot — **everything is
live directly in this repo**, `OpenHands/enterprise`. See `PLAN.md`'s
"Central artifact location" section for the reasoning and the deferred
decision about what happens when a second production repo needs this.

Wired up and active:

- `.github/workflows/landing-checklist.yml` — the caller workflow, invoking
  `.github/workflows/landing-checklist-reusable.yml` via a same-repo
  relative reference (no cross-repo Actions dependency).
- `.github/workflows/landing-checklist-reusable.yml` — the actual presubmit
  check logic.
- `.github/landing-checklist/repos.yml` — the canonical production repo
  allowlist. Because this repo is private, automations that read this file
  fetch it via the authenticated GitHub Contents API (`GITHUB_TOKEN` +
  `Accept: application/vnd.github.raw`), not a public raw URL.
- `.github/pull_request_template.md` — updated with the
  `<!-- landing-checklist:v1 -->` checklist block.

Everything else in this directory (`repos.yml`, the Linear templates, and
the automation specs under `automations/`) is a design-reference copy kept
in sync with what's live under `.github/`, for visibility and review — it is
not deployed anywhere else. Nothing here needs to be deployed to
`OpenHands/OpenHands` or any other repo for the pilot to work.
