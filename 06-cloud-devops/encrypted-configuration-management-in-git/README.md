# secretsync

Encrypted configuration management in Git: commit secrets, not plaintext.

> A CLI and workflow that keeps per-environment secrets encrypted inside a Git repository, decrypts them only in CI, rotates keys with a grace period, and records every operation in a tamper-evident audit trail.

## Overview

An original, self-contained implementation of the **secrets-in-Git** workflow for the Foundev portfolio. `.sops.yaml`-style configuration maps each environment (`dev`, `staging`, `prod`) to its own age recipients, `secretsync` wraps Mozilla SOPS for the actual encryption, and every encrypt/decrypt/rotate/refusal is appended to a hash-chained audit log so history edits and tail truncation are detectable.

The point is the whole workflow, not just the crypto: the same commit holds ciphertext, config, the pre-commit gate, a CI example, and the audit trail that proves what happened to each file.

## Features

- Per-environment encryption: `secretsync encrypt <file> --env dev` re-keys plaintext to that environment's age recipients through SOPS.
- CI-gated decryption: `secretsync decrypt` refuses to run on a laptop and only proceeds under `CI=true` plus a run marker; `--allow-local` is the explicit escape hatch and is itself blocked when `decrypt_policy.enforce_local_deny` is on.
- Key rotation with grace: `secretsync rotate --env prod` re-encrypts to current recipients, keeps `previous_recipients` readable until `--drop-previous`, and tracks `rotation_reminder_days` / `last_rotated`.
- Hash-chained audit trail: append-only JSONL (`.secretsync/audit.log`) where each record hashes the previous one, plus a `.secretsync/audit.head` checkpoint that catches tail truncation. Denials are audited too.
- Pre-commit gate: `secretsync check` validates config, ciphertext coverage, tracked-file hygiene, the audit chain, and scans tracked files for secret-shaped plaintext.
- Plaintext guard: encryption is refused while other tracked files still carry secret keys in cleartext.
- Stdlib-first: no pip dependencies; `sops` and `age-keygen` are the only external binaries, with pure-Python shims as a documented fallback when they are absent.

## Stack

`Python`
The CSV stack is Python. sops 3.13.3 and age v1.3.2 are downloaded into `.tools/` (gitignored) by `scripts/fetch_tools.sh`, so encryption, decryption and rotation are exercised against the real binaries; the test suite automatically falls back to the bundled shims when neither binary is present. The package itself has no runtime dependency: `pip install -e ".[dev]"` adds only pytest. Ansible is optional and only needed for the example playbook, which CI now runs.

## Threat model & workflow

**Protects against**

- Secrets in Git history forever: plaintext never lands in a commit, and old ciphertext is only readable by whoever holds the environment's age identity.
- A stolen laptop or leaked clone: without `.secretsync/local-keys/*.key` (gitignored) or the CI key, ciphertext is opaque.
- Silent edits to encrypted files: the audit trail records a sha256 per ciphertext, and `check --check-files` flags content that changed without an event.
- Audit-history tampering: rewriting a record breaks its hash, forging a chain hash breaks the following link, and deleting tail records mismatches the checkpoint.
- Local curiosity: decrypt is denied outside CI by default, and each denial is itself an audited event.

**Does not protect against**

- A compromised CI runner holding the age identity: it can decrypt anything for its environment.
- A malicious maintainer who can rewrite both the ciphertext and the audit trail *and* re-sign the checkpoint — the trail is tamper-*evident*, not tamper-*proof*.
- Recipients shared across environments: separation is per-env by configuration, not by cryptography.
- Secret values inside a committed plaintext file that `check`'s pattern scan does not recognise.

**Workflow**

1. `secretsync init --envs dev,staging,prod` writes `.sops.yaml`, per-env age keys (gitignored), `.gitattributes`, the pre-commit hook and encrypted samples.
2. Author edits `environments/dev/app.yaml` locally, runs `encrypt --env dev`, deletes or keeps the plaintext untracked.
3. `git config core.hooksPath .githooks` makes `secretsync check` run on every commit.
4. CI checks out the repo, injects the age identity, and runs `secretsync decrypt --env prod` for the deploy step only.
5. Rotation: drop the old public key into `.sops.yaml`, run `rotate --env prod`, verify with `audit verify --check-files`, then `rotate --env prod --drop-previous` once the grace period ends.

## Run

```bash
python3 -m secretsync --help
python3 -m secretsync init --envs dev,staging,prod     # scaffold config, keys, samples
python3 -m secretsync encrypt environments/dev/secrets.enc.yaml --env dev
python3 -m secretsync decrypt environments/dev/secrets.enc.yaml --env dev            # refused locally
CI=true SECRETSYNC_RUN_ID=1234 GITHUB_ACTIONS=true \
  python3 -m secretsync decrypt environments/dev/secrets.enc.yaml --env dev          # allowed in CI
python3 -m secretsync rotate --env dev                 # re-key with previous_recipients grace
python3 -m secretsync audit                            # list the trail
python3 -m secretsync audit verify --check-files       # verify chain + ciphertext hashes
python3 -m secretsync check                            # the pre-commit gate
bash scripts/real_e2e.sh                               # full end-to-end run with the real binaries
```

