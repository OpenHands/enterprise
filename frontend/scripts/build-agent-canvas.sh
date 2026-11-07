#!/usr/bin/env bash
#
# Build the Agent Canvas SPA (OpenHands/OpenHands) and stage it under
# `frontend/public/canvas` so it is served at `/canvas` next to this app.
#
# In cloud, `/canvas` is a separate service fronted by an ingress rule. For
# local development there is no ingress, so we bake the Canvas bundle into this
# app's SPA output instead: the app hard-redirects `/` to `/canvas`, and the
# Vite dev server (and the backend's static mount) serves the files from here.
#
# This is temporary scaffolding: when the OSS frontend is retired, `/canvas`
# becomes the only surface and this script goes away.
#
# Overrides:
#   AGENT_CANVAS_REF        git tag/branch/commit to build (default: v1.35.0)
#   AGENT_CANVAS_REPO       repository URL
#   AGENT_CANVAS_CACHE_DIR  where to keep the checkout (default: frontend/.cache/agent-canvas)
#   AGENT_CANVAS_SOURCE_DIR an already-fetched source tree to build as-is, skipping
#                           the clone (used by containers/app/Dockerfile)
#
# Every staged bundle carries agent-canvas-build.json (served at
# /canvas/agent-canvas-build.json) naming the ref and commit it was built from.
# See docs/agent-canvas-packaging.md for the install modes and which artifact
# each one serves.
set -euo pipefail

# Never prompt for credentials: without this, an unreachable/private repo makes
# `git clone` block on an interactive "Username for 'https://github.com'" prompt
# instead of failing, which would hang `make run`.
export GIT_TERMINAL_PROMPT=0

CANVAS_REPO="${AGENT_CANVAS_REPO:-https://github.com/OpenHands/OpenHands.git}"
CANVAS_REF="${AGENT_CANVAS_REF:-v1.35.0}"

FRONTEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="${AGENT_CANVAS_CACHE_DIR:-$FRONTEND_DIR/.cache/agent-canvas}"
OUT_DIR="$FRONTEND_DIR/public/canvas"
MANIFEST_NAME="agent-canvas-build.json"

# On failure, say what /canvas will actually serve: a bundle staged by an
# earlier successful run is left in place, so "no /canvas" would be untrue.
report_failure() {
  local status=$?
  [ "$status" -eq 0 ] && return
  if [ -f "$OUT_DIR/$MANIFEST_NAME" ]; then
    echo "error: Agent Canvas build of $CANVAS_REF failed; public/canvas still holds the previously staged bundle: $(tr -d '\n' < "$OUT_DIR/$MANIFEST_NAME")" >&2
  elif [ -f "$OUT_DIR/index.html" ]; then
    echo "error: Agent Canvas build of $CANVAS_REF failed; public/canvas still holds a previously staged bundle of unknown ref" >&2
  else
    echo "error: Agent Canvas build of $CANVAS_REF failed; no bundle is staged, so this app will not serve /canvas" >&2
  fi
}
trap report_failure EXIT

# Vite bakes this into the bundle, so the SPA knows it is mounted at /canvas
# instead of a dedicated hostname (matches the VITE_BASE_PATH the cloud image
# is built with).
CANVAS_BASE_PATH="${AGENT_CANVAS_BASE_PATH:-/canvas}"

# Lock the Canvas SPA to a specific cloud host so it skips first-run
# onboarding and uses cookie auth instead of session API keys.
# Set AGENT_CANVAS_LOCK_TO_CLOUD to the origin the browser will visit,
# including scheme and port, e.g. "http://localhost:3030".
# When set, VITE_LOCK_TO_CLOUD is passed to the Canvas build.
CANVAS_LOCK_TO_CLOUD="${AGENT_CANVAS_LOCK_TO_CLOUD:-}"

echo "Building Agent Canvas (${AGENT_CANVAS_SOURCE_DIR:+pre-fetched source at }${AGENT_CANVAS_SOURCE_DIR:-$CANVAS_REF}) -> $OUT_DIR"

# The Canvas build is large and immutable per ref, so a full re-run is only
# needed when the ref changes. Reuse the checkout when the ref is unchanged.
if [ -n "${AGENT_CANVAS_SOURCE_DIR:-}" ]; then
  CACHE_DIR="$AGENT_CANVAS_SOURCE_DIR"
  CANVAS_SOURCE="prefetched"
  echo "Using pre-fetched source at $CACHE_DIR"
