#!/usr/bin/env bash
#
# 00-preflight.sh - sanity checks before installing K3s on an edge node.
#
# Idempotent and read-only: this script only inspects the host, it never
# mutates it. Run it on every node (server and agent) before the install
# scripts. Exit code 0 means "safe to proceed", non-zero means "fix the
# reported problem first".
#
# Usage:
#   sudo ./bootstrap/00-preflight.sh [--role server|agent]
#
# Environment (optional):
#   EXPECTED_UNIQUE_HOSTNAMES  comma separated list of all hostnames in the
#                              cluster; used to detect duplicate hostnames.
#
set -euo pipefail

ROLE="agent"
for arg in "$@"; do
  case "${arg}" in
    --role=*) ROLE="${arg#*=}" ;;
    --role) shift; ROLE="${1:-agent}" ;;
    --help|-h)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
  esac
done

FAIL=0
WARN=0

ok()   { printf '  [ ok ] %s\n' "$*"; }
warn() { printf '  [warn] %s\n' "$*"; WARN=$((WARN + 1)); }
fail() { printf '  [FAIL] %s\n' "$*"; FAIL=$((FAIL + 1)); }

section() { printf '\n== %s ==\n' "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
section "Host identity"
ARCH="$(uname -m)"
HOST="$(hostname -s 2>/dev/null || hostname)"
printf '  arch=%s hostname=%s role=%s\n' "${ARCH}" "${HOST}" "${ROLE}"

case "${ARCH}" in
  x86_64|aarch64|arm64|armv7l)
    ok "architecture ${ARCH} is supported by K3s"
    ;;
  *)
    fail "architecture ${ARCH} is not a supported K3s target (want x86_64/aarch64/armv7l)"
    ;;
esac

# ---------------------------------------------------------------------------
section "Memory"
if [ -r /proc/meminfo ]; then
  MEM_MB="$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)"
  printf '  MemTotal=%sMB\n' "${MEM_MB}"
  if [ "${MEM_MB}" -lt 1024 ]; then
    fail "less than 1GiB of RAM detected; K3s plus etcd will be unstable"
  elif [ "${MEM_MB}" -lt 2048 ]; then
    if [ "${ROLE}" = "server" ]; then
      fail "server nodes should have >=2GiB of RAM for embedded etcd (found ${MEM_MB}MB)"
    else
      warn "agent node has ${MEM_MB}MB; fine for agents, tight for stateful pods"
    fi
  else
    ok "memory ${MEM_MB}MB is sufficient"
  fi
else
  warn "/proc/meminfo not readable; skipping memory check"
fi

# ---------------------------------------------------------------------------
section "cgroup v2"
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
  ok "cgroup v2 (unified hierarchy) detected"
elif [ -d /sys/fs/cgroup ]; then
  warn "cgroup v1 detected; K3s works but cgroup v2 is recommended"
else
  fail "/sys/fs/cgroup missing; a cgroup hierarchy is required"
fi

if have systemctl; then
  if systemctl is-system-running >/dev/null 2>&1 || systemctl --version >/dev/null 2>&1; then
    ok "systemd is available (K3s installs as a systemd service)"
  else
    fail "systemd not responding; K3s install script requires systemd"
  fi
else
  fail "systemd (systemctl) not found; use the manual K3s binary install instead"
fi

# ---------------------------------------------------------------------------
section "Swap"
SWAP_LINES="$(awk '/^[^#].*[[:space:]]swap[[:space:]]/ {n++} END {print n+0}' /proc/swaps 2>/dev/null || echo 0)"
SWAP_FSTAB="$(awk '$3 == "swap" && $1 !~ /^#/ {n++} END {print n+0}' /etc/fstab 2>/dev/null || echo 0)"
if [ "${SWAP_LINES}" -eq 0 ] && [ "${SWAP_FSTAB}" -eq 0 ]; then
  ok "swap is disabled (kubelet requirement)"
else
  warn "swap appears enabled; disable with: swapoff -a && sed -i '/ swap / s/^/#/' /etc/fstab"
fi

# ---------------------------------------------------------------------------
section "Kernel modules and sysctls"
for mod in br_netfilter overlay; do
  if [ -d "/sys/module/${mod}" ] || modprobe -n "${mod}" >/dev/null 2>&1; then
    ok "kernel module ${mod} available"
  else
    warn "kernel module ${mod} not available; containerd networking may fail"
  fi
done

for key in "net.ipv4.ip_forward:1" "net.bridge.bridge-nf-call-iptables:1"; do
  name="${key%%:*}"
  want="${key##*:}"
  got="$(sysctl -n "${name}" 2>/dev/null || echo missing)"
  if [ "${got}" = "${want}" ]; then
    ok "sysctl ${name}=${got}"
  else
    warn "sysctl ${name}=${got} (want ${want}); see docs/TROUBLESHOOTING.md"
  fi
