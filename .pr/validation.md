> Historical validation of the original PR. For the October 23 refresh against current main, see [refresh-validation.md](refresh-validation.md). Migration numbers and SDK versions below describe the earlier implementation.

# Enterprise Agent Profile validation

Implementation tested by the HTTP harness: `ea3be78b4d77b329e3b2ba6dfbba2aefcf1b8245`.
Runtime: the released `openhands-agent-server==1.54.0` wheel, on a real local HTTP
socket. The Enterprise packages and mypy environment use the same release.

## HTTP and model evidence

`agent-profile-http-qa.json` records seven passing cases. The harness mounts the
production Enterprise profile router over a fresh PostgreSQL database and uses
real organization permissions, profile storage, SDK resolution and the
production conversation builder. It forwards requests into the released Agent
Server over HTTP. A generated local bearer identity replaces Keycloak login;
this does not claim deployed Cloud authentication or full sandbox orchestration.

- Identical `default` and `default-copy` profiles produce matching empty tool
  selections, persona, MCP selection and profile/runtime suffixes. Preview is
  valid and both conversations return HTTP 201.
- Both request payloads and the runtime's actual secret registries contain only
  `ALLOWED_QA`; stored and API-supplied secrets outside the allow-list are absent.
- A dangling MCP reference returns structured HTTP 422 before any runtime
  conversation is created. An unknown profile returns 404; ordinary settings
  remain readable.
- An ACP profile with empty MCP/secret selections creates successfully through
  the settings transport, with an empty runtime secret registry. This checks
  ACP creation, not an authenticated external CLI turn.
- A future-version profile survives a real HTTP save of a different profile.
  Selecting the unreadable entry returns 422 without falling back, while
  ordinary settings remain readable.
- Saved Sonnet credentials drive a real model turn. Its ActionEvent calls the
  real stdio FastMCP `qa_echo` tool and its ObservationEvent contains
  `ENTERPRISE_PROFILE_QA_OK`; the conversation finishes.

The standalone harness is `.pr/agent_profile_http_qa.py`. Run from this repository
with `PYTHONPATH` set to the repository root, `AGENT_SERVER_URL` and
`SESSION_API_KEY` for an isolated SDK 1.54.0 server, and `OH_PERSISTENCE_DIR` set to
isolated state. `QA_LLM_PROFILE_DIR`, `QA_LLM_PROFILE` and `QA_CIPHER_KEY_FILE`
select existing saved credentials; defaults follow the local saved-profile
layout. The script starts and removes its own PostgreSQL container, creates no
real user settings, deletes its runtime conversations, and stores no credential
values in the JSON report.

## Automated and migration checks

The final implementation passed all 7,577 backend tests with:

```sh
TMPDIR=/tmp uv run pytest -n 4 tests/unit --tb=short -q
```

`TMPDIR=/tmp` matches Linux CI and avoids an unchanged test's macOS-specific
assumption about the temporary directory. This includes a regression verifying that an internal resolver TypeError is a
503, rather than a user configuration 422. The subsequent rebase brings in only
the Enterprise 1.71.0 release metadata; application behavior is unchanged.

The PostgreSQL suite executes a real Alembic 188→189 upgrade with encrypted
profile envelopes. Only known legacy empty tool lists change; current empty
selections, future/invalid records, ids and the active pointer survive. Migration
integrity reports one linear head at 189. Audit tests cover invalid schema
markers without mutating their input.

Frontend lint, staged type checking and production build pass; the affected
Agent settings suite passes all 10 tests. Python pre-commit and mypy against SDK
1.54.0 pass.

## Deployment limits

No deployed Cloud/Replicated environment or Kubernetes resource was changed.
Follow `docs/agent-profile-upgrade.md`: freeze writes, stop old writers, audit,
migrate and cut over completely. Update deployment-owned image references too.
Old writers cannot be made safe by this application's new reader alone.

Exact parent MCP inheritance in SDK delegates and Canvas default-path/catalog
migration remain in their upstream trackers. Planning and per-request raw
system prompts retain the SDK-supported raw-agent path because SDK 1.54.0
settings cannot represent those prompt presets.

After SDK 1.55.0 was released, the pins moved to 1.55.0. The full unit suite
passes on the released wheels: 7,643 passed.
