"""Portable fallback: the whole flow on pure-Python shim binaries.

The suite prefers real sops/age (``.tools/`` or PATH), but this test
forces ``SECRETSYNC_SOPS`` / ``SECRETSYNC_AGE_KEYGEN`` at freshly
written shims to prove the contract works with no external binaries —
the gate a fresh clone sees on a machine without sops installed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

from conftest import _SHIM_KEYGEN, _SHIM_SOPS, Repo, base_env, run_cli

RECIPIENT_RE = re.compile(r"recipient: (age1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{58})")


class ShimTools:
    """Just enough surface for the Repo helper (key generation)."""

    is_real = False

    def __init__(self, keygen: str):
        self.keygen = keygen
        self.env: dict[str, str] = {}

    def gen_key(self, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [self.keygen, "-o", str(dest)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr.decode()
        for line in dest.read_text(encoding="utf-8").splitlines():
            if line.startswith("# public key:"):
                return line.split(":", 2)[-1].strip()
        raise AssertionError("no public key in generated identity")


def test_shim_end_to_end(tmp_path, tools):
    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    sops = shim_dir / "sops"
    keygen = shim_dir / "age-keygen"
    if os.name == "nt":
        sops_script = shim_dir / "sops_shim.py"
        keygen_script = shim_dir / "age_keygen_shim.py"
        sops_script.write_text(_SHIM_SOPS, encoding="utf-8")
        keygen_script.write_text(_SHIM_KEYGEN, encoding="utf-8")
        sops = shim_dir / "sops.cmd"
        keygen = shim_dir / "age-keygen.cmd"
        sops.write_text(
            f'@echo off\r\n"{sys.executable}" "{sops_script}" %*\r\n', encoding="utf-8"
        )
        keygen.write_text(
            f'@echo off\r\n"{sys.executable}" "{keygen_script}" %*\r\n', encoding="utf-8"
        )
    else:
        sops.write_text(_SHIM_SOPS, encoding="utf-8")
        keygen.write_text(_SHIM_KEYGEN, encoding="utf-8")
        sops.chmod(0o755)
        keygen.chmod(0o755)

    env = base_env(
        tools, SECRETSYNC_SOPS=str(sops), SECRETSYNC_AGE_KEYGEN=str(keygen)
    )
    root = tmp_path / "shim-repo"
    root.mkdir()
    (root / ".gitignore").write_text(".secretsync/local-keys/\n*.dec.yaml\n.env\n")
    init = run_cli(["init", "--root", str(root), "--envs", "dev"], env, root)
    assert init.returncode == 0, init.stderr.decode()

    r = Repo(root, env, ShimTools(str(keygen)))
    r.git("init", "-q")
    r.git("config", "user.name", "Shim Tester")
    r.git("config", "user.email", "shim@example.invalid")

    # ciphertext produced by the shims
    sample = r.root / "environments" / "dev" / "secrets.enc.yaml"
    assert "ENC[" in sample.read_text(encoding="utf-8")
    assert "sops:" in sample.read_text(encoding="utf-8")

    # encrypt a new file through the CLI
    r.write("environments/dev/app.yaml", "db_password: shim-value\n")
    enc = r.cli(["encrypt", "environments/dev/app.yaml", "--env", "dev"])
    assert enc.returncode == 0, enc.stderr.decode()
    app_enc = "environments/dev/app.enc.yaml"

    # CI-gated decrypt works on shims
    dec = r.cli(
        ["decrypt", app_enc, "--env", "dev"],
        **r.ci_env(),
        SOPS_AGE_KEY_FILE=str(r.key_path("dev")),
    )
    assert dec.returncode == 0, dec.stderr.decode()
    assert b"db_password: shim-value" in dec.stdout

    # refusal still holds outside CI
    denied = r.cli(["decrypt", app_enc, "--env", "dev"])
    assert denied.returncode == 1

    # rotation with a fresh shim recipient keeps the grace recipient
    old = RECIPIENT_RE.findall(sample.read_text(encoding="utf-8"))[0]
    new = r.tools.gen_key(tmp_path / "shim-b.key")
    cfg = r.config_path()
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
    )
    rot = r.cli(["rotate", "--env", "dev"])
    assert rot.returncode == 0, rot.stderr.decode()
    after = RECIPIENT_RE.findall(sample.read_text(encoding="utf-8"))
    assert new in after and old in after, "grace recipient must survive on shims"

    # check + audit verify stay green on shims
    chk = r.cli(["check"])
    assert chk.returncode == 0, chk.stdout.decode()
    ver = r.cli(["audit", "verify", "--check-files"])
    assert ver.returncode == 0, ver.stderr.decode()
