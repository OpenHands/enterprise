# Builds the enterprise-server image, the only image this repo publishes.
#
# The repository root is the deployable layout: `openhands/` (the app server)
# and the SaaS/enterprise modules that extend it (`server/`, `storage/`,
# `integrations/`, `sync/`, ...) sit side by side and are copied into /app
# as-is, so `uvicorn saas_server:app`, `python -m run_maintenance_tasks`,
# `python -m sync.<job>` and `alembic upgrade head` all run from /app.
# Python dependencies are installed with uv from the committed uv.lock.
ARG OPENHANDS_BUILD_VERSION=dev
# Global so the `FROM` below can use it: `COPY --from` does not expand variables.
ARG AGENT_CANVAS_SOURCE=none

# Optional source for the Agent Canvas build below, selected by the
# AGENT_CANVAS_SOURCE build arg (`none` by default). With `git`, BuildKit
# fetches AGENT_CANVAS_REF (a tag or branch) itself and caches the layer on the
# resolved commit rather than on the ref's name. A branch such as `main` is
# therefore rebuilt exactly when it moves, which `git clone` inside a RUN would
# not do: that layer is reused for as long as the build args stay the same.
FROM scratch AS agent-canvas-source-none
FROM scratch AS agent-canvas-source-git
ARG AGENT_CANVAS_REPO=https://github.com/OpenHands/OpenHands.git
ARG AGENT_CANVAS_REF=main
ADD ${AGENT_CANVAS_REPO}#${AGENT_CANVAS_REF} /

FROM agent-canvas-source-${AGENT_CANVAS_SOURCE} AS agent-canvas-source

FROM node:26.11.1-trixie-slim AS frontend-builder

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend ./

