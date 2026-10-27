#!/usr/bin/env bash
#
# 40-verify-cluster.sh - health checks for a running edge cluster.
#
# Runs on any node that has a kubeconfig (a server node by default). Uses
# `k3s kubectl` when plain kubectl is absent, so no extra tooling is needed.
#
# Usage:
#   sudo ./bootstrap/40-verify-cluster.sh            # nodes + provisioner
#   sudo ./bootstrap/40-verify-cluster.sh --etcd     # also check etcd members
#   ./bootstrap/40-verify-cluster.sh --drain <node>  # cordon + drain a node
#   ./bootstrap/40-verify-cluster.sh --uncordon <node>
#
set -euo pipefail

log() { printf '[40-verify-cluster] %s\n' "$*"; }
die() { printf '[40-verify-cluster] ERROR: %s\n' "$*" >&2; exit 1; }

KUBECTL="kubectl"
if ! command -v kubectl >/dev/null 2>&1; then
  if command -v k3s >/dev/null 2>&1; then
    KUBECTL="k3s kubectl"
  else
    die "neither kubectl nor k3s found; run this on a cluster node"
  fi
fi

kube() { ${KUBECTL} "$@"; }

MODE="verify"
TARGET_NODE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --etcd) MODE="verify-etcd"; shift ;;
    --drain) MODE="drain"; TARGET_NODE="${2:-}"; shift 2 ;;
    --drain=*) MODE="drain"; TARGET_NODE="${1#*=}"; shift ;;
    --uncordon) MODE="uncordon"; TARGET_NODE="${2:-}"; shift 2 ;;
    --uncordon=*) MODE="uncordon"; TARGET_NODE="${1#*=}"; shift ;;
    --help|-h) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

# ---------------------------------------------------------------------------
case "${MODE}" in
  drain)
    [ -n "${TARGET_NODE}" ] || die "--drain needs a node name"
    log "cordon + drain ${TARGET_NODE} (local-path volumes are node-bound; pods may reschedule elsewhere)"
    kube cordon "${TARGET_NODE}"
    kube drain "${TARGET_NODE}" --ignore-daemonsets --delete-emptydir-data --timeout=180s
    log "node ${TARGET_NODE} drained; reverse with --uncordon ${TARGET_NODE}"
    ;;
  uncordon)
    [ -n "${TARGET_NODE}" ] || die "--uncordon needs a node name"
    kube uncordon "${TARGET_NODE}"
    log "node ${TARGET_NODE} is schedulable again"
    ;;
  verify|verify-etcd)
    log "waiting for every node to become Ready (up to 180s)"
    if kube wait --for=condition=Ready nodes --all --timeout=180s; then
      log "all nodes Ready"
    else
      kube get nodes -o wide || true
      die "some nodes did not become Ready"
    fi

    log "node inventory:"
    kube get nodes -o custom-columns=\
'NODE:.metadata.name,STATUS:.status.conditions[-1].type,ROLE:.metadata.labels.node-role\.kubernetes\.io/control-plane,CLASS:.metadata.labels.node-class,VERSION:.status.nodeInfo.kubeletVersion' || true

    log "checking the local-path provisioner"
    if kube get storageclass local-path >/dev/null 2>&1; then
      kube get storageclass local-path -o wide || true
      if kube -n kube-system get deploy local-path-provisioner >/dev/null 2>&1; then
        kube -n kube-system rollout status deploy/local-path-provisioner --timeout=120s
      else
        log "warning: local-path-provisioner deployment not found in kube-system"
      fi
    else
      log "warning: StorageClass 'local-path' not found; local-path-provisioner may be disabled"
    fi

    log "control-plane pods:"
    kube -n kube-system get pods -o wide 2>/dev/null || true

    if [ "${MODE}" = "verify-etcd" ]; then
      log "checking etcd members (run from a server node)"
      for ep in 127.0.0.1:2379; do
        if command -v k3s >/dev/null 2>&1; then
          # shellcheck disable=SC2016
          k3s etcd-snapshot list 2>/dev/null | head -n 5 || log "warning: could not list etcd snapshots"
        fi
        log "checking endpoint ${ep} with etcdctl if available"
        if command -v etcdctl >/dev/null 2>&1; then
          etcdctl --endpoints="${ep}" endpoint health || true
          etcdctl --endpoints="${ep}" member list -w table || true
        else
          log "etcdctl not installed; rely on 'k3s kubectl get nodes' and the healthcheck timer"
        fi
      done
      log "etcd quorum reminder: an odd number of servers (3) tolerates 1 failure"
    fi

    log "verification complete"
    ;;
esac
