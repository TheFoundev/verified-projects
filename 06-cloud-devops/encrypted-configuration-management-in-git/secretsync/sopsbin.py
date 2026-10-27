"""Locate and drive the real ``sops`` / ``age-keygen`` binaries.

Resolution order for each tool:

1. ``SECRETSYNC_SOPS`` / ``SECRETSYNC_AGE_KEYGEN`` environment variable
2. ``sops_bin`` in ``.sops.yaml``
3. the tool on ``PATH``
4. a bundled copy under ``.tools/`` in the project root

The environment variables are the seam the test suite uses to inject
shim binaries when the real ones are unavailable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

__all__ = ["ToolError", "find_tool", "sops_encrypt", "sops_decrypt", "age_keygen"]


class ToolError(RuntimeError):
    """Raised when a required external tool is missing or fails."""


def find_tool(kind: str, root: Path, config_sops_bin: str | None = None) -> str:
    """kind is ``sops`` or ``age-keygen``."""
    if kind == "sops":
        env_var, exe = "SECRETSYNC_SOPS", "sops"
    else:
        env_var, exe = "SECRETSYNC_AGE_KEYGEN", "age-keygen"
    preset = os.environ.get(env_var)
    if preset:
        if not Path(preset).expanduser().exists():
            raise ToolError(f"{env_var} points at {preset!r} which does not exist")
        return str(Path(preset).expanduser())
    if kind == "sops" and config_sops_bin:
        candidate = Path(config_sops_bin)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.exists():
            return str(candidate)
    on_path = shutil.which(exe)
    if on_path:
        return on_path
    candidates = [
        root / ".tools" / exe,
        root / ".tools" / exe / exe,
        root / ".tools" / "age" / exe,  # official age tarball layout
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise ToolError(
        f"{exe} not found: install it, put it on PATH, place it under {root / '.tools'}, "
        f"or set {env_var}"
    )


def _key_env(key_file: str | Path | None) -> dict:
    env = dict(os.environ)
    if key_file:
        env["SOPS_AGE_KEY_FILE"] = str(key_file)
    return env


def input_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in (".ini", ".env", ".properties"):
        return "ini"
    if suffix == ".toml":
        return "toml"
    return "yaml"


def _run(
    cmd: list[str],
    env: dict,
    stdin: bytes | None = None,
    what: str = "command",
    cwd: str | None = None,
) -> bytes:
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        raise ToolError(f"{what}: cannot execute {cmd[0]!r}: {exc}") from None
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        tail = "\n".join(stderr.splitlines()[-6:])
        raise ToolError(f"{what} failed (exit {proc.returncode}):\n{tail}")
    return proc.stdout


def _sops_file(
    binary: str,
    args: list[str],
    data: bytes,
    env: dict,
    what: str,
) -> bytes:
    """sops has no stdin alias, so stage the input as a temp file.

    The subprocess runs with its cwd *outside* the repository: sops
    discovers ``.sops.yaml`` from the cwd, and secretsync's config uses
    its own schema with repo-relative path rules that a staged temp file
    could never match.
    """
    suffix = ".yaml"
    if "--input-type" in args:
        itype = args[args.index("--input-type") + 1]
        suffix = {"json": ".json", "ini": ".ini", "toml": ".toml"}.get(itype, ".yaml")
    fd, tmp = tempfile.mkstemp(prefix="secretsync-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return _run(
            [binary, *args, tmp], env, None, what, cwd=os.path.dirname(tmp)
        )
    finally:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover - defensive
            pass


def sops_encrypt(
    root: Path,
    plaintext: bytes,
    recipients: list[str],
    sops_bin: str | None = None,
    input_type: str = "yaml",
) -> bytes:
    """Encrypt *plaintext* to *recipients*, returning the ciphertext document."""
    binary = sops_bin or find_tool("sops", root)
    args = [
        "--encrypt",
        "--age",
        ",".join(recipients),
        "--input-type",
        input_type,
        "--output-type",
        input_type,
    ]
    return _sops_file(binary, args, plaintext, dict(os.environ), "sops --encrypt")


def sops_decrypt(
    root: Path,
    ciphertext: bytes,
    key_file: str | Path | None = None,
    sops_bin: str | None = None,
    input_type: str = "yaml",
) -> bytes:
    """Decrypt a ciphertext document, returning the plaintext bytes."""
    binary = sops_bin or find_tool("sops", root)
    args = ["--decrypt", "--input-type", input_type, "--output-type", input_type]
    return _sops_file(binary, args, ciphertext, _key_env(key_file), "sops --decrypt")


def age_keygen(root: Path, out_path: Path) -> str:
    """Generate an age identity into *out_path* and return the public key."""
    binary = find_tool("age-keygen", root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [binary, "-o", str(out_path)],
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ToolError("age-keygen failed: " + (detail[-1] if detail else "unknown error"))
    for line in out_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# public key:"):
            return line.split(":", 2)[-1].strip()
    raise ToolError(f"age-keygen produced no public key in {out_path}")
