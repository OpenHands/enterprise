<a name="readme-top"></a>

<div align="center">
  <img src="https://assets.openhands.dev/logo-whitebackground.png" alt="OpenHands logo" width="340">
  <p align="center">
    <strong>OpenHands Enterprise — Backend Codebase</strong>
  </p>
  <p align="center">
    The source-available backend for OpenHands Enterprise, a self-hosted developer control center for coding agents and automations.
  </p>
</div>

<div align="center">
  <a href="https://docs.openhands.dev/enterprise"><img src="https://img.shields.io/badge/Documentation-000?logo=googledocs&logoColor=FFE165&style=for-the-badge" alt="Documentation"></a>
  <a href="https://www.openhands.dev/enterprise"><img src="https://img.shields.io/badge/Website-OpenHands%20Enterprise-3068es?style=for-the-badge" alt="OpenHands Enterprise Website"></a>
  <a href="https://go.openhands.dev/slack"><img src="https://img.shields.io/badge/Slack-Join%20the%20community-611f69?logo=slack&logoColor=white&style=for-the-badge" alt="Join us on Slack"></a>
</div>

<div align="center">
  <a href="https://docs.openhands.dev/enterprise">Docs</a> |
  <a href="https://www.openhands.dev/enterprise">Website</a> |
  <a href="./Development.md">Development Guide</a> |
  <a href="https://go.openhands.dev/slack">Slack</a>
</div>

---

## About OpenHands Enterprise

OpenHands Enterprise brings the power of autonomous coding agents to your organization with the governance, security, and compliance your enterprise demands.

OpenHands Enterprise allows you to run AI coding agents directly on your own servers or in your private cloud.

**This is the source-available, commercially licensed backend codebase for OpenHands Enterprise**. It provides a self-hosted developer control center for coding agents and automations, with additional enterprise features including authentication, billing, and integrations.

### License

> [!WARNING]
> OpenHands Enterprise is commercially licensed under the [Polyform Free Trial License](./LICENSE). This is **not** an open source license. Usage is limited to 30 days per calendar year without a commercial license.

For commercial licensing and pricing inquiries, please [contact us](https://www.openhands.dev/contact).

### What OpenHands Enterprise Offers

- Multi-use RBAC — Manage multiple users from a single platform
- Platform integrations — Invoke OpenHands directly from Slack, Jira, GitHub, GitLab, or Bitbucket
- Scalability — Run unlimited parallel agent conversations without local resource constraints
- Enterprise security — SAML authentication, RBAC, and centralized agent traces
- Usage Monitoring — Track and enforce budgets; monitor usage across all users

> [Choosing between OpenHands Agent Canvas and OpenHands Enterprise](https://docs.openhands.dev/enterprise/enterprise-vs-oss)

### Free 30 Day Trial

The preferred method of installing OpenHands Enterprise is via our Replicated installer.
- Fist, read the [Quick Start guide](https://docs.openhands.dev/enterprise/quick-start) to understand system requirements for OpenHands Enterprise
- Then, [register for a Trial account](https://install.r9.all-hands.dev/openhands/signup) via the Replicated installer portal.

### Run Locally with Docker Compose

For a local evaluation or development, `docker-compose.yml` starts the whole stack (app, worker, automation service, Agent Canvas, PostgreSQL and a reverse proxy) from the released images. Docker with the Compose plugin is the only requirement.

```bash
docker compose up -d
```

Then open <http://localhost:9043/oauth/idp/signup> to create the admin account, and add an LLM under **Settings**. Agent Canvas is served at <http://localhost:9043/canvas/>. Data is kept in named volumes, so it survives `docker compose down`; run `docker compose down -v` to reset everything. If you change `APP_PORT` or access it through another hostname, set `WEB_HOST`, `WEB_URL` and `AGENT_CANVAS_LOCK_TO_CLOUD` to match (see the comments in `docker-compose.yml`).

To test your local changes, build `app` and `worker` from your checkout instead of pulling the release image:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

Or put `COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml` in `.env` and run `docker compose up -d --build`.

To run other published versions, set any of these (shell or `.env`):

| Variable | Default | Example |
| --- | --- | --- |
| `APP_IMAGE` | `ghcr.io/openhands/enterprise-server:<release>` | `ghcr.io/openhands/enterprise-server:main` (latest `main`), `:pr-<n>`, `:sha-<sha>` |
| `AUTOMATION_IMAGE` | `ghcr.io/openhands/automation:<version>` | `ghcr.io/openhands/automation:main` |
| `AGENT_CANVAS_IMAGE` | `ghcr.io/openhands/agent-canvas:<version>` | `ghcr.io/openhands/agent-canvas:main` |

For moving tags such as `main`, add `--pull always` to `docker compose up`. After upgrading from an older checkout, run `docker compose down` first so the proxy picks up the new routing.

### Learn More

- **Documentation**: [https://docs.openhands.dev/enterprise](https://docs.openhands.dev/enterprise)
- **Website**: [https://www.openhands.dev/enterprise](https://www.openhands.dev/enterprise)

## Contributing

> [!IMPORTANT]
> Contributions from outside, non-OpenHands maintainers are currently not accepted.

If you are interested in collaborating with us or contributing to OpenHands Enterprise, please reach out to discuss a Contributor License Agreement (CLA). You can contact us through our [website](https://www.openhands.dev/contact) or [Slack community](https://go.openhands.dev/slack).

## Development

See the [Development guide](./Development.md) for setup instructions, the repository layout, and development workflows. Python dependencies are managed with [uv](https://docs.astral.sh/uv/).
