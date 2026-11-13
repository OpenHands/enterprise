#!/usr/bin/env bash
# Usage: ./scripts/setup.sh [docker compose options]
# Example: ./scripts/setup.sh -f docker-compose.yml -f docker-compose.https.yml
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose is not available. Install Docker with the Compose plugin: https://docs.docker.com/compose/install/" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Cannot reach Docker. Start Docker, or run this script as a user that can run docker commands." >&2
  exit 1
fi

compose_cmd="docker compose"
for arg in "$@"; do
  compose_cmd+=" $(printf '%q' "$arg")"
done

echo "Starting OpenHands Enterprise with: $compose_cmd up --wait"
echo

docker compose "$@" up --wait
status=$?
if [ "$status" -ne 0 ]; then
  echo >&2
  echo "OpenHands Enterprise did not start (docker compose exited with code $status)." >&2
  echo "To see why, read the logs:   $compose_cmd logs" >&2
  echo "Which container failed:      $compose_cmd ps --all" >&2
  exit "$status"
fi

# The app's own idea of its public address already reflects APP_PORT, WEB_URL and .env.
url=$(docker compose "$@" config 2>/dev/null | awk '$1 == "OH_WEB_URL:" { gsub(/["'\'']/, "", $2); print $2; exit }')
url=${url:-http://localhost:9043}
url=${url%/}

echo
echo "OpenHands Enterprise is running."
echo
echo "  OpenHands:      $url"
echo "  Agent Canvas:   $url/canvas/"
echo
echo "  View the logs:  $compose_cmd logs -f"
echo "  One service:    $compose_cmd logs -f app"
echo "  Need support?   ./scripts/support-bundle.sh"