Needs Python 3.10+, `sops`, and `age-keygen` (pointed at with `SECRETSYNC_SOPS` / `SECRETSYNC_AGE_KEYGEN`, or placed under `.tools/`). Ansible is optional and only used by the example playbook.

### Install

```bash
python -m pip install -e ".[dev]"     # the package and pytest
bash scripts/fetch_tools.sh            # the sops and age binaries into .tools/
pytest -q
```

`.tools/` holds the downloaded binaries (about 70 MB) and is gitignored: run `scripts/fetch_tools.sh` after a clone to get them back, or let CI do it. The test suite falls back to the bundled pure-Python shims when the binaries are missing.

## Structure

```text
encrypted-configuration-management-in-git/
├── secretsync/                 the CLI package (python3 -m secretsync)
│   ├── cli.py                  commands: init, encrypt, decrypt, rotate, audit, check
│   ├── config.py               .sops.yaml parsing, validation and in-place editors
│   ├── miniyaml.py             small dependency-free YAML subset reader/writer
│   ├── policy.py               CI-gate decision logic for decrypt
│   ├── audit.py                hash-chained JSONL trail + checkpoint verification
│   ├── sopsbin.py              sops/age binary discovery and subprocess wrappers
│   ├── plaintext.py            secret-shaped key scanning in tracked files
│   ├── gitutil.py              actor attribution and tracked-file queries
│   └── check.py                the pre-commit gate checks
├── tests/                      96 tests (unit + throwaway-git-repo integration)
├── environments/               per-env encrypted samples (dev, staging, prod)
├── ansible/                    example playbook, inventories, secretsync lookup plugin
├── .github/workflows/          the CI: the test suite and the Ansible playbook
├── .github/workflows-example/  CI example: gated decrypt during deploy
├── .githooks/pre-commit        runs `secretsync check`
├── scripts/fetch_tools.sh      downloads the sops and age binaries into .tools/
├── scripts/real_e2e.sh         end-to-end verification with the real binaries
├── .sops.yaml                  recipients, creation rules, files, decrypt policy
└── .secretsync/                audit.log, audit.head, local-keys/ (gitignored)
```

## Verification

All commands below were run in this repository:

```bash
python3 -m py_compile secretsync/*.py tests/*.py   # PY_COMPILE_OK (no output, exit 0)
pip install -e ".[dev]"                            # the editable install (the dev extra)
python3 -m pytest -q                               # 96 passed in 12.26s
bash scripts/real_e2e.sh                           # ALL REAL-BINARY CHECKS PASSED
python3 -m secretsync check                        # check: OK
python3 -m secretsync audit verify --check-files   # audit chain OK: 8 record(s), file hashes checked
ansible-playbook -i ansible/inventory/ci.yml ansible/playbook.yml   # 6 ok, 2 changed, 0 failed
```

The playbook run scaffolds a throwaway `dev` environment first (`secretsync init --envs dev --force`), because the ciphertexts that are committed were encrypted to recipients whose age identities are gitignored:

```bash
python3 -m secretsync init --envs dev --force
cp environments/dev/secrets.enc.yaml ansible/secrets/dev.secrets.enc.yaml
CI=true SECRETSYNC_RUN_ID=local \
SOPS_AGE_KEY_FILE=.secretsync/local-keys/dev.key \
ANSIBLE_LOOKUP_PLUGINS=ansible/lookup_plugins \
  ansible-playbook -i ansible/inventory/ci.yml ansible/playbook.yml
```

`scripts/real_e2e.sh` prints one `PASS` line per step: toolchain present (sops 3.13.3), init scaffolded config + encrypted sample, plaintext guard refuses committed cleartext, decrypt refused outside CI and allowed with `CI=true` + marker, rotate re-keyed dev with a verifying audit chain, check catches plaintext secrets, audit verify detects tampered history.

## Limitations

- The sample ciphertexts in `environments/` and `ansible/secrets/` were encrypted to build-time recipients whose age identities are gitignored, so this clone cannot decrypt them locally; run `secretsync init` in a fresh directory (or use the CI path) to get your own keys.
- The `ansible/` example runs against the runner itself in CI (`ansible/inventory/ci.yml`, local connection). It decrypts a throwaway environment, not a real deployment; the inventory of the example (`ansible/inventory/dev.yml`) still points at a host that only exists in a real one.
- The test suite falls back to pure-Python shims when `sops`/`age-keygen` are absent (`tests/test_shim_fallback.py` keeps that path covered); the real binaries are used automatically when present.
- The audit trail is tamper-evident, not tamper-proof: it detects rewriting, forging and truncation, but cannot prevent an attacker with write access to the whole repository from rebuilding a consistent trail.
- Only a YAML/JSON-shaped subset of `.sops.yaml` is parsed (`secretsync/miniyaml.py`); exotic YAML features are rejected rather than interpreted.
- One age identity per environment, held either locally (gitignored) or in CI; there is no KMS integration or per-file recipient override beyond `creation_rules`.

## License

MPL-2.0. See `LICENSE`.
