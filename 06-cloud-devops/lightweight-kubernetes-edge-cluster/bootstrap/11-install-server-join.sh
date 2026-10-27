#!/usr/bin/env bash
#
# 11-install-server-join.sh - join an ADDITIONAL control-plane node.
#
# Run this on the 2nd and 3rd server nodes after 10-install-server.sh has
# bootstrapped the first one. Each join adds an etcd member, so the control
# plane tolerates the loss of one node once you reach three servers.
#
# QORUM GUIDANCE
#   1 server  -> no fault tolerance
#   2 servers -> no fault tolerance (etcd needs a majority; 2 is worse than 1
#               because a single loss blocks writes)
#   3 servers -> tolerates 1 failure   <-- do this
#   5 servers -> tolerates 2 failures  (rarely worth it on edge hardware)
# Always keep an odd number of servers.
#
# The join token is read from a file, never passed literally on the command
# line, so it does not leak into your shell history or the process table.
#
# Usage:
#   sudo ./bootstrap/11-install-server-join.sh --server https://FIRST_SERVER_IP:6443 \
#        --token-file ./secrets/node-token [--tls-san NAME] [--node-ip IP] [--dry-run]
#
# Generate the token file on an existing server with:
#   sudo ./bootstrap/30-token-helper.sh show > ./secrets/node-token
#
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"

SERVER_URL="${K3S_URL:-}"
TOKEN_FILE="${K3S_TOKEN_FILE:-}"
NODE_IP="${NODE_IP:-}"
K3S_VERSION="${K3S_VERSION:-}"
TLS_SANS=()
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --server) SERVER_URL="$2"; shift 2 ;;
    --server=*) SERVER_URL="${1#*=}"; shift ;;
    --token-file) TOKEN_FILE="$2"; shift 2 ;;
    --token-file=*) TOKEN_FILE="${1#*=}"; shift ;;
    --tls-san) TLS_SANS+=("$2"); shift 2 ;;
    --tls-san=*) TLS_SANS+=("${1#*=}"); shift ;;
    --node-ip) NODE_IP="$2"; shift 2 ;;
    --node-ip=*) NODE_IP="${1#*=}"; shift ;;
    --k3s-version) K3S_VERSION="$2"; shift 2 ;;
    --k3s-version=*) K3S_VERSION="${1#*=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --help|-h) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

log() { printf '[11-install-server-join] %s\n' "$*"; }
die() { printf '[11-install-server-join] ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (use sudo)"

[ -n "${SERVER_URL}" ] || die "--server https://FIRST_SERVER_IP:6443 is required"
[ -n "${TOKEN_FILE}" ] || die "--token-file is required (use 30-token-helper.sh show)"
[ -r "${TOKEN_FILE}" ] || die "token file ${TOKEN_FILE} is not readable"

TOKEN="$(tr -d '\r\n' < "${TOKEN_FILE}")"
[ -n "${TOKEN}" ] || die "token file ${TOKEN_FILE} is empty"

# Never echo the token: only its length, as a weak sanity check.
case "${TOKEN}" in
  K10*) log "token file looks like a K3s secure token (length ${#TOKEN})" ;;
  *) log "warning: token does not start with K10; double-check it is a node-token" ;;
esac

INSTALL_ARGS=(server --server "${SERVER_URL}")
if [ -n "${NODE_IP}" ]; then
  INSTALL_ARGS+=(--node-ip "${NODE_IP}")
fi
for san in "${TLS_SANS[@]:-}"; do
  [ -n "${san}" ] && INSTALL_ARGS+=(--tls-san "${san}")
done

INSTALL_ENV=(
  "INSTALL_K3S_EXEC=${INSTALL_ARGS[*]}"
  "K3S_URL=${SERVER_URL}"
  "K3S_TOKEN=${TOKEN}"
)
if [ -n "${K3S_VERSION}" ]; then
  INSTALL_ENV+=("INSTALL_K3S_VERSION=${K3S_VERSION}")
fi

CONFIG_FILE="${REPO_ROOT}/k3s/config.yaml"
if [ -f "${CONFIG_FILE}" ]; then
  INSTALL_ENV+=("K3S_CONFIG_FILE=${CONFIG_FILE}")
  log "using server config file ${CONFIG_FILE}"
fi

log "planned INSTALL_K3S_EXEC: ${INSTALL_ARGS[*]} (token redacted)"

if [ "${DRY_RUN}" -eq 1 ]; then
  log "dry-run: would join ${SERVER_URL} as an additional control-plane node"
  exit 0
fi

log "downloading and running the official K3s installer"
if ! curl -sfL https://get.k3s.io -o /tmp/k3s-install.sh; then
  die "could not download the K3s installer; check network access"
fi
chmod +x /tmp/k3s-install.sh

env "${INSTALL_ENV[@]}" sh /tmp/k3s-install.sh

systemctl enable --now k3s.service

log "waiting up to 120s for this node to register"
for _ in $(seq 1 60); do
  if [ -f /etc/rancher/k3s/k3s.yaml ]; then
    log "node joined; confirm quorum from any server with:"
    log "  sudo k3s kubectl get nodes -o wide"
    log "  sudo ./bootstrap/40-verify-cluster.sh --etcd"
    exit 0
  fi
  sleep 2
done

die "node did not finish joining within 120s; check: journalctl -u k3s -n 100"
