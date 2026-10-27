#!/usr/bin/env bash
# Real end-to-end verification of the secretsync workflow using the
# genuine sops + age binaries (downloaded into .tools/ during the build,
# or installed system-wide).
#
# Run:  bash scripts/real_e2e.sh
# Prints one PASS/FAIL line per step and exits non-zero on failure.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SOPS="${SECRETSYNC_SOPS:-$HERE/.tools/sops}"
AGEKEYGEN="${SECRETSYNC_AGE_KEYGEN:-$HERE/.tools/age/age-keygen}"

if [ ! -x "$SOPS" ] && ! command -v sops >/dev/null 2>&1; then
  echo "SKIP: no sops binary found (set SECRETSYNC_SOPS or install sops)"
  exit 0
fi
command -v sops >/dev/null 2>&1 && SOPS="$(command -v sops)"
[ -x "$SOPS" ] || SOPS="$HERE/.tools/sops"
if [ ! -x "$AGEKEYGEN" ]; then
  command -v age-keygen >/dev/null 2>&1 && AGEKEYGEN="$(command -v age-keygen)" || {
    echo "SKIP: no age-keygen binary found"; exit 0; }
fi

export SECRETSYNC_SOPS="$SOPS"
export SECRETSYNC_AGE_KEYGEN="$AGEKEYGEN"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; exit 1; }

# 1. tool versions -----------------------------------------------------------
"$SOPS" --version >/dev/null 2>&1 || fail "sops --version"
"$AGEKEYGEN" --version >/dev/null 2>&1 || fail "age-keygen --version"
pass "toolchain present ($( "$SOPS" --version 2>/dev/null | head -1 ))"

# 2. scaffold a throwaway repository ----------------------------------------
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
cd "$WORK"
git init -q
git config user.name "E2E Bot"
git config user.email "e2e@foundev.local"
printf '.secretsync/local-keys/\n*.dec.yaml\n.env\n' > .gitignore

python3 -m secretsync init --envs dev >/dev/null || fail "secretsync init"
[ -f .sops.yaml ] || fail "config written"
[ -f environments/dev/secrets.enc.yaml ] || fail "sample encrypted"
grep -q "ENC\[" environments/dev/secrets.enc.yaml || fail "ciphertext markers"
pass "init scaffolded config + encrypted sample"

# 3. plaintext guard ---------------------------------------------------------
printf 'password: should-not-be-tracked\n' > leftover.yaml
git add -A
printf 'token: new-secret\n' > environments/dev/app.yaml
if python3 -m secretsync encrypt environments/dev/app.yaml --env dev >/dev/null 2>&1; then
  fail "plaintext guard should refuse while leftover.yaml is tracked"
fi
git rm -q --cached leftover.yaml
python3 -m secretsync encrypt environments/dev/app.yaml --env dev >/dev/null || fail "encrypt"
[ -f environments/dev/app.enc.yaml ] || fail "encrypted output exists"
pass "plaintext guard refuses committed cleartext, encrypt works after cleanup"

# 4. CI-gated decrypt --------------------------------------------------------
if python3 -m secretsync decrypt environments/dev/secrets.enc.yaml --env dev >/dev/null 2>&1; then
  fail "decrypt must be refused outside CI"
fi
OUT="$(CI=true SECRETSYNC_RUN_ID=e2e GITHUB_ACTIONS=true GITHUB_ACTOR=e2e \
        GITHUB_RUN_ID=1 python3 -m secretsync decrypt \
        environments/dev/secrets.enc.yaml --env dev)" || fail "CI decrypt"
echo "$OUT" | grep -q "db_password: change-me-dev" || fail "plaintext round-trip"
pass "decrypt refused outside CI, allowed with CI=true + marker"

# 5. rotation with grace -----------------------------------------------------
OLD_REC="$(awk '/^# public key:/ {print $4}' .secretsync/local-keys/dev.key)"
NEW_REC="$("$AGEKEYGEN" -o "$WORK/new.key" 2>/dev/null >/dev/null; awk '/^# public key:/ {print $4}' "$WORK/new.key")"
python3 - "$OLD_REC" "$NEW_REC" <<'PY'
import pathlib, sys
old, new = sys.argv[1], sys.argv[2]
p = pathlib.Path(".sops.yaml")
text = p.read_text()
assert old in text, "dev recipient missing from config"
p.write_text(text.replace(old, new))
PY
python3 -m secretsync rotate --env dev >/dev/null || fail "rotate"
grep -q "recipient: $NEW_REC" environments/dev/secrets.enc.yaml || fail "new recipient"
python3 -m secretsync audit verify >/dev/null || fail "audit verify"
pass "rotate re-keyed dev, audit chain verifies"

# 6. check gate --------------------------------------------------------------
python3 -m secretsync check >/dev/null || fail "check on clean tree"
printf 'db_password: leaked\n' > bad.yaml
git add -f bad.yaml
if python3 -m secretsync check >/dev/null 2>&1; then
  fail "check must catch plaintext db_password"
fi
git rm -q --cached bad.yaml
python3 -m secretsync check >/dev/null || fail "check recovers"
pass "check catches plaintext secrets and passes on a clean tree"

# 7. tamper detection --------------------------------------------------------
python3 - <<'PY'
import json, pathlib
p = pathlib.Path(".secretsync/audit.log")
lines = p.read_text().splitlines()
rec = json.loads(lines[0]); rec["event"] = "forged"
lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
p.write_text("\n".join(lines) + "\n")
PY
if python3 -m secretsync audit verify >/dev/null 2>&1; then
  fail "audit verify must detect tampering"
fi
pass "audit verify detects tampered history"

echo "ALL REAL-BINARY CHECKS PASSED"
