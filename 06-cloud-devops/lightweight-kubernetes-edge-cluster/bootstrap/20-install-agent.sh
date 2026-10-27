#!/usr/bin/env bash
#
# 20-install-agent.sh - join a worker (agent) node to the cluster.
#
# Agents run workloads but not the control plane, so they can be cheap
# single-board computers or small VMs. Each agent is labelled with its
# hardware class (node-class=pi|vm) so the Helm chart can place pods.
#
# The join token is read from a file, never passed literally on the command
# line, so it does not leak into shell history or the process table.
#
# Usage:
#   sudo ./bootstrap/20-install-agent.sh --server https://SERVER_IP:6443 \
#        --token-file ./secrets/node-token --node-class pi \
#        [--label key=value] [--node-ip IP] [--dry-run]
#
set -euo pipefail

SERVER_URL="${K3S_URL:-}"
TOKEN_FILE="${K3S_TOKEN_FILE:-}"
NODE_CLASS=""
NODE_IP="${NODE_IP:-}"
K3S_VERSION="${K3S_VERSION:-}"
EXTRA_LABELS=()
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --server) SERVER_URL="$2"; shift 2 ;;
    --server=*) SERVER_URL="${1#*=}"; shift ;;
    --token-file) TOKEN_FILE="$2"; shift 2 ;;
    --token-file=*) TOKEN_FILE="${1#*=}"; shift ;;
    --node-class) NODE_CLASS="$2"; shift 2 ;;
    --node-class=*) NODE_CLASS="${1#*=}"; shift ;;
    --node-ip) NODE_IP="$2"; shift 2 ;;
    --node-ip=*) NODE_IP="${1#*=}"; shift ;;
    --k3s-version) K3S_VERSION="$2"; shift 2 ;;
    --k3s-version=*) K3S_VERSION="${1#*=}"; shift ;;
    --label) EXTRA_LABELS+=("$2"); shift 2 ;;
    --label=*) EXTRA_LABELS+=("${1#*=}"); shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --help|-h) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

log() { printf '[20-install-agent] %s\n' "$*"; }
die() { printf '[20-install-agent] ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (use sudo)"
[ -n "${SERVER_URL}" ] || die "--server https://SERVER_IP:6443 is required"
[ -n "${TOKEN_FILE}" ] || die "--token-file is required (use 30-token-helper.sh show)"
[ -r "${TOKEN_FILE}" ] || die "token file ${TOKEN_FILE} is not readable"

case "${NODE_CLASS}" in
  pi|vm) ;;
  "") die "--node-class pi|vm is required so the scheduler can place pods" ;;
  *) die "--node-class must be 'pi' or 'vm' (got '${NODE_CLASS}')" ;;
esac

TOKEN="$(tr -d '\r\n' < "${TOKEN_FILE}")"
[ -n "${TOKEN}" ] || die "token file ${TOKEN_FILE} is empty"

LABEL_ARGS=("node-class=${NODE_CLASS}")
for lbl in "${EXTRA_LABELS[@]:-}"; do
  case "${lbl}" in
    *"="*) LABEL_ARGS+=("${lbl}") ;;
    *) die "--label expects key=value (got '${lbl}')" ;;
  esac
done

LOG_ENV=("K3S_URL=${SERVER_URL}")
LOG_ENV+=("K3S_TOKEN=<redacted>")
if [ -n "${NODE_IP}" ]; then
  LOG_ENV+=("K3S_NODE_IP=${NODE_IP}")
fi

INSTALL_ARGS=(agent)
if [ -n "${NODE_IP}" ]; then
  INSTALL_ARGS+=(--node-ip "${NODE_IP}")
fi
for lbl in "${LABEL_ARGS[@]}"; do
  INSTALL_ARGS+=(--node-label "${lbl}")
done

log "planned labels: ${LABEL_ARGS[*]}"

if [ "${DRY_RUN}" -eq 1 ]; then
  log "dry-run: would join ${SERVER_URL} as agent with node-class=${NODE_CLASS}"
  exit 0
fi

CONFIG_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/k3s/config-agent.yaml"

INSTALL_ENV=(
  "INSTALL_K3S_EXEC=${INSTALL_ARGS[*]}"
  "K3S_URL=${SERVER_URL}"
  "K3S_TOKEN=${TOKEN}"
)
if [ -n "${K3S_VERSION}" ]; then
  INSTALL_ENV+=("INSTALL_K3S_VERSION=${K3S_VERSION}")
fi
if [ "${NODE_IP}" ]; then
  INSTALL_ENV+=("K3S_NODE_IP=${NODE_IP}")
fi
if [ -f "${CONFIG_FILE}" ]; then
  INSTALL_ENV+=("K3S_CONFIG_FILE=${CONFIG_FILE}")
  log "using agent config file ${CONFIG_FILE}"
fi

log "downloading and running the official K3s installer"
if ! curl -sfL https://get.k3s.io -o /tmp/k3s-install-agent.sh; then
  die "could not download the K3s installer; check network access"
fi
chmod +x /tmp/k3s-install-agent.sh

env "${INSTALL_ENV[@]}" sh /tmp/k3s-install-agent.sh

systemctl enable --now k3s-agent.service

log "agent joined; confirm from a server with:"
log "  sudo k3s kubectl get nodes -l node-class=${NODE_CLASS} -o wide"
log "done"
