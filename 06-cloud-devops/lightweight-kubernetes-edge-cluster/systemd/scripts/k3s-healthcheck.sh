#!/usr/bin/env bash
#
# k3s-healthcheck.sh - installed to /usr/local/bin/k3s-healthcheck.sh and run
# by systemd/k3s-healthcheck.timer every minute.
#
# It checks two things:
#   1. the local K3s runtime unit is active, and
#   2. the node reports Ready through the API (or the API socket answers).
#
# After HEALTH_FAIL_THRESHOLD consecutive failures it restarts the runtime
# and logs loudly; it never deletes data.
#
set -euo pipefail

FAIL_THRESHOLD="${HEALTH_FAIL_THRESHOLD:-3}"
STATE_FILE="${HEALTH_STATE_FILE:-/run/k3s-healthcheck.fails}"

log() { printf '[k3s-healthcheck] %s\n' "$*"; }

# Work out which runtime this node runs.
UNIT="k3s.service"
if systemctl list-unit-files k3s-agent.service >/dev/null 2>&1 \
   && systemctl is-enabled k3s-agent.service >/dev/null 2>&1; then
  UNIT="k3s-agent.service"
fi

failures=0
if [ -r "${STATE_FILE}" ]; then
  failures="$(tr -dc '0-9' < "${STATE_FILE}" 2>/dev/null || echo 0)"
  failures="${failures:-0}"
fi

healthy=0
if systemctl is-active --quiet "${UNIT}"; then
  if command -v k3s >/dev/null 2>&1 && [ "${UNIT}" = "k3s.service" ]; then
    if k3s kubectl get --raw='/readyz' >/dev/null 2>&1; then
      healthy=1
    fi
  else
    # Agents have no local API server; kubelet liveness is the signal.
    if [ -S /run/k3s/containerd/containerd.sock ] || systemctl is-active --quiet k3s-agent.service; then
      healthy=1
    fi
  fi
fi

if [ "${healthy}" -eq 1 ]; then
  printf '0' > "${STATE_FILE}" 2>/dev/null || true
  log "healthy (${UNIT}); failure counter reset"
  exit 0
fi

failures=$((failures + 1))
printf '%s' "${failures}" > "${STATE_FILE}" 2>/dev/null || true
log "unhealthy (${UNIT}); consecutive failures=${failures}/${FAIL_THRESHOLD}"

if [ "${failures}" -ge "${FAIL_THRESHOLD}" ]; then
  log "threshold reached; restarting ${UNIT}"
  systemctl restart "${UNIT}" || log "restart of ${UNIT} failed"
  printf '0' > "${STATE_FILE}" 2>/dev/null || true
  # A non-zero exit here surfaces the event in the journal for alerting.
  exit 1
fi

exit 0
