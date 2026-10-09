#!/usr/bin/env bash
# Usually run as the command shown in JobWatch (Servers & agents), which serves this script with the
# defaults below filled in:
#   curl -fsSL https://<ingest>/agent/install.sh | sudo bash -s -- --token <bootstrap>
# From a release download instead: pass --server, and set CS_DOWNLOAD_BASE / CS_AGENT_VERSION.
set -euo pipefail
# --- defaults: JobWatch rewrites these three lines when it serves this script (R62) ---
DEFAULT_SERVER=""
DEFAULT_VERSION="0.1.0"
DEFAULT_BASE="https://downloads.example.com/agent"
# --- end defaults ---
SERVER="${CS_SERVER:-$DEFAULT_SERVER}"; TOKEN=""; VERSION="${CS_AGENT_VERSION:-$DEFAULT_VERSION}"; BASE="${CS_DOWNLOAD_BASE:-$DEFAULT_BASE}"
while [[ $# -gt 0 ]]; do case "$1" in --server) SERVER="$2"; shift 2;; --token) TOKEN="$2"; shift 2;; *) shift;; esac; done
[[ -z "$SERVER" || -z "$TOKEN" ]] && { echo "usage: install.sh --token TOKEN [--server https://ingest.example.com]"; exit 1; }
[[ $EUID -ne 0 ]] && { echo "run as root"; exit 1; }
[[ "$SERVER" == https://* ]] || { echo "--server must be https://"; exit 1; }
ARCH=$(uname -m); case "$ARCH" in x86_64) ARCH=amd64;; aarch64) ARCH=arm64;; *) echo "unsupported arch $ARCH"; exit 1;; esac

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
BIN="cronsentinel-agent-linux-$ARCH"
curl -fsSL --proto '=https' "$BASE/$VERSION/$BIN" -o "$TMP/$BIN"
curl -fsSL --proto '=https' "$BASE/$VERSION/cronsentinel-agent.service" -o "$TMP/cronsentinel-agent.service"
curl -fsSL --proto '=https' "$BASE/$VERSION/SHA256SUMS" -o "$TMP/SHA256SUMS"
# R39: verify before anything is installed or run as root. A tampered or truncated download fails here.
( cd "$TMP" && grep -E " ($BIN|cronsentinel-agent.service)\$" SHA256SUMS | sha256sum -c --strict - ) \
  || { echo "checksum verification failed — not installing"; exit 1; }

install -m 0755 "$TMP/$BIN" /usr/local/bin/cronsentinel-agent
ln -sf /usr/local/bin/cronsentinel-agent /usr/local/bin/cs-run   # `cs-run` alias → `cronsentinel-agent exec`
install -d -m 0750 /var/lib/cronsentinel /etc/cronsentinel
# Spool for wrapped jobs that do not run as root: anyone may create a file, nobody but root may list
# or read other users' files (sticky + no read bit for others). The daemon imports and deletes them.
install -d -m 1733 /var/lib/cronsentinel/spool
/usr/local/bin/cronsentinel-agent enroll --server "$SERVER" --token "$TOKEN"
install -m 0644 "$TMP/cronsentinel-agent.service" /etc/systemd/system/cronsentinel-agent.service
systemctl daemon-reload && systemctl enable --now cronsentinel-agent
echo "JobWatch agent installed. Wrap jobs with: cs-run -- /path/to/script.sh"