elif [ -d "$CACHE_DIR/.git" ] && [ "$(git -C "$CACHE_DIR" rev-parse -q --verify HEAD 2>/dev/null || true)" != "" ] \
  && [ "$(git -C "$CACHE_DIR" describe --tags --always 2>/dev/null || true)" = "$CANVAS_REF" ]; then
  CANVAS_SOURCE="cached-clone"
  echo "Reusing cached checkout at $CACHE_DIR"
else
  CANVAS_SOURCE="clone"
  rm -rf "$CACHE_DIR"
  mkdir -p "$(dirname "$CACHE_DIR")"
  echo "Cloning $CANVAS_REPO at $CANVAS_REF"
  # Shallow clone; fall back to a full clone if the ref is not a branch/tag tip.
  if ! git clone --depth 1 --branch "$CANVAS_REF" "$CANVAS_REPO" "$CACHE_DIR" 2>/dev/null; then
    git clone "$CANVAS_REPO" "$CACHE_DIR"
    git -C "$CACHE_DIR" checkout "$CANVAS_REF"
  fi
fi

echo "Installing Agent Canvas dependencies"
# Canvas declares engines.node >= 24; it builds on Node 22 (which this repo
# pins), so engine mismatches are warnings rather than errors.
(cd "$CACHE_DIR" && npm ci --no-audit --no-fund)

echo "Compiling Agent Canvas with VITE_BASE_PATH=$CANVAS_BASE_PATH"

# Build the Canvas SPA. VITE_BASE_PATH is always set.
# VITE_LOCK_TO_CLOUD is set when AGENT_CANVAS_LOCK_TO_CLOUD is provided.
#
# VITE_BACKEND_BASE_URL and VITE_BACKEND_HOST are explicitly unset: if they
# are present in the shell environment (e.g. from frontend/.env), Vite bakes
# them into the bundle and Canvas uses that host instead of window.location.origin,
# which breaks cookie auth in locked-cloud mode.
BUILD_ENV="VITE_BASE_PATH=$CANVAS_BASE_PATH"
if [ -n "$CANVAS_LOCK_TO_CLOUD" ]; then
  BUILD_ENV="$BUILD_ENV VITE_LOCK_TO_CLOUD=$CANVAS_LOCK_TO_CLOUD"
  echo "  VITE_LOCK_TO_CLOUD=$CANVAS_LOCK_TO_CLOUD"
fi
(cd "$CACHE_DIR" && env -u VITE_BACKEND_BASE_URL -u VITE_BACKEND_HOST $BUILD_ENV npm run build)

if [ ! -f "$CACHE_DIR/build/index.html" ]; then
  echo "error: Agent Canvas build did not produce build/index.html" >&2
  exit 1
fi

# React Router SPA builds have unpacked build/client into build/ in every
# version seen so far, but prefer build/client if a future version leaves it
# nested.
CANVAS_BUILD_DIR="$CACHE_DIR/build"
if [ -f "$CACHE_DIR/build/client/index.html" ]; then
  CANVAS_BUILD_DIR="$CACHE_DIR/build/client"
fi

# A pre-fetched tree (BuildKit `ADD <repo>#<ref>`) has no .git, so its commit
# is unknown; the requested ref is still recorded.
CANVAS_COMMIT="$(git -C "$CACHE_DIR" rev-parse -q --verify HEAD 2>/dev/null || true)"
if [ -n "$CANVAS_COMMIT" ] && [ "$(git -C "$CACHE_DIR" rev-parse --show-toplevel 2>/dev/null)" != "$(cd "$CACHE_DIR" && pwd -P)" ]; then
  CANVAS_COMMIT=""
fi

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
cp -R "$CANVAS_BUILD_DIR"/. "$OUT_DIR"/

CANVAS_REPO="$CANVAS_REPO" CANVAS_REF="$CANVAS_REF" CANVAS_COMMIT="$CANVAS_COMMIT" \
  CANVAS_SOURCE="$CANVAS_SOURCE" CANVAS_BASE_PATH="$CANVAS_BASE_PATH" \
  CANVAS_LOCK_TO_CLOUD="$CANVAS_LOCK_TO_CLOUD" \
  node -e '
const env = process.env;
const manifest = {
  repository: env.CANVAS_REPO,
  ref: env.CANVAS_REF,
  commit: env.CANVAS_COMMIT || null,
  source: env.CANVAS_SOURCE,
  base_path: env.CANVAS_BASE_PATH,
  lock_to_cloud: env.CANVAS_LOCK_TO_CLOUD || null,
};
process.stdout.write(JSON.stringify(manifest) + "\n");
' > "$OUT_DIR/$MANIFEST_NAME"

echo "Agent Canvas staged at public/canvas ($(du -sh "$OUT_DIR" | cut -f1)): $(tr -d '\n' < "$OUT_DIR/$MANIFEST_NAME")"
