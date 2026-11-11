# PRD-198 review evidence

Temporary review artifacts. The `.pr/` directory is removed when the PR is approved.

## Screenshots

Both are 1280x860 captures of this branch's frontend in headless Chromium, driven by Playwright. All data is synthetic (`Example Org (synthetic)`, `github:example-org/approved-skills`). The repo's MSW mocks stood in for the backend, plus a small in-memory store for organization marketplaces and settings readback.

- `01-org-marketplace-legacy-default.png`: an organization marketplace with no saved preference. The new selector shows **Use existing behavior**, which saves `null` and keeps today's merge order.
- `03-org-marketplace-prefer-organization-saved-reopened.png`: the same marketplace after choosing **Use this marketplace's skill**, saving, refetching settings and reopening the editor. The saved `"marketplace"` value reappears.

Assertions checked during the same run:
- An unrelated edit on a legacy registration sends `public_skill_precedence: null`.
- Choosing the marketplace option sends `"marketplace"`, and the settings readback and the reopened editor both return `"marketplace"`.

## Matched-version probe

`test_prd198_matched.py` runs Enterprise and the patched SDK/Agent Server in one process, with no network. It checks the effective `github` skill content under each preference with public skills on and off, and checks that the plugin catalog reports `disabled` when the catalog setting is off. 7 cases passed.

To reproduce:
1. Install the companion SDK change (`openhands-sdk` and `openhands-agent-server`, built from the SDK PR branch) into this repo's environment in place of the pinned 1.60.1.
2. Copy `tests/public_extension_fixtures.py` from the SDK PR branch into this repo's `tests/` (the probe imports `tests.public_extension_fixtures`).
3. Run `uv run --no-sync pytest .pr/prd-198/test_prd198_matched.py` so uv keeps the patched packages installed for the probe.
4. Delete the copied fixture and restore the pinned versions with `uv sync --frozen`.

Not covered here: live deployment, warm-claimed or resumed sandboxes, and automation-triggered conversations.
