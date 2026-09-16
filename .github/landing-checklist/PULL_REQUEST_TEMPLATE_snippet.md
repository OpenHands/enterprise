<!--
Complete this section for conventional feature PRs (`feat:` or `feat(scope):`).
The landing-checklist workflow parses the marker, item labels, and evidence
fields below.
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