# Agent Canvas (OpenHands/OpenHands) is NOT built here by default: building it
# clones a second repository over the network (scripts/build-agent-canvas.sh),
# which cloud doesn't need (it serves /canvas from a separate service behind
# an ingress rule) and which would add a network dependency to every image
# build otherwise. Opt in with `--build-arg AGENT_CANVAS=1` to bake /canvas
# into this image, served by the app.py/saas_server.py static mount at /canvas
# once frontend/build/canvas exists. docker-compose.yml does not: it runs the
# released agent-canvas image as its own service behind the proxy.
ARG AGENT_CANVAS=
# `none` (default): scripts/build-agent-canvas.sh clones its own pinned ref.
# `git`: build the source fetched by the agent-canvas-source-git stage above.
ARG AGENT_CANVAS_SOURCE
COPY --from=agent-canvas-source / /agent-canvas-source
# Same-origin URL (scheme + host + port) the browser will use to reach this
# app, e.g. http://localhost:9043. Passed through to
# scripts/build-agent-canvas.sh as AGENT_CANVAS_LOCK_TO_CLOUD, which bakes it
# into the Canvas bundle as VITE_LOCK_TO_CLOUD -- this is what lets Canvas
# skip its own onboarding and authenticate via this app's existing session
# cookie (POST /api/authenticate) instead of prompting to connect to
# OpenHands Cloud. Must match exactly what the compose stack's APP_PORT
# publishes, or cookie auth won't engage.
ARG AGENT_CANVAS_LOCK_TO_CLOUD=
# Fail-soft, same as the `build-agent-canvas` Makefile target: Canvas is
# scaffolding over a still-working OSS frontend, so a failed clone/build
# (e.g. no network) must not fail the whole image build.
RUN if [ -n "$AGENT_CANVAS" ]; then \
        (apt-get update -y \
            && apt-get install -y --no-install-recommends git ca-certificates \
            && rm -rf /var/lib/apt/lists/* \
            && AGENT_CANVAS_LOCK_TO_CLOUD="$AGENT_CANVAS_LOCK_TO_CLOUD" \
                AGENT_CANVAS_SOURCE_DIR="$([ "$AGENT_CANVAS_SOURCE" = none ] || echo /agent-canvas-source)" \
                npm run build:agent-canvas) \
        || echo "Agent Canvas build failed; continuing without /canvas."; \
    fi

RUN npm run build

FROM python:3.13.17-slim-trixie AS base
FROM base AS backend-builder

WORKDIR /app

# Pin uv to the version that generated uv.lock (see "Lockfile Regeneration" in AGENTS.md).
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/tmp/uv_cache

RUN apt-get update -y \
    && apt-get install -y curl make git build-essential jq gettext

# Install the dependencies. The source tree is copied into /app below and
# imported from there via PYTHONPATH=/app.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev && rm -rf $UV_CACHE_DIR

# ── enterprise-server ────────────────────────────────────────────────────────
FROM base AS enterprise-server

WORKDIR /app

# re-declare for this section
ARG OPENHANDS_BUILD_VERSION

# Datadog labels
LABEL com.datadoghq.tags.service="deploy"
LABEL com.datadoghq.tags.env="${DD_ENV}"

ENV RUN_AS_OPENHANDS=true
# A random number--we need this to be different from the user's UID on the host machine
ENV OPENHANDS_USER_ID=42420
ENV SANDBOX_LOCAL_RUNTIME_URL=http://host.docker.internal
ENV USE_HOST_NETWORK=false
ENV WORKSPACE_BASE=/opt/workspace_base
ENV OPENHANDS_BUILD_VERSION=$OPENHANDS_BUILD_VERSION
ENV SANDBOX_USER_ID=0
ENV FILE_STORE=local
ENV FILE_STORE_PATH=/.openhands
ENV INIT_GIT_IN_EMPTY_WORKSPACE=1
RUN mkdir -p $FILE_STORE_PATH
RUN mkdir -p $WORKSPACE_BASE

# System packages, Node.js (includes npm/npx), and security updates for all
# packages with available fixes. Applying an unfiltered `apt-get upgrade` (rather
# than a curated list) ensures Debian security advisories are picked up without
# the list going stale each time a new CVE lands -- notably the full perl stack
# pulled in transitively by `git`, plus libpcre2, libc, gzip, libssh2 and the
# CPython runtime libraries.
# CI reuses this layer from the registry build cache, so a Debian security update
# published after the layer was cached (e.g. openssl/libpcre2 3.5.7-1~deb13u3)
# is not picked up. BuildKit keys an ADD of a URL on the downloaded content, so
# this layer is rebuilt (and `apt-get upgrade` re-run) whenever Debian publishes
# a security update.
ADD http://deb.debian.org/debian-security/dists/trixie-security/InRelease /tmp/trixie-security-InRelease
RUN rm -f /tmp/trixie-security-InRelease \
    && apt-get update -y \
    && apt-get install -y curl git ssh sudo jq gettext \
    && curl -fsSL https://deb.nodesource.com/setup_26.x | bash - \
    && apt-get install -y nodejs \
    && apt-get upgrade -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# npm bundles its own undici and brace-expansion and no npm release (including
# 12.2.0) ships fixed versions yet, so swap them in place. Both are drop-in
# patch/minor releases with unchanged dependencies. Fixes CVE-2026-19534,
# CVE-2026-85024 (undici) and CVE-2026-102276, CVE-2026-102277, CVE-2026-102278
# (brace-expansion). Drop this once the installed npm bundles fixed versions.
ARG NPM_UNDICI_VERSION=6.28.1
ARG NPM_BRACE_EXPANSION_VERSION=5.0.12
RUN set -eux; \
    npm_modules="$(npm root -g)/npm/node_modules"; \
    packs="$(mktemp -d)"; \
    for spec in "undici@${NPM_UNDICI_VERSION}" "brace-expansion@${NPM_BRACE_EXPANSION_VERSION}"; do \
        name="${spec%@*}"; \
        tarball="$(npm pack --silent --pack-destination "$packs" "$spec")"; \
        rm -rf "${npm_modules:?}/${name}"; \
        mkdir "${npm_modules}/${name}"; \
        tar -xzf "${packs}/${tarball}" -C "${npm_modules}/${name}" --strip-components=1; \
    done; \
    rm -rf "$packs" /root/.npm

# Default is 1000, but OSX is often 501
RUN sed -i 's/^UID_MIN.*/UID_MIN 499/' /etc/login.defs
# Default is 60000, but we've seen up to 200000
RUN sed -i 's/^UID_MAX.*/UID_MAX 1000000/' /etc/login.defs

RUN groupadd --gid $OPENHANDS_USER_ID openhands
RUN useradd -l -m -u $OPENHANDS_USER_ID --gid $OPENHANDS_USER_ID -s /bin/bash openhands && \
    usermod -aG openhands openhands && \
    usermod -aG sudo openhands && \
    echo '%sudo ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
RUN chown -R openhands:openhands /app && chmod -R 770 /app
RUN sudo chown -R openhands:openhands $WORKSPACE_BASE && sudo chmod -R 770 $WORKSPACE_BASE
USER openhands

ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH='/app'

COPY --chown=openhands:openhands --chmod=770 --from=backend-builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}

