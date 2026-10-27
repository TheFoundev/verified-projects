#!/usr/bin/env bash
#
# 99-uninstall.sh - remove K3s from a node.
#
# Destructive: by default it prints what it would do and requires --yes to
# actually remove anything. It uses the official uninstall scripts that the
# K3s installer drops on disk (k3s-uninstall.sh / k3s-agent-uninstall.sh).
#
# Usage:
#   sudo ./bootstrap/99-uninstall.sh --role server --yes
#   sudo ./bootstrap/99-uninstall.sh --role agent  --yes
#
# WARNING: uninstalling a SERVER node removes its etcd member. If this is the
# last server, or if too many servers are removed at once, the cluster loses
# quorum and data is unrecoverable. Take an etcd snapshot first
# (systemctl start k3s-backup.service) and confirm quorum afterwards.
#
set -euo pipefail

log() { printf '[99-uninstall] %s\n' "$*"; }
die() { printf '[99-uninstall] ERROR: %s\n' "$*" >&2; exit 1; }

ROLE=""
CONFIRM=0

while [ $# -gt 0 ]; do
  case "$1" in
    --role) ROLE="${2:-}"; shift 2 ;;
    --role=*) ROLE="${1#*=}"; shift ;;
    --yes|-y) CONFIRM=1; shift ;;
    --help|-h) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ "$(id -u)" -eq 0 ] || die "must run as root (use sudo)"
case "${ROLE}" in
  server|agent) ;;
  "") die "--role server|agent is required" ;;
  *) die "--role must be 'server' or 'agent' (got '${ROLE}')" ;;
esac

if [ "${ROLE}" = "server" ]; then
  UNINSTALL_SH="/usr/local/bin/k3s-uninstall.sh"
  UNIT="k3s.service"
  DATA_DIR="/var/lib/rancher/k3s"
else
  UNINSTALL_SH="/usr/local/bin/k3s-agent-uninstall.sh"
  UNIT="k3s-agent.service"
  DATA_DIR="/var/lib/rancher/k3s/agent"
fi

log "role=${ROLE}"
log "unit to stop/disable:  ${UNIT}"
log "uninstall script:      ${UNINSTALL_SH}"
log "data directory removed: ${DATA_DIR}"

if [ ! -x "${UNINSTALL_SH}" ]; then
  die "${UNINSTALL_SH} not found; was K3s installed by the official installer?"
fi

if [ "${CONFIRM}" -ne 1 ]; then
  log "dry-run: re-run with --yes to actually uninstall (nothing was changed)"
  exit 0
fi

log "stopping and disabling ${UNIT}"
systemctl disable --now "${UNIT}" || true

log "running ${UNINSTALL_SH} (this deletes ${DATA_DIR}, including etcd data on servers)"
# The upstream script is designed to be run once as root and removes the
# service, binaries and data dir itself.
sh "${UNINSTALL_SH}"

log "k3s removed from this node."
log "if this was a server, re-check quorum from a surviving server:"
log "  sudo ./bootstrap/40-verify-cluster.sh --etcd"
