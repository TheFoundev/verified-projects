#!/usr/bin/env bash
# Download the sops and age binaries into .tools/.
#
# The binaries are gitignored: they are large (about 70 MB together) and
# they are not part of the source. Run this script once after a clone, or
# let CI do it, before running the test suite with the real toolchain.
#
#   bash scripts/fetch_tools.sh
#
# The versions can be overridden with SOPS_VERSION and AGE_VERSION.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$HERE/.tools"
SOPS_VERSION="${SOPS_VERSION:-3.13.3}"
AGE_VERSION="${AGE_VERSION:-v1.3.2}"

mkdir -p "$TOOLS"

if [ -x "$TOOLS/sops" ] && "$TOOLS/sops" --version 2>/dev/null | grep -q "$SOPS_VERSION"; then
  echo "sops $SOPS_VERSION already present in .tools/"
else
  echo "downloading sops $SOPS_VERSION ..."
  curl -sSL -o "$TOOLS/sops" \
    "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.amd64"
  chmod +x "$TOOLS/sops"
fi

if [ -x "$TOOLS/age/age-keygen" ]; then
  echo "age $AGE_VERSION already present in .tools/"
else
  echo "downloading age $AGE_VERSION ..."
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  curl -sSL -o "$tmp/age.tar.gz" \
    "https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION#v}-linux-amd64.tar.gz"
  tar -xzf "$tmp/age.tar.gz" -C "$tmp"
  mkdir -p "$TOOLS/age"
  cp "$tmp"/age/* "$TOOLS/age/"
  chmod +x "$TOOLS/age/age-keygen" "$TOOLS/age/age" "$TOOLS/age/age-inspect"
fi

"$TOOLS/sops" --version
"$TOOLS/age/age-keygen" --version
echo "the toolchain is ready in .tools/"