done

# ---------------------------------------------------------------------------
section "Firewall ports"
check_port() {
  local port="$1" proto="$2" label="$3"
  if have ss; then
    if ss -H -lntu 2>/dev/null | awk -v p="${port}" '$5 ~ (":" p "$") {found=1} END {exit !found}'; then
      warn "${label}: something is already listening on ${proto}/${port}"
    else
      ok "${label}: ${proto}/${port} is free"
    fi
  elif have netstat; then
    if netstat -lntu 2>/dev/null | awk -v p="${port}" '$4 ~ (":" p "$") {found=1} END {exit !found}'; then
      warn "${label}: something is already listening on ${proto}/${port}"
    else
      ok "${label}: ${proto}/${port} is free"
    fi
  else
    warn "neither ss nor netstat present; cannot check ${label} (${proto}/${port})"
  fi
}

check_port 6443 tcp "Kubernetes API"
check_port 2379 tcp "etcd client"
check_port 2380 tcp "etcd peer"
check_port 10250 tcp "kubelet"
check_port 8472 udp "flannel VXLAN"

if have firewall-cmd && firewall-cmd --state >/dev/null 2>&1; then
  warn "firewalld is active; open 6443/tcp, 2379-2380/tcp, 8472/udp, 10250/tcp between nodes"
elif have ufw && ufw status 2>/dev/null | grep -q "Status: active"; then
  warn "ufw is active; allow the same ports listed above between nodes"
else
  ok "no active host firewall detected (or none installed)"
fi

# ---------------------------------------------------------------------------
section "Hostname uniqueness"
if [ -n "${EXPECTED_UNIQUE_HOSTNAMES:-}" ]; then
  COUNT_TOTAL="$(printf '%s' "${EXPECTED_UNIQUE_HOSTNAMES}" | tr ',' '\n' | sed '/^$/d' | wc -l)"
  COUNT_UNIQ="$(printf '%s' "${EXPECTED_UNIQUE_HOSTNAMES}" | tr ',' '\n' | sed '/^$/d' | sort -u | wc -l)"
  if [ "${COUNT_TOTAL}" -eq "${COUNT_UNIQ}" ]; then
    ok "hostname list has no duplicates (${COUNT_UNIQ} nodes)"
  else
    fail "duplicate hostnames in EXPECTED_UNIQUE_HOSTNAMES: $(printf '%s' "${EXPECTED_UNIQUE_HOSTNAMES}" | tr ',' '\n' | sed '/^$/d' | sort | uniq -d | tr '\n' ' ')"
  fi
  case ",$(printf '%s' "${EXPECTED_UNIQUE_HOSTNAMES}" | tr -d ' ')," in
    *",${HOST},"*) ok "this host (${HOST}) is in the expected node list" ;;
    *) warn "this host (${HOST}) is not in EXPECTED_UNIQUE_HOSTNAMES" ;;
  esac
else
  warn "EXPECTED_UNIQUE_HOSTNAMES not set; duplicate hostnames break cluster membership"
fi

# ---------------------------------------------------------------------------
section "Container runtime tooling"
if have docker; then
  if docker info >/dev/null 2>&1; then
    ok "docker present and reachable (K3s bundles containerd and ignores it)"
  else
    warn "docker binary present but daemon not reachable (K3s bundles containerd anyway)"
  fi
else
  ok "docker not installed; K3s ships its own containerd (fine on edge nodes)"
fi

# ---------------------------------------------------------------------------
section "Disk space"
for path in / /var/lib; do
  if [ -d "${path}" ]; then
    AVAIL_GB="$(df -Pk "${path}" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1024/1024}')"
    printf '  %s free=%sGB\n' "${path}" "${AVAIL_GB:-?}"
    if [ -n "${AVAIL_GB}" ] && [ "${AVAIL_GB}" -lt 5 ]; then
      fail "less than 5GB free on ${path}; etcd snapshots and images need headroom"
    else
      ok "disk space on ${path} looks sufficient"
    fi
  fi
done

# ---------------------------------------------------------------------------
printf '\n== Summary ==\n'
printf '  failures: %d\n' "${FAIL}"
printf '  warnings: %d\n' "${WARN}"
if [ "${FAIL}" -gt 0 ]; then
  printf '  RESULT: FAIL - fix the failures above before installing K3s\n'
  exit 1
fi
printf '  RESULT: PASS - this host is ready for the install scripts\n'
exit 0
