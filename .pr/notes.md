# OHE-3724 — QA notes

## Environment

Frontend dev server against the MSW mock handlers (no backend):

```bash
cd frontend
VITE_MOCK_API=true VITE_MOCK_SAAS=true npx react-router dev --port 12000 --host 0.0.0.0
# http://localhost:12000/super-admin/auth-providers/1/users
```

Mock data covers the three interesting row shapes: a named account, an
account whose provider reported no email, and a second named account.

## Screenshots

- `screenshots/user-links-table.png` — table at 1400px. Each column is a
  primary line (name + email / external email) with the raw id on a muted
  second line.
- `screenshots/relink-dialog.png` — the re-point dialog now restates the
  external identity and the account it currently points at above the
  account picker.
- `screenshots/user-links-table-narrow.png` — 820px viewport; both lines
  truncate rather than wrapping, so row height stays uniform.

## Not covered

- No real provider round-trip was exercised: the display name comes from
  `User.git_user_name`, so a row only shows a real name once the linked
  account has signed in through a git provider at least once. Otherwise it
  falls back to the email local part, matching the instance user directory.
