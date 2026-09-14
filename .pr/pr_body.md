<!-- Keep this PR as draft until it is ready for review. -->

<!-- AI/LLM agents: be concise and specific. Do not check the box below. -->

HUMAN:


- [ ] A human has tested these changes.

AGENT:

_This PR was created by an AI agent (OpenHands) on behalf of the assignee._

---

## Why

Non-superadmin users on an OpenHands Enterprise instance have no supported way to discover who the SuperAdmins are. The only endpoint that lists SuperAdmins (`GET /api/admin/super-admins`) is gated by `MANAGE_SUPER_ADMINS`, which only SuperAdmins hold — so a regular user (or even an org owner) cannot answer "who is my administrator?". Because the initial SuperAdmin is assigned implicitly (first user / upgrade backfill), instances can end up where nobody remembers who it is.

Linear: https://linear.app/all-hands-ai/issue/OHE-3196

## Summary

- When `USER_PROVISIONING_ENABLED` is on (managed-users / enterprise mode), `GET /api/admin/super-admins` now requires only an authenticated user — so any logged-in user can discover the instance SuperAdmins (user_id/email).
- When the flag is off, the endpoint stays superadmin-only via `MANAGE_SUPER_ADMINS` (unchanged behavior).
- `grant`/`revoke` are untouched and remain superadmin-only regardless of the flag.
- The flag is read at request time (not import time) so it is testable; in production it is a startup constant, consistent with how the provision-user endpoint is gated.

## Issue Number

OHE-3196

## How to Test

Unit tests (mocked, no DB required): `uv run pytest tests/unit/server/routes/test_super_admins.py`

The test file covers four cases:
- flag off + superadmin → 200 (unchanged behavior)
- flag off + no super role → 403 (unchanged behavior)
- flag on + any authenticated user → 200 (new)
- flag on + unauthenticated → 401 (new)

Manual: set `USER_PROVISIONING_ENABLED=true`, start the SaaS backend, and call `GET /api/admin/super-admins` as a non-superadmin user — expect 200 with the SuperAdmin list. With the flag unset/false, the same call as a non-superadmin returns 403.

> Note: I could not run the pytest suite in this sandbox because the root `conftest.py` starts a Postgres testcontainer that requires Docker, which is unavailable here. I verified all four cases with a standalone httpx harness that mirrors the test file exactly (all pass). CI will run the full suite.

## Video/Screenshots

N/A — backend-only change.

## Type

- [ ] Bug fix
- [x] Feature
- [ ] Refactor
- [ ] Breaking change
- [ ] Docs / chore

## Notes

- Scope of what is exposed: `user_id` + `email` (matches the existing `SuperAdminResponse` shape). No new fields on the `User` model.
- The "Contact your administrator" UI affordance is intentionally out of scope here; it will land with the SuperAdmin dashboard work (OHE-651).
- `USER_PROVISIONING_ENABLED` is off by default in staging/prod, so this change is opt-in per environment via Helm — conservative default, no behavior change unless the flag is already on.
