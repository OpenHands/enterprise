#!/usr/bin/env bash
# Runs the Bats shell tests with pinned bats-core, bats-support and bats-assert, fetched once into .cache/.
#
# Usage: tests/shell/run.sh [bats options] [test files]
#   tests/shell/run.sh                                     # every suite
#   tests/shell/run.sh tests/shell/setup_sh.bats           # wrapper suite, no Docker needed
#   tests/shell/run.sh tests/shell/setup_sh_compose.bats   # needs the docker CLI and Compose, not a daemon
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
cache=$(cd "$here/../.." && pwd)/.cache/bats

# fetch NAME SHA: checks out bats-core/NAME at exactly SHA into $cache/NAME-SHA.
fetch() {
  local name=$1 sha=$2 dest=$cache/$1-$2 tmp
  [ -e "$dest/.fetched" ] && return 0
  echo "Fetching $name $sha" >&2
  tmp=$(mktemp -d)
  git -C "$tmp" init -q
  git -C "$tmp" fetch -q --depth 1 "https://github.com/bats-core/$name.git" "$sha"
  git -C "$tmp" checkout -q FETCH_HEAD
  if [ "$(git -C "$tmp" rev-parse HEAD)" != "$sha" ]; then
    echo "$name checkout does not match $sha" >&2
    rm -rf "$tmp"
    exit 1
  fi
  touch "$tmp/.fetched"
  mkdir -p "$cache"
  rm -rf "$dest"
  mv "$tmp" "$dest"
}

BATS_CORE_SHA=eb7f42f8d608ac693d7a4b67474f6714ea68cfc5    # v1.14.0
BATS_SUPPORT_SHA=24a72e14349690bcbf7c151b9d2d1cdd32d36eb1 # v0.3.0
BATS_ASSERT_SHA=f1e9280eaae8f86cbe278a687e6ba755bc802c1a  # v2.2.4
fetch bats-core "$BATS_CORE_SHA"
fetch bats-support "$BATS_SUPPORT_SHA"
fetch bats-assert "$BATS_ASSERT_SHA"

# bats_load_library looks for <dir>/<name>/load.bash.
lib=$cache/lib
mkdir -p "$lib"
ln -sfn "$cache/bats-support-$BATS_SUPPORT_SHA" "$lib/bats-support"
ln -sfn "$cache/bats-assert-$BATS_ASSERT_SHA" "$lib/bats-assert"
export BATS_LIB_PATH=$lib

if [ "$#" -eq 0 ]; then
  set -- "$here"
fi
exec "$cache/bats-core-$BATS_CORE_SHA/bin/bats" "$@"
