# Feature Landing Checklist

This directory holds the full design for the engineering "landing checklist"
process: GitHub presubmit checks, Linear state machine, and the OpenHands
automations that carry a feature from `(feat)` PR merge through production
verification, bug bash, tech-council approval, docs reveal, and eventual
feature-flag removal.

Start with [`PLAN.md`](PLAN.md) for the overall architecture and rollout plan.

## What's live in this repo vs. reference-only

Per the plan, the reusable checklist logic and the canonical `repos.yml`
allowlist are meant to live in a single shared location
(`OpenHands/OpenHands`) so all production repos consume the same
implementation. In this repo, the following pieces are wired up and active:

- `.github/workflows/landing-checklist.yml` — the caller workflow, invoking
  the reusable required check from `OpenHands/OpenHands`.
- `.github/pull_request_template.md` — updated with the
  `<!-- landing-checklist:v1 -->` checklist block.

Everything else in this directory (`workflows/landing-checklist-reusable.yml`,
`repos.yml`, the Linear templates, and the automation specs under
`automations/`) is the design reference copied here for visibility and
review. The reusable workflow and `repos.yml` still need to be deployed to
`OpenHands/OpenHands` as the single source of truth; see `PLAN.md` for the
deployment sequence.
