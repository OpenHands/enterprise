# PR 763 review fixes — October 27, 2026

Base: `e410f7acb1c33713f7d452daa90fc042c487eca4` (current main at refresh).
SDK, tools, agent-server, lint environment and bundled sandbox: **1.58.1**.

## Findings addressed

1. **Migration collision:** move the unshipped profile-tools migration to **199**,
   after main's **198**. Released migrations are unchanged. Fresh upgrade and
   downgrade/re-upgrade pass on PostgreSQL 16 with both pg8000 and psycopg2
   ([six checks](migration-review-results.json)). Encrypted profile upgrade tests
   cover starting revisions 192, 195 and 198, preserving opaque profiles and
   intentional version-3 empty tool lists.
2. **Sandbox security policy:** the released SDK settings and launch-additions
   schemas cannot transport `system_prompt_kwargs`. Build native agents from the
   resolved profile settings and use the existing supported raw-agent transport
   to carry `cli_mode: false`. Resolve tools with the SDK's `launch_tool_specs`
   using the sandbox's `/server_info` browser capability. The SDK still finalizes
   runtime context, memory and skills. ACP stays on the settings transport.
3. **Child launcher:** use `resolve_tool_specs` for defaults, preserving
   `SwitchLLMTool`. Explicit empty/restricted lists remain unchanged.

Before the behavioral fixes, four new regression cases failed: the child
launcher lost model switching, and the sandbox flag was absent for each of
null/empty/restricted profile tools. After the fixes, JSON round-trip tests render
sandbox risk tiers and reject CLI tiers; browser-capability tests cover both
runtime states and explicit selections. The profile object is not mutated.

## Real HTTP/runtime proof

[Ten cases pass](http-profile-review-results.json) through the production profile
router/resolver/request builder and a separate SDK 1.58.1 server over HTTP, using
real PostgreSQL. Returned native agents render the sandbox security policy.
Default launches with and without the child launcher retain model switching;
restricted coding profiles omit browser tools. Empty tools, persona, suffixes,
MCP and secret selections survive. ACP creation and invalid-profile rejection
still pass. Azure DeepSeek executes the stdio MCP tool and finishes successfully.

Reproduce with `.pr/run_agent_profile_http_qa.py` using an authorized
`QA_LLM_JSON` supplied through the environment. Do not put credentials in source
or logs. `.pr/migration_matrix_qa.py` reproduces the migration driver matrix.

## Automated checks

- **8,391 backend tests passed**, **75.91% coverage**, above CI's 72% threshold
  (450.62 seconds; Python 3.12.13, locked dependencies, four workers).
- Frontend lint/typecheck and production build pass on Node 22.
- Python pre-commit, including mypy over 531 source files, passes.
- Migration static integrity and locked dependency checks pass; both PostgreSQL
  drivers pass all six migration round trips.

The backend command matches CI's suite and coverage requirements:

```sh
TMPDIR=/tmp uv tool run --from uv==0.13.5 uv run --locked pytest -q -n 4 --dist worksteal tests/unit --cov --cov-branch --cov-fail-under=72 --tb=short
```

## Validation boundaries

This uses a local bearer identity fixture, not deployed Keycloak/Canvas. No
Replicated instance was changed. ACP creation does not prove authenticated ACP
CLI inference. The small Azure call does not demonstrate sustained coding-agent
capacity at 20,000 TPM. The human-testing checkbox remains unchecked.