# Remove the system pip. Nothing in the image uses it (the app runs from the uv
# managed /app/.venv), and the latest pip (26.2.1) still vendors msgpack, setuptools and
# urllib3 versions that Trivy flags (msgpack 1.1.2, setuptools 70.3.0,
# urllib3 2.7.0 in pip/_vendor/vendor.txt), so upgrading it does not help.
USER root
RUN /usr/local/bin/python3 -m pip uninstall -y pip --break-system-packages
USER openhands

COPY --chown=openhands:openhands --chmod=770 ./skills ./skills
COPY --chown=openhands:openhands --chmod=770 ./openhands ./openhands
COPY --chown=openhands:openhands --chmod=770 ./server ./server
COPY --chown=openhands:openhands --chmod=770 ./storage ./storage
COPY --chown=openhands:openhands --chmod=770 ./integrations ./integrations
COPY --chown=openhands:openhands --chmod=770 ./sync ./sync
COPY --chown=openhands:openhands --chmod=770 ./analytics ./analytics
COPY --chown=openhands:openhands --chmod=770 ./utils ./utils
COPY --chown=openhands:openhands --chmod=770 ./migrations ./migrations
COPY --chown=openhands:openhands --chmod=770 saas_server.py run_maintenance_tasks.py run_budget_maintenance.py run_budget_preflight.py ./
COPY --chown=openhands:openhands pyproject.toml uv.lock alembic.ini README.md LICENSE ./

# Add this line to set group ownership of all files/directories not already in "app" group
# openhands:openhands -> openhands:openhands
RUN find /app \! -group openhands -exec chgrp openhands {} +

COPY --chown=openhands:openhands --chmod=770 --from=frontend-builder /app/build ./frontend/build
COPY --chown=openhands:openhands --chmod=770 ./containers/app/entrypoint.sh /app/entrypoint.sh

# Default the enterprise service-injector kinds and service classes in the image (overridable via env).
# Keeping these next to the classes they name means the chart never references an
# app-internal class path, so a chart/image version skew can't crashloop config load.
ENV OH_LLM_MODEL_KIND=server.verified_models.litellm_proxy_model_router.LiteLLMProxyModelServiceInjector
ENV OH_APP_CONVERSATION_INFO_KIND=server.utils.saas_app_conversation_info_injector.SaasAppConversationInfoServiceInjector
ENV OPENHANDS_GITLAB_SERVICE_CLS=integrations.gitlab.gitlab_service.SaaSGitLabService

USER openhands

WORKDIR /app

ENTRYPOINT ["/app/entrypoint.sh"]
# Command will be overridden by Kubernetes deployment template
CMD ["uvicorn", "saas_server:app", "--host", "0.0.0.0", "--port", "3000"]
