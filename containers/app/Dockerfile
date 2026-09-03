# Builds the enterprise-server image, the only image this repo publishes.
#
# The repository root is the deployable layout: `openhands/` (the app server)
# and the SaaS/enterprise modules that extend it (`server/`, `storage/`,
# `integrations/`, `sync/`, ...) sit side by side and are copied into /app
# as-is, so `uvicorn saas_server:app`, `python -m run_maintenance_tasks`,
# `python -m sync.<job>` and `alembic upgrade head` all run from /app.
# Python dependencies are installed with uv from the committed uv.lock.
ARG OPENHANDS_BUILD_VERSION=dev
FROM node:25.9-trixie-slim AS frontend-builder

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend ./
RUN npm run build

FROM python:3.13.7-slim-trixie AS base
FROM base AS backend-builder

WORKDIR /app

# Pin uv to the version that generated uv.lock (see "Lockfile Regeneration" in AGENTS.md).
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/tmp/uv_cache

RUN apt-get update -y \
    && apt-get install -y curl make git build-essential jq gettext

# Dependencies only. The project itself is never installed as a package: its
# source is copied into /app below and imported from there via PYTHONPATH=/app.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev && rm -rf $UV_CACHE_DIR

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

# System packages, Node.js v20+ and npm (which includes npx), and security
# updates for packages with available fixes.
RUN apt-get update -y \
    && apt-get install -y curl git ssh sudo jq gettext \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y nodejs \
    && apt-get upgrade -y \
        libc-bin \
        libc6 \
        libgnutls30 \
        libsqlite3-0 \
        perl-base \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

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

# Pin the system pip to a known-good version (reproducible builds) and fix
# CVE-2025-8869. Trivy scans it; the uv-managed venv ships no pip of its own.
ARG PIP_VERSION=26.0.1
USER root
RUN /usr/local/bin/python3 -m pip install --no-cache-dir "pip==${PIP_VERSION}" --break-system-packages
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
COPY --chown=openhands:openhands --chmod=770 saas_server.py run_maintenance_tasks.py run_budget_maintenance.py ./
COPY --chown=openhands:openhands pyproject.toml uv.lock alembic.ini README.md LICENSE ./

# Add this line to set group ownership of all files/directories not already in "app" group
# openhands:openhands -> openhands:openhands
RUN find /app \! -group openhands -exec chgrp openhands {} +

COPY --chown=openhands:openhands --chmod=770 --from=frontend-builder /app/build ./frontend/build
COPY --chown=openhands:openhands --chmod=770 ./containers/app/entrypoint.sh /app/entrypoint.sh

# Default the enterprise service-injector kinds in the image (overridable via env).
# Keeping these next to the classes they name means the chart never references an
# app-internal class path, so a chart/image version skew can't crashloop config load.
ENV OH_LLM_MODEL_KIND=server.verified_models.litellm_proxy_model_router.LiteLLMProxyModelServiceInjector
ENV OH_APP_CONVERSATION_INFO_KIND=server.utils.saas_app_conversation_info_injector.SaasAppConversationInfoServiceInjector

USER openhands

WORKDIR /app

ENTRYPOINT ["/app/entrypoint.sh"]
# Command will be overridden by Kubernetes deployment template
CMD ["uvicorn", "saas_server:app", "--host", "0.0.0.0", "--port", "3000"]
