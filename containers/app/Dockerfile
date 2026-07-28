# Builds the enterprise-server image, the only image this repo publishes.
#
# The `enterprise` stage (bottom of this file) is the default build target and
# the one CI pushes. `openhands-app` is the plain app image the enterprise
# layers sit on; build it on its own with `--target openhands-app`.
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
ENV PYTHONPATH='/app'

# One venv for the whole repo — uv.lock covers the app and the enterprise server,
# so the enterprise stage below has nothing left to install.
#
# UV_PYTHON/UV_PYTHON_DOWNLOADS pin the venv to the base image's interpreter. A
# uv-managed CPython would live outside the venv and not survive the COPY into
# the next stage, leaving a venv whose interpreter symlink dangles.
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1

# Pin uv to the version used to generate uv.lock
ARG UV_VERSION=0.11.32
RUN apt-get update -y \
    && apt-get install -y curl make git build-essential jq gettext \
    && python3 -m pip install "uv==${UV_VERSION}" --break-system-packages

COPY pyproject.toml uv.lock ./
RUN touch README.md
# --no-install-project: the `openhands` package is copied onto PYTHONPATH below
# rather than installed, so the image serves the source tree it was built from.
RUN uv sync --frozen --no-default-groups --no-install-project

FROM base AS openhands-app

WORKDIR /app

# re-declare for this section
ARG OPENHANDS_BUILD_VERSION

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

RUN apt-get update -y \
    && apt-get install -y curl git ssh sudo \
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
# CVE-2025-8869. Trivy scans it even though nothing here uses it: uv installs
# into the venv directly, so the venv itself has no pip to pin.
# `/usr/local/bin/python3` is the system interpreter regardless of `PATH`.
ARG PIP_VERSION=26.0.1
USER root
RUN /usr/local/bin/python3 -m pip install --no-cache-dir "pip==${PIP_VERSION}" --break-system-packages
USER openhands

COPY --chown=openhands:openhands --chmod=770 ./skills ./skills
COPY --chown=openhands:openhands --chmod=770 ./openhands ./openhands
COPY --chown=openhands:openhands pyproject.toml uv.lock README.md LICENSE ./

# Add this line to set group ownership of all files/directories not already in "app" group
# openhands:openhands -> openhands:openhands
RUN find /app \! -group openhands -exec chgrp openhands {} +

COPY --chown=openhands:openhands --chmod=770 --from=frontend-builder /app/build ./frontend/build
COPY --chown=openhands:openhands --chmod=770 ./containers/app/entrypoint.sh /app/entrypoint.sh

USER root

WORKDIR /app

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "openhands.server.listen:app", "--host", "0.0.0.0", "--port", "3000"]

# ── enterprise-server ────────────────────────────────────────────────────────
# Enterprise layers on top of the app image. Keeping them as a stage here (rather
# than a separate Dockerfile FROM a published app image) means one build produces
# one image, so enterprise-server is the only image CI has to push.
FROM openhands-app AS enterprise

# Datadog labels
LABEL com.datadoghq.tags.service="deploy"
LABEL com.datadoghq.tags.env="${DD_ENV}"

# Install Node.js v20+ and npm (which includes npx)
# Apply security updates to fix CVEs
RUN apt-get update && \
    apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && \
    apt-get install -y nodejs && \
    apt-get install -y jq gettext && \
    # Apply security updates for packages with available fixes
    apt-get upgrade -y \
        libc-bin \
        libc6 \
        libgnutls30 \
        libsqlite3-0 \
        perl-base && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Enterprise dependencies are already in the venv (one uv.lock for the repo), so
# this only adds source. It lands flattened at /app — /app/server, /app/storage,
# etc. — which is why enterprise code imports them unprefixed.
COPY --chown=openhands:openhands --chmod=770 enterprise .

# Default the enterprise service-injector kinds in the image (overridable via env).
# Keeping these next to the classes they name means the chart never references an
# app-internal class path, so a chart/image version skew can't crashloop config load.
ENV OH_LLM_MODEL_KIND=server.verified_models.litellm_proxy_model_router.LiteLLMProxyModelServiceInjector
ENV OH_APP_CONVERSATION_INFO_KIND=server.utils.saas_app_conversation_info_injector.SaasAppConversationInfoServiceInjector

USER openhands

# Command will be overridden by Kubernetes deployment template
CMD ["uvicorn", "saas_server:app", "--host", "0.0.0.0", "--port", "3000"]
