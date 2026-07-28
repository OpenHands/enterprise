---
name: update-sdk
description: This skill should be used when the user asks to "update SDK", "bump SDK version", "pin SDK to a commit", "test unreleased SDK", "update agent-server image", "bump the version", "prepare a release", "what files change for a release", or needs to know how SDK packages are managed in the OpenHands repository. For detailed reference material, see references/docker-image-locations.md and references/sdk-pinning-examples.md in this skill directory.
---

# Update SDK

Bump SDK packages (`openhands-sdk`, `openhands-agent-server`, `openhands-tools`), pin them to unreleased commits for testing, and cut an OpenHands release.

## Quick Summary — How Many Files Change?

| Activity | Manual edits | Auto-regenerated | Total |
|----------|:------------:|:----------------:|:-----:|
| **SDK bump** (released PyPI version) | 2 | 1 | **3** |
| **SDK pin** (unreleased git commit) | 3 | 1 | **4** |
| **Release commit** (version bump) | 3 | 0 | **3** |

The auto-regenerated file is always `uv.lock` — the repo root is the only Python project.

## SDK Package Bump — 2 Files + 1 Lock File

Land as a separate PR before the release. Examples: `929dcc3` (SDK 1.11.5), `cd235cc` (SDK 1.11.4).

| File | What to change |
|------|----------------|
| `pyproject.toml` | `openhands-sdk`, `openhands-agent-server`, `openhands-tools` in the `[project] dependencies` array (PEP 508) |
| `openhands/app_server/sandbox/sandbox_spec_service.py` | `AGENT_SERVER_IMAGE` constant — set to `ghcr.io/openhands/agent-server:<version>-python` |

Then regenerate the lock file:
```bash
uv lock
```

## Docker Image Locations — All Hardcoded References

For the complete inventory of every file containing a hardcoded Docker image tag or repository, see `references/docker-image-locations.md`. Key files that must stay in sync during an SDK bump:

| File | Image reference | Updated during SDK bump? |
|------|----------------|:------------------------:|
| `openhands/app_server/sandbox/sandbox_spec_service.py` | `AGENT_SERVER_IMAGE = 'ghcr.io/openhands/agent-server:<tag>-python'` | ✅ Yes |
| `docker-compose.yml` | `AGENT_SERVER_IMAGE_TAG` default | ✅ Should be |
| `containers/dev/compose.yml` | `AGENT_SERVER_IMAGE_REPOSITORY` + `_TAG` defaults | ✅ Should be |

> **CI enforcement:** `.github/workflows/check-version-consistency.yml` validates version consistency and compose file image references on every PR and push to main.

### ⚠️ Docker Image Tag Gotcha (merge-commit SHA)

The SDK CI in `software-agent-sdk` repo tags Docker images with the **GitHub Actions merge-commit SHA**, NOT the PR head-commit SHA. When pinning to an SDK PR branch:

1. Check the SDK PR description for the actual image tag (look for the `AGENT_SERVER_IMAGES` section)
2. Or query the CI logs: the "Consolidate Build Information" job prints `"short_sha": "<tag>"`
3. The merge-commit SHA differs from the head SHA shown in the PR

For released SDK versions, images use a version tag (e.g., `1.12.0-python`) — no merge-commit ambiguity.

## Cutting a Release — 3 Files

A release commit updates the version string across 3 files. Gold-standard examples: 1.3.0 (`d063c8c`), 1.4.0 (`495f48b`).

| File | What to change |
|------|----------------|
| `pyproject.toml` | `version = "X.Y.Z"` under `[project]` |
| `frontend/package.json` | `"version": "X.Y.Z"` |
| `frontend/package-lock.json` | `"version": "X.Y.Z"` in **two** places (root object and `packages[""]`) |

> **Note:** `openhands/version.py` reads the version from `pyproject.toml` at runtime — no manual edit needed there.

### Compose Files (2 files)

Both compose files should use `ghcr.io/openhands/agent-server` with the current SDK version tag.

| File | What to verify |
|------|----------------|
| `docker-compose.yml` | `AGENT_SERVER_IMAGE_REPOSITORY` defaults to agent-server, `AGENT_SERVER_IMAGE_TAG` is current |
| `containers/dev/compose.yml` | Same — must use agent-server, not runtime |

### Release Workflow

#### Step 1: Verify the SDK bump has landed

```bash
grep -n "openhands-sdk\|openhands-agent-server\|openhands-tools" pyproject.toml
grep -n "AGENT_SERVER_IMAGE" openhands/app_server/sandbox/sandbox_spec_service.py
grep "AGENT_SERVER_IMAGE_TAG" docker-compose.yml containers/dev/compose.yml
```

#### Step 2: Merge the release PR

Don't bump versions or push tags by hand. `release.yml` runs release-please on
every push to `main`, which keeps a draft release PR open; merging it bumps
`pyproject.toml`, `frontend/package.json`, and `frontend/package-lock.json` and
pushes the `X.Y.Z` tag.

#### Step 3: Images get tagged automatically

Every push to `main` builds and publishes a `ghcr.io/openhands/enterprise-server` image for that commit (tagged by SHA, short SHA, and branch name). It is the only image this repo publishes.

The `X.Y.Z` tag then aliases that commit's image as `X.Y.Z`, `X.Y`, `X`, and `latest`, and opens a chart PR in `OpenHands/OpenHands-Cloud`. Non-semver tags just get their literal name applied.

Requires the commit to already be built. If you push the tag too early, the retag CI job fails loudly — re-run it from the Actions UI once the build completes.

## Development: Pin SDK to an Unreleased Commit

For detailed examples of all pinning formats (commit, branch, uv-only), see `references/sdk-pinning-examples.md`.

### Files to change (3 manual + 1 lock file)

| File | What to change |
|------|----------------|
| `pyproject.toml` | Pin all 3 SDK packages in `[project] dependencies` |
| `openhands/app_server/sandbox/sandbox_spec_service.py` | `AGENT_SERVER_IMAGE` — use the merge-commit SHA tag, NOT the head-commit SHA |
| `docker-compose.yml` | `AGENT_SERVER_IMAGE_TAG` default (for local development) |
| `uv.lock` | Auto-regenerated via `uv lock` |

### CI guard

The `check-package-versions.yml` workflow blocks merging to `main` if `[project] dependencies` (or `[tool.uv.sources]`) pins a package to a git ref or URL. This ensures unreleased SDK pins do not accidentally ship in a release.
