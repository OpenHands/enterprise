#!/usr/bin/env bash
# Ubuntu 24.04 host bootstrap; run as root on the dedicated Compose VM.
set -euo pipefail

if [[ -f /opt/openhands-compose/.gcp/host-bootstrap-complete ]] && \
  command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  systemctl enable --now docker
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl jq python3
install -m 0755 -d /etc/apt/keyrings
curl --fail --silent --show-error --location \
  https://download.docker.com/linux/ubuntu/gpg \
  --output /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc

. /etc/os-release
if [[ "${ID}" != ubuntu || "${VERSION_ID}" != 24.04 ]]; then
  echo 'This bootstrap requires Ubuntu 24.04.' >&2
  exit 1
fi

cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
install -m 0755 -d /etc/docker
if [[ ! -f /etc/docker/daemon.json ]]; then
  cat > /etc/docker/daemon.json <<'EOF'
{"log-driver":"local","log-opts":{"max-size":"20m","max-file":"5"}}
EOF
fi
systemctl enable --now docker
install -m 0700 -d /opt/openhands-compose /opt/openhands-compose/.gcp
docker version --format '{{.Server.Version}}'
docker compose version
touch /opt/openhands-compose/.gcp/host-bootstrap-complete
