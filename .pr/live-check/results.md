# Live check on `enterprise-server:sha-a78dbc0` (saas-deploy preview pr-2463)

Run of `setup_tour_check.py` on 2026-11-20 as the preview's instance-admin identity,
with `GET /api/admin/setup-state` stubbed to an active guide for its Personal Workspace.
Retries were only for HTTP 503s from the preview, not for failed checks.

```
PASS fault1: popover={'title': 'Choose a provider', 'progress': '2 of 5', 'x': 479, 'y': 454}
PASS fault2: url=https://pr-2463.staging.all-hands.dev/settings/agent popover=None trail=[['Save the LLM profile', 466, 604], None]
PASS save: url=https://pr-2463.staging.all-hands.dev/settings/org-members trail=[['Save the LLM profile', 466, 511], None, ['Invite users', 466, 104]]
PASS cleanup: profiles=['Default'] active=Default
```

`trail` is every position the popover took after the action: `None` is no popover.
No entry is in the top-left corner.
