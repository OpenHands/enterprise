# Podman named-network URL regression

## Scope

This PR recovers the one-file network-membership fix from the October 30, 2026 standalone rootless Podman experiment. It changes how the existing Docker-compatible backend chooses sandbox URLs. It does not introduce a Podman installer, alter SELinux policy, or change the separate Helm experiment or Podman backend PR #1203.

Podman reported `HostConfig.NetworkMode=bridge` while `NetworkSettings.Networks` contained the configured named network. The original backend looked only at NetworkMode, found no published ports, and returned `exposed_urls=[]`; the Enterprise conversation start failed. Checking actual membership allows the existing configured proxy URL to use the container ports. Unrelated network membership still falls back to published host ports.

## Recorded working deployment, October 30

| Component | Exact recorded version/configuration |
| --- | --- |
| OS | Red Hat Enterprise Linux 9.6 (Plow), x86_64, with updated RHUI packages; not a frozen 9.6 package set |
| Kernel | `5.14.0-570.141.1.el9_6.x86_64` |
| Podman RPM | `podman-5.8.2-8.el9_8.x86_64`; engine/API version `5.8.2` |
| OCI runtime | `crun` (actual engine selection); runc was installed but was not the selected runtime |
| Engine configuration | Rootless host UID 10001; netavark, cgroups v2, overlay storage |
| Enterprise base image | `ghcr.io/openhands/enterprise-server:1.79.0` |
| Enterprise recorded image ID | `7d5f0824a73b75d07e39eeb0b06157e1196d72a27977193ef6c7fd0c46ec5b2f` |
| Enterprise recorded repo digests | `sha256:04fe3fa5253533b1d805c274018cbac03fe8bd51ffb3e82dcc24efe1d3c68264`, `sha256:6b91630c8ff72835ff491e9669537edd3f9d7f2ba4fa4ee9d4ae407d78c26e5e` |
| Agent image | `ghcr.io/openhands/agent-server:1.58.1-python` |
| Docker Compose client | `2.39.4`; Docker Engine and Sysbox were not installed |
| Derived app/worker image | `localhost/mbank-full-enterprise:1.79-podman-network-v1` |
| Derived image ID | `0b516167670a430da751f79e700128e76d77504c5c2a4c66700aabaafff09ff6` |
| SELinux | Host Enforcing; app/worker only used `label=disable` for rootless socket access; sandboxes retained labels and default seccomp |

The derived image copied only the patched `docker_sandbox_service.py` onto the released Enterprise base. Original file SHA256: `781ed55fe24b8f121572e0bb62e0524f72b333a05051255a5a671674a5e5fa75`; patched file SHA256: `3a9a18c036f0b66ae6f8680121a1980e00dbe6c90c03031b0d6817ab34c97383`.

The deployment record reports real Enterprise agent tasks for two users, authenticated Canvas, native repository automation and pause/resume after the fix. Its six metadata cases included ordinary Docker named networks, default bridge published ports, host networking and unrelated networks. These are historical results on the 1.79.0-derived deployment, not a fresh execution of the current main revision. The recorded artifacts are retained locally; infrastructure availability has not been rechecked for this PR.

Source record: [team results and deployment context](https://app.notion.com/p/4087be798a178153afaed01b87a10bcf), section “Draft mBank rootless Podman results and next steps.” Local provenance: `podman-fullstack/RESULTS.md`, `evidence/01-preflight.log`, `evidence/00-install-images-qualified.log`, `evidence/09-regression-baseline.log`, `evidence/10-build-network-adaptation.log`, `evidence/11-host-inventory-corrected.log`, and `evidence/network-membership.patch` under the October 29 task workspace. Private host addresses, credentials and customer payloads are not included here.

## Current regression validation

Base: Enterprise main `e0e84df71b80e0863d076545cc702cc514ae65f3`.

```sh
JWT_SECRET=test-jwt-secret .venv/bin/python -m pytest --noconftest tests/unit/app_server/test_docker_sandbox_network_urls.py -q
```

The test runs the actual `DockerSandboxService._to_sandbox_info` method with controlled metadata and checks complete agent/VSCode URLs. Engine, database and HTTP clients are mocks; no new deployment or LLM call is made.

- Before the product change: 2 failed, 8 passed. The failing cases were `podman-named-network` and `network-mode-is-id`, both returning empty URLs rather than container-port URLs.
- After the recovered change: 10 passed.
- Compatibility cases: Docker named-network mode; default bridge published ports; host networking; unrelated networks with and without published ports; missing/null network membership; no configured network.

`--noconftest` avoids the repository's global PostgreSQL-container bootstrap because this test needs no database and no local Docker daemon is available. This does not qualify an actual Docker engine, the full database-backed service suite, or current-main end-to-end Enterprise behavior. The macOS SDK browser dependency aborts on display detection inside the filesystem sandbox; the successful test runs used the same environment outside that sandbox.

Final local checks: all 10 cases passed after formatting; the required staged-file pre-commit run passed, including Ruff and repository-wide mypy. The full PostgreSQL-backed suite was not run.
