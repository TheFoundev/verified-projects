#!/usr/bin/env bash
#
# 30-token-helper.sh - read and rotate the K3s node-token.
#
# The node-token lives on a server at:
#   /var/lib/rancher/k3s/server/node-token   (mode 0600, root only)
#
# Subcommands:
#   show    print the current node-token to stdout (requires sudo)
#   rotate  generate a new random token, write it, and restart k3s so it
#           takes effect. Existing agents keep working until they reconnect;
#           re-join them with 20-install-agent.sh and the new token.
#
# Usage:
#   sudo ./bootstrap/30-token-helper.sh show
#   sudo ./bootstrap/30-token-helper.sh show > ./secrets/node-token
#   sudo ./bootstrap/30-token-helper.sh rotate --yes
#
set -euo pipefail

TOKEN_PATH="/var/lib/rancher/k3s/server/node-token"

log()  { printf '[30-token-helper] %s\n' "$*"; }
die()  { printf '[30-token-helper] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: sudo ./bootstrap/30-token-helper.sh <show|rotate> [--yes]

  show            print the current node-token (stdout only, never logged)
  rotate --yes    generate a new token and restart k3s to apply it

Rotating invalidates the old token. Re-join every agent and additional
server with the new token afterwards.
EOF
}

cmd="${1:-}"
case "${cmd}" in
  ""|-h|--help) usage; exit 0 ;;
  show|rotate) shift ;;
  *) usage; die "unknown subcommand '${cmd}'" ;;
esac

[ "$(id -u)" -eq 0 ] || die "must run as root (use sudo)"

case "${cmd}" in
  show)
    [ -r "${TOKEN_PATH}" ] || die "${TOKEN_PATH} not readable; is this a server node?"
    # Print only the token, nothing else, so it can be redirected to a file.
    tr -d '\r\n' < "${TOKEN_PATH}"
    printf '\n'
    ;;
  rotate)
    CONFIRM=0
    for a in "$@"; do
      [ "${a}" = "--yes" ] && CONFIRM=1
    done
    if [ "${CONFIRM}" -ne 1 ]; then
      die "rotate is destructive; re-run with --yes to confirm"
    fi
    [ -r "${TOKEN_PATH}" ] || die "${TOKEN_PATH} not readable; is this a server node?"

    # Generate the new token the same shape K3s uses: K10 + 64 random chars.
    NEW_TOKEN="K10$(head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n')"

    BACKUP="${TOKEN_PATH}.bak.$(date +%Y%m%d%H%M%S)"
    install -m 0600 -o root -g root "${TOKEN_PATH}" "${BACKUP}"
    log "backed up old token to ${BACKUP} (mode 0600, root only)"

    printf '%s\n' "${NEW_TOKEN}" > "${TOKEN_PATH}.new"
    chmod 0600 "${TOKEN_PATH}.new"
    chown root:root "${TOKEN_PATH}.new"
    mv "${TOKEN_PATH}.new" "${TOKEN_PATH}"

    log "token rotated; restarting k3s"
    systemctl restart k3s.service
    log "restarted. New agents/servers must join with the new token:"
    log "  sudo ./bootstrap/30-token-helper.sh show > ./secrets/node-token"
    ;;
esac
