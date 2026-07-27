# SDK Pinning Examples

Examples from real commits showing how to pin SDK packages to unreleased commits, branches, or released versions.

## Pin to a Specific Commit

Example from commit `169fb76` (pinning all 3 packages to SDK commit `100e9af`):

### `dependencies` array (PEP 508 format)

```toml
"openhands-agent-server @ git+https://github.com/OpenHands/software-agent-sdk.git@100e9af#subdirectory=openhands-agent-server",
"openhands-sdk @ git+https://github.com/OpenHands/software-agent-sdk.git@100e9af#subdirectory=openhands-sdk",
"openhands-tools @ git+https://github.com/OpenHands/software-agent-sdk.git@100e9af#subdirectory=openhands-tools",
```

### `openhands/app_server/sandbox/sandbox_spec_service.py`

```python
AGENT_SERVER_IMAGE = 'ghcr.io/openhands/agent-server:<merge-commit-sha>-python'
```

**⚠️ Important:** The image tag is the **merge-commit SHA** from the SDK CI, not the commit hash used in `pyproject.toml`. Look up the correct tag from the SDK PR description or CI logs.

## Pin to a Branch

Example from commit `430ee1c` (pinning to branch `openhands/issue-2228-sdk-settings-schema`):

### `[tool.uv.sources]`

```toml
[tool.uv.sources]
openhands-sdk = { git = "https://github.com/OpenHands/software-agent-sdk.git", branch = "openhands/issue-2228-sdk-settings-schema", subdirectory = "openhands-sdk" }
openhands-agent-server = { git = "https://github.com/OpenHands/software-agent-sdk.git", branch = "openhands/issue-2228-sdk-settings-schema", subdirectory = "openhands-agent-server" }
openhands-tools = { git = "https://github.com/OpenHands/software-agent-sdk.git", branch = "openhands/issue-2228-sdk-settings-schema", subdirectory = "openhands-tools" }
```

## Using `[tool.uv.sources]` Override

Instead of a PEP 508 direct reference in the `dependencies` array, keep the PyPI version there and add a `[tool.uv.sources]` override. Example from commit `1daca49`:

```toml
[tool.uv.sources]
openhands-sdk = { git = "https://github.com/OpenHands/software-agent-sdk.git", subdirectory = "openhands-sdk", rev = "4170cca" }
openhands-agent-server = { git = "https://github.com/OpenHands/software-agent-sdk.git", subdirectory = "openhands-agent-server", rev = "4170cca" }
openhands-tools = { git = "https://github.com/OpenHands/software-agent-sdk.git", subdirectory = "openhands-tools", rev = "4170cca" }
```

## Released PyPI Version (standard release)

Example from commit `929dcc3` (SDK 1.11.5):

### `dependencies` array

```toml
"openhands-agent-server==1.11.5",
"openhands-sdk==1.11.5",
"openhands-tools==1.11.5",
```

### `openhands/app_server/sandbox/sandbox_spec_service.py`

For released versions, the image tag uses the version number:

```python
AGENT_SERVER_IMAGE = 'ghcr.io/openhands/agent-server:1.11.5-python'
```

However, **some releases use a commit-hash tag** even for the released version. Check which tag format exists on GHCR. Example from `929dcc3`:

```python
AGENT_SERVER_IMAGE = 'ghcr.io/openhands/agent-server:010e847-python'
```

## Regenerate the Lock File

After any change to `pyproject.toml`, always regenerate `uv.lock` (the repo's only lockfile):

```bash
uv lock
```

## CI Guards

- **`check-package-versions.yml`**: Blocks merge to `main` if `[project] dependencies` or `[tool.uv.sources]` pins a package to a git ref or URL (prevents shipping unreleased SDK pins)
- **`check-version-consistency.yml`**: Validates version strings match across `pyproject.toml`, `package.json`, `package-lock.json`, and verifies compose files use `agent-server` images
