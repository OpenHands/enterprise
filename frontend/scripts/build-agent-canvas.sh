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
#   AGENT_CANVAS_REF        git tag/branch/commit to build (default: v1.21.0)
#   AGENT_CANVAS_REPO       repository URL
#   AGENT_CANVAS_CACHE_DIR  where to keep the checkout (default: frontend/.cache/agent-canvas)
set -euo pipefail

CANVAS_REPO="${AGENT_CANVAS_REPO:-https://github.com/OpenHands/OpenHands.git}"
CANVAS_REF="${AGENT_CANVAS_REF:-v1.21.0}"

FRONTEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="${AGENT_CANVAS_CACHE_DIR:-$FRONTEND_DIR/.cache/agent-canvas}"
OUT_DIR="$FRONTEND_DIR/public/canvas"

# Vite bakes this into the bundle, so the SPA knows it is mounted at /canvas
# instead of a dedicated hostname (matches the VITE_BASE_PATH the cloud image
# is built with).
CANVAS_BASE_PATH="${AGENT_CANVAS_BASE_PATH:-/canvas}"

echo "Building Agent Canvas ($CANVAS_REF) -> $OUT_DIR"

# The Canvas build is large and immutable per ref, so a full re-run is only
# needed when the ref changes. Reuse the checkout when the ref is unchanged.
if [ -d "$CACHE_DIR/.git" ] && [ "$(git -C "$CACHE_DIR" rev-parse -q --verify HEAD 2>/dev/null || true)" != "" ] \
  && [ "$(git -C "$CACHE_DIR" describe --tags --always 2>/dev/null || true)" = "$CANVAS_REF" ]; then
  echo "Reusing cached checkout at $CACHE_DIR"
else
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
(cd "$CACHE_DIR" && VITE_BASE_PATH="$CANVAS_BASE_PATH" npm run build)

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

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
cp -R "$CANVAS_BUILD_DIR"/. "$OUT_DIR"/

echo "Agent Canvas staged at public/canvas ($(du -sh "$OUT_DIR" | cut -f1))"
