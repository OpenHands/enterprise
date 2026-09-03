<!-- Keep this PR as draft until it is ready for review. -->

<!-- AI/LLM agents: be concise and specific. Do not check the box below. -->

HUMAN:


- [ ] A human has tested these changes.

AGENT:

---

## Why

<!-- Describe problem, motivation, etc.-->

## Summary

<!-- 1-3 bullets describing what changed. -->
-

## Issue Number
<!-- Required if there is a relevant issue to this PR. -->

## How to Test

<!--
Required. Share the steps for the reviewer to be able to test your PR. e.g. You can test by running `npm install` then `npm build dev`.

If you could not test this, say why.
-->

## Video/Screenshots

<!--
Provide a video or screenshots of testing your PR. e.g. you added a new feature to the gui, show us the video of you testing it successfully.

-->

## Type

- [ ] Bug fix
- [ ] Feature
- [ ] Refactor
- [ ] Breaking change
- [ ] Docs / chore

## Notes

<!-- Optional: migrations, config changes, rollout concerns, follow-ups, or anything reviewers should know. -->

<!--
Complete this section for conventional feature PRs (`feat:` or `feat(scope):`).
-->

<!-- landing-checklist:v1 -->
### Feature landing checklist

_Required before merge:_
- [ ] Documentation added for the feature on the website
- [ ] E2E test added covering regression / up-to-spec behavior
- [ ] Feature is gated by an `ENABLE_<FEATURE>` flag and exposed through the appropriate Helm / embedded-cluster configuration

_Evidence:_
- Linear tracking ticket: `___________`
- Feature flag name: `___________`
- Primary E2E test: `path, test name, or URL`
- Delivery targets: `saas-staging, saas-production, replicated-unstable, replicated-beta, replicated-stable`

_Tracked after merge:_
- [ ] Required test environments are ready
- [ ] Bug bash completed with 3+ engineers
- [ ] Tech council approved
- [ ] Required final environments and production flag are verified
- [ ] Public launch and cleanup completed
