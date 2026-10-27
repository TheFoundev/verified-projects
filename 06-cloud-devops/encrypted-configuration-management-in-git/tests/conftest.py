"""Shared fixtures: real sops/age binaries when available, shims otherwise.

Tool resolution (session scoped):

1. ``SECRETSYNC_SOPS`` / ``SECRETSYNC_AGE_KEYGEN`` if the environment
   already provides them
2. ``sops`` / ``age-keygen`` on PATH
3. bundled binaries under ``.tools/`` (the real-binary build path)
4. otherwise: pure-Python shims implementing the minimal CLI contract
   written into a temp dir, so the suite runs with no binaries at all
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_SHIM_SOPS = r'''#!/usr/bin/env python3
"""Minimal sops shim for secretsync tests (encrypt/decrypt contract only)."""
import base64
import os
import sys


def fail(msg):
    sys.stderr.write(msg + "\n")
    sys.exit(1)


args = sys.argv[1:]
if "--version" in args:
    print("sops-shim 0.1.0 (test double)")
    sys.exit(0)
mode = "encrypt" if "--encrypt" in args else "decrypt" if "--decrypt" in args else None
if mode is None:
    fail("shim: expected --encrypt or --decrypt")

recipients = []
positional = []
i = 0
while i < len(args):
    a = args[i]
    if a == "--age":
        recipients = [r for r in args[i + 1].split(",") if r]
        i += 2
    elif a in ("--input-type", "--output-type"):
        i += 2
    elif a.startswith("-"):
        i += 1
    else:
        positional.append(a)
        i += 1
if not positional:
    fail("shim: no input file")
try:
    data = open(positional[-1], "rb").read()
except OSError as exc:
    fail("shim: cannot read input: %s" % exc)

if mode == "encrypt":
    if not recipients:
        fail("shim: --age is required")
    b64 = base64.b64encode(data).decode()
    lines = [
        "encrypted_blob: ENC[AES256_GCM,data:%s,iv:shim,tag:shim,type:str]" % b64,
        "sops:",
        "    age:",
    ]
    for rec in recipients:
        lines += [
            "        - enc: |",
            "            -----BEGIN AGE ENCRYPTED FILE-----",
            "            c2hpbS1lbmNyeXB0ZWQtZGF0YS1rZXk=",
            "            -----END AGE ENCRYPTED FILE-----",
            "          recipient: %s" % rec,
        ]
    lines += ['    lastmodified: "2026-01-01T00:00:00Z"', "    version: 3.0.0-shim"]
    sys.stdout.write("\n".join(lines) + "\n")
    sys.exit(0)

# decrypt
text = data.decode("utf-8", "replace")
recs = [ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.strip().startswith("recipient:")]
key_file = os.environ.get("SOPS_AGE_KEY_FILE")
identity = None
if key_file and os.path.exists(key_file):
    for ln in open(key_file, encoding="utf-8"):
        if ln.startswith("# public key:"):
            identity = ln.split(":", 2)[-1].strip()
            break
if identity is None:
    fail("shim: no age identities found (set SOPS_AGE_KEY_FILE)")
if recs and identity not in recs:
    fail("shim: no identity found for any of the configured recipients")
for ln in text.splitlines():
    marker = "encrypted_blob: ENC[AES256_GCM,data:"
    if ln.startswith(marker):
        payload = ln[len(marker):].split(",", 1)[0]
        sys.stdout.buffer.write(base64.b64decode(payload))
        sys.exit(0)
fail("shim: no encrypted payload found")
'''

_SHIM_KEYGEN = r'''#!/usr/bin/env python3
"""Minimal age-keygen shim: writes a fake identity and public key comment."""
import random
import sys

if "-o" not in sys.argv:
    sys.stderr.write("shim age-keygen: missing -o\n")
    sys.exit(2)
out = sys.argv[sys.argv.index("-o") + 1]
alphabet = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
rng = random.Random(out)
pub = "age1" + "".join(rng.choice(alphabet) for _ in range(58))
secret = "AGE-SECRET-KEY-1" + "".join(
    rng.choice("0123456789ABCDEFGHJKMNPQRSTVWXYZ") for _ in range(52)
)
try:
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("# created: 2026-01-01T00:00:00Z\n")
        fh.write("# public key: %s\n" % pub)
        fh.write(secret + "\n")
except OSError as exc:
    sys.stderr.write("shim age-keygen: %s\n" % exc)
    sys.exit(1)
sys.stderr.write("Public key: %s\n" % pub)
'''


def _candidate(*paths: Path) -> str | None:
    for path in paths:
        if path and Path(path).is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


class Tools:
    def __init__(self, kind: str, env: dict[str, str], keygen: str):
        self.kind = kind
        self.env = env
        self.keygen = keygen

    @property
    def is_real(self) -> bool:
        return self.kind == "real"

    def gen_key(self, dest: Path) -> str:
        """Generate an age identity at *dest*, returning the public key."""
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


@pytest.fixture(scope="session")
def tools(tmp_path_factory) -> Tools:
    sops = os.environ.get("SECRETSYNC_SOPS")
    keygen = os.environ.get("SECRETSYNC_AGE_KEYGEN")
    if not (sops and keygen):
        sops = sops or _candidate(
            shutil.which("sops"),
            PROJECT_ROOT / ".tools" / "sops",
        )
        keygen = keygen or _candidate(
            shutil.which("age-keygen"),
            PROJECT_ROOT / ".tools" / "age" / "age-keygen",
            PROJECT_ROOT / ".tools" / "age-keygen",
        )
    if sops and keygen:
        return Tools(
            "real",
            {"SECRETSYNC_SOPS": sops, "SECRETSYNC_AGE_KEYGEN": keygen},
            keygen,
        )
    shim_dir = tmp_path_factory.mktemp("shims")
    if os.name == "nt":
        # Windows CreateProcess cannot start an extension-less Python script.
        # Write the same shims as .py files and expose .cmd wrappers.
        sops_script = shim_dir / "sops_shim.py"
        keygen_script = shim_dir / "age_keygen_shim.py"
        sops_script.write_text(_SHIM_SOPS, encoding="utf-8")
        keygen_script.write_text(_SHIM_KEYGEN, encoding="utf-8")
        sops_path = shim_dir / "sops.cmd"
        keygen_path = shim_dir / "age-keygen.cmd"
        sops_path.write_text(
            f'@echo off\r\n"{sys.executable}" "{sops_script}" %*\r\n', encoding="utf-8"
        )
        keygen_path.write_text(
            f'@echo off\r\n"{sys.executable}" "{keygen_script}" %*\r\n', encoding="utf-8"
        )
    else:
        sops_path = shim_dir / "sops"
        keygen_path = shim_dir / "age-keygen"
        sops_path.write_text(_SHIM_SOPS, encoding="utf-8")
        keygen_path.write_text(_SHIM_KEYGEN, encoding="utf-8")
        sops_path.chmod(0o755)
        keygen_path.chmod(0o755)
    return Tools(
        "shim",
        {"SECRETSYNC_SOPS": str(sops_path), "SECRETSYNC_AGE_KEYGEN": str(keygen_path)},
        str(keygen_path),
    )


def base_env(tools: Tools, **overrides: str) -> dict[str, str]:
    """A hermetic CLI environment: PYTHONPATH points at the package, CI
    indicators are cleared, and the resolved tool shims are injected."""
    env = dict(os.environ)
    pythonpath = str(PROJECT_ROOT)
    if env.get("PYTHONPATH"):
        pythonpath = pythonpath + os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = pythonpath
    # The CLI prints typographic characters (em dashes); force UTF-8 so the
    # test-side decode() works on Windows, where the console default is cp1252.
    env["PYTHONIOENCODING"] = "utf-8"
    for var in (
        "CI",
        "SECRETSYNC_RUN_ID",
        "SECRETSYNC_ACTOR",
        "SOPS_AGE_KEY_FILE",
        "GITHUB_ACTIONS",
        "GITHUB_ACTOR",
        "GITHUB_RUN_ID",
        "GITLAB_CI",
        "JENKINS_URL",
        "TF_BUILD",
        "BUILD_BUILDID",
    ):
        env.pop(var, None)
    env.update(tools.env)
    env.update(overrides)
    return env


def run_cli(args: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "secretsync", *args],
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class Repo:
    """A throwaway git repository scaffolded with `secretsync init`."""

    def __init__(self, root: Path, env: dict[str, str], tools: Tools):
        self.root = root
        self.env = env
        self.tools = tools

    def cli(self, args: list[str], **environ: str) -> subprocess.CompletedProcess:
        env = dict(self.env)
        env.update(environ)
        return run_cli(args, env, self.root)

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def write(self, rel: str, content: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(self.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def config_path(self) -> Path:
        return self.root / ".sops.yaml"

    def key_path(self, env_name: str) -> Path:
        return self.root / ".secretsync" / "local-keys" / f"{env_name}.key"

    def audit_records(self) -> list[dict]:
        log = self.root / ".secretsync" / "audit.log"
        if not log.exists():
            return []
        return [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def ci_env(self, **extra: str) -> dict[str, str]:
        """Environment values for a simulated CI run."""
        values = {
            "CI": "true",
            "SECRETSYNC_RUN_ID": "run-12345",
            "GITHUB_ACTIONS": "true",
            "GITHUB_ACTOR": "foundev-bot",
            "GITHUB_RUN_ID": "12345",
        }
        values.update(extra)
        return values


@pytest.fixture
def repo(tmp_path, tools) -> Repo:
    root = tmp_path / "repo"
    root.mkdir()
    env = base_env(tools)
    git = run_cli_env_init(root, env)
    assert git.returncode == 0, git.stderr.decode()
    r = Repo(root, env, tools)
    r.git("init", "-q")
    r.git("config", "user.name", "Test User")
    r.git("config", "user.email", "test@example.invalid")
    # mirror the shipped .gitignore so `git add -A` behaves like the real repo
    (root / ".gitignore").write_text(
        "__pycache__/\n.pytest_cache/\n.tools/\n.secretsync/local-keys/\n"
        "*.dec.yaml\n*.dec.yml\n.env\n",
        encoding="utf-8",
    )
    result = r.cli(["init", "--envs", "dev,staging"])
    assert result.returncode == 0, result.stderr.decode()
    return r


def run_cli_env_init(root: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    """Preflight: the package itself must be importable before scaffolding."""
    return run_cli(["--version"], env, root)
