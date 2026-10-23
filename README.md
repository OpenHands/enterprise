<a name="readme-top"></a>

<div align="center">
  <img src="https://assets.openhands.dev/logo-whitebackground.png" alt="OpenHands logo" width="340">
  <p align="center">
    <strong>OpenHands Enterprise — Backend Codebase, Arr!</strong>
  </p>
  <p align="center">
    The source-available backend fer OpenHands Enterprise, a self-hosted developer control helm fer yer coding agents and automations, matey.
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

## Ahoy, About OpenHands Enterprise

OpenHands Enterprise brings the power o' autonomous coding agents t' yer crew, with the governance, security, and compliance yer enterprise demands, savvy?

OpenHands Enterprise lets ye run AI coding agents right on yer own servers or in yer private cloud, far from prying eyes.

**This be the source-available, commercially licensed backend codebase fer OpenHands Enterprise**. It provides a self-hosted developer control helm fer coding agents and automations, with extra enterprise plunder includin' authentication, billin', and integrations.

### License (Read the Pirate Code!)

> [!WARNING]
> OpenHands Enterprise be commercially licensed under the [Polyform Free Trial License](./LICENSE). This be **not** an open source license, ye scallywag. Usage be limited t' 30 days per calendar year without a commercial license.

Fer commercial licensing and pricin' inquiries, send word and [contact us](https://www.openhands.dev/contact).

### What Treasure OpenHands Enterprise Offers

- Multi-use RBAC — Command multiple crew members from a single ship
- Platform integrations — Summon OpenHands straight from Slack, Jira, GitHub, GitLab, or Bitbucket
- Scalability — Sail unlimited parallel agent conversations without local resource constraints
- Enterprise security — SAML authentication, RBAC, and centralized agent traces, locked tight as a treasure chest
- Usage Monitoring — Track and enforce budgets; keep a weather eye on usage across all hands

> [Choosin' between OpenHands Agent Canvas and OpenHands Enterprise](https://docs.openhands.dev/enterprise/enterprise-vs-oss)

### Free 30 Day Trial Voyage

The preferred way o' installin' OpenHands Enterprise be via our Replicated installer.
- First, read the [Quick Start guide](https://docs.openhands.dev/enterprise/quick-start) t' understand the system requirements fer OpenHands Enterprise
- Then, [register fer a Trial account](https://install.r9.all-hands.dev/openhands/signup) via the Replicated installer portal.

### Run Locally with Docker Compose (Hoist the Colors!)

Fer a local evaluation or development, `docker-compose.yml` launches the whole fleet (app, worker, automation service, Agent Canvas, PostgreSQL and a reverse proxy) from the released images. Docker with the Compose plugin be the only thing ye need.

```bash
docker compose up -d
```

Then open <http://localhost:3100/oauth/idp/signup> t' create the admin account, and add an LLM under **Settings**. Agent Canvas be served at <http://localhost:3100/canvas/>. Data be kept in named volumes, so it survives `docker compose down`; run `docker compose down -v` t' scuttle everything and start anew. If ye change `APP_PORT` or access it through another hostname, set `WEB_HOST`, `WEB_URL` and `AGENT_CANVAS_LOCK_TO_CLOUD` t' match (see the comments in `docker-compose.yml`).

T' test yer local changes, build `app` and `worker` from yer checkout instead o' pullin' the release image:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

Or stow `COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml` in `.env` and run `docker compose up -d --build`.

T' sail other published versions, set any o' these (shell or `.env`):

| Variable | Default | Example |
| --- | --- | --- |
| `APP_IMAGE` | `ghcr.io/openhands/enterprise-server:<release>` | `ghcr.io/openhands/enterprise-server:main` (latest `main`), `:pr-<n>`, `:sha-<sha>` |
| `AUTOMATION_IMAGE` | `ghcr.io/openhands/automation:<version>` | `ghcr.io/openhands/automation:main` |
| `AGENT_CANVAS_IMAGE` | `ghcr.io/openhands/agent-canvas:<version>` | `ghcr.io/openhands/agent-canvas:main` |

Fer movin' tags such as `main`, add `--pull always` t' `docker compose up`. After upgradin' from an older checkout, run `docker compose down` first so the proxy picks up the new routin', or ye'll be sailin' by an old map.

### Learn More, Me Hearties

- **Documentation** (the ship's log): [https://docs.openhands.dev/enterprise](https://docs.openhands.dev/enterprise)
- **Website** (the crow's nest): [https://www.openhands.dev/enterprise](https://www.openhands.dev/enterprise)

## Contributing (Joinin' the Crew)

> [!IMPORTANT]
> Contributions from outside, non-OpenHands maintainers be currently not accepted. No stowaways!

If ye be interested in collaboratin' with us or contributin' t' OpenHands Enterprise, send a message in a bottle t' discuss a Contributor License Agreement (CLA). Ye can contact us through our [website](https://www.openhands.dev/contact) or [Slack community](https://go.openhands.dev/slack).

## Development (Buildin' the Ship)

See the [Development guide](./Development.md) fer setup instructions, the repository layout, and development workflows. Python dependencies be managed with [uv](https://docs.astral.sh/uv/).
