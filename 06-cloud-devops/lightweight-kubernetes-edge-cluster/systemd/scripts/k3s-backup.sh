#!/usr/bin/env bash
#
# k3s-backup.sh - installed to /usr/local/bin/k3s-backup.sh and run by
# systemd/k3s-backup.timer every 6 hours.
#
# On a server node it asks K3s to write an etcd snapshot, then prunes old
# snapshots down to SNAPSHOT_RETAIN. On an agent (no embedded etcd) it exits
# cleanly without doing anything.
#
# This is a *local* snapshot on the same host. For real durability, sync the
# snapshot directory to another machine or object storage; see docs/RUNBOOK.md.
#
set -euo pipefail

SNAPSHOT_DIR="${SNAPSHOT_DIR:-/var/lib/rancher/k3s/server/db/snapshots}"
SNAPSHOT_RETAIN="${SNAPSHOT_RETAIN:-5}"

log() { printf '[k3s-backup] %s\n' "$*"; }

if ! command -v k3s >/dev/null 2>&1; then
  log "k3s binary not found; nothing to do on this node"
  exit 0
fi

if [ ! -d /var/lib/rancher/k3s/server ]; then
  log "this is not a server node (no server dir); skipping etcd snapshot"
  exit 0
fi

mkdir -p "${SNAPSHOT_DIR}"

log "requesting an etcd snapshot"
# The built-in snapshot command is safe to run on a live cluster.
k3s etcd-snapshot save \
  --snapshot-dir "${SNAPSHOT_DIR}" \
  --name "auto-$(date +%Y%m%d-%H%M%S)"

log "pruning to the newest ${SNAPSHOT_RETAIN} snapshots"
# `ls -1t` already sorts newest first; everything after the retain count goes.
TO_DELETE="$(ls -1t "${SNAPSHOT_DIR}"/auto-* 2>/dev/null | tail -n +"$((SNAPSHOT_RETAIN + 1))" || true)"
if [ -n "${TO_DELETE}" ]; then
  printf '%s\n' "${TO_DELETE}" | while IFS= read -r old; do
    [ -n "${old}" ] || continue
    log "removing old snapshot ${old}"
    rm -f -- "${old}"
  done
else
  log "no snapshots to prune"
fi

log "snapshot run complete; files in ${SNAPSHOT_DIR}:"
ls -1 "${SNAPSHOT_DIR}" 2>/dev/null || true
