#!/usr/bin/env bash
# Collects the logs and state of the docker compose stack into one archive to send to OpenHands support.
#
# Usage: ./scripts/support-bundle.sh [docker compose options]
#   ./scripts/support-bundle.sh
#   ./scripts/support-bundle.sh -f docker-compose.yml -f docker-compose.https.yml
#
# The script reads .env from the repository root, like docker compose does.
# It writes openhands-support-<timestamp>.tar.gz to the current directory.
set -uo pipefail

out_dir=$(pwd)
cd "$(dirname "$0")/.." || exit 1

name="openhands-support-$(date -u +%Y%m%d-%H%M%S)"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
bundle="$work/$name"
mkdir -p "$bundle/logs" "$bundle/sandboxes" "$bundle/state"

compose() {
  docker compose "$@"
}

# Runs a command and writes its output and errors to a file. A failed command does not stop the script.
capture() {
  local file=$1
  shift
  "$@" >"$bundle/$file" 2>&1 || echo "(command failed with exit code $?)" >>"$bundle/$file"
}

if ! docker info >/dev/null 2>&1; then
  echo "Cannot reach Docker. Start Docker, or run this script as a user that can run docker commands." >&2
  exit 1
fi

echo "Collecting the stack state..."
capture state/compose-ps.txt compose "$@" ps --all
capture state/compose-images.txt compose "$@" images
capture state/docker-version.txt docker version
capture state/docker-info.txt docker info
capture state/docker-disk-usage.txt docker system df
capture state/git-commit.txt git log -1 --format='%H %d %cI'

# Values whose names contain KEY, SECRET, PASS, TOKEN or CREDENTIAL are replaced with ***,
# and so are the user name and password in URLs such as postgres://user:password@host.
compose "$@" config 2>&1 |
  awk '{
    if (match($0, /^[[:space:]]*[A-Za-z0-9_.-]+:/) && length($0) > RLENGTH &&
        tolower(substr($0, 1, RLENGTH)) ~ /(key|secret|pass|token|credential)/) {
      print substr($0, 1, RLENGTH) " \"***\""
      next
    }
    print
  }' |
  sed -E 's#://[^/@[:space:]]+:[^/@[:space:]]+@#://***:***@#g' \
    >"$bundle/state/compose-config.yml"

# The health check history and exit codes. The environment is left out, because it holds secrets.
for id in $(compose "$@" ps --all --quiet 2>/dev/null); do
  container=$(docker inspect --format '{{.Name}}' "$id" | sed 's#^/##')
  capture "state/$container.json" docker inspect --format '{{json .State}}' "$id"
done

echo "Collecting the service logs..."
services=$(compose "$@" config --services 2>/dev/null)
if [ -z "$services" ]; then
  echo "No compose services found. Run this script from the repository with the same options you use for docker compose up." >&2
fi
for service in $services; do
  capture "logs/$service.log" compose "$@" logs --no-color --timestamps --no-log-prefix "$service"
done

# The app starts sandbox containers outside of compose and labels them openhands.managed=true.
echo "Collecting the sandbox logs..."
for container in $(docker ps --all --filter label=openhands.managed=true --format '{{.Names}}'); do
  capture "sandboxes/$container.log" docker logs --timestamps "$container"
  capture "state/$container.json" docker inspect --format '{{json .State}}' "$container"
done

tar -czf "$out_dir/$name.tar.gz" -C "$work" "$name"

echo
echo "Created $out_dir/$name.tar.gz ($(du -h "$out_dir/$name.tar.gz" | awk '{print $1}'))."
