"""The `check` gate: what a pre-commit hook runs.

Failures (exit 1):

* invalid configuration
* a configured secrets file that is missing or not encrypted
* an encrypted file in the tree that no creation rule manages
* tracked ``*.dec.*`` files or tracked key material
* plaintext secret-shaped keys in tracked structured files
* a tampered / truncated audit trail

Warnings (reported, never fatal):

* environments whose last rotation is older than their reminder age
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import gitutil, plaintext
from .audit import AuditLog, sha256_file
from .config import CONFIG_NAME, Config, ConfigError, load_config

__all__ = ["Report", "run_check", "is_encrypted", "age_key_contents"]

_SECRET_CONTENT_MARKERS = ("AGE-SECRET-KEY-", "-----BEGIN AGE PRIVATE KEY-----")
_KEY_NAME_PATTERNS = (".agekey",)
# file types where embedded private-key content is worth scanning; source
# and documentation files may legitimately mention the marker strings
_KEY_CONTENT_SUFFIXES = set(plaintext.TEXT_SUFFIXES) | {
    ".txt",
    ".key",
    ".pem",
    ".agekey",
    ".secret",
    "",
}


@dataclass
class Report:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def is_encrypted(path: Path) -> bool:
    """True when a file carries sops ciphertext markers."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "ENC[" in text and re.search(r"(?m)^sops:\s*$", text) is not None


def age_key_contents(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(marker in text for marker in _SECRET_CONTENT_MARKERS)


def run_check(config: Config | None = None, root: Path | None = None) -> Report:
    errors: list[str] = []
    warnings: list[str] = []

    if config is None:
        try:
            config = load_config(root) if root else _load_from_cwd()
        except ConfigError as exc:
            return Report(errors=[f"config: {exc}"], warnings=[])
    base = config.root

    tracked = gitutil.tracked_files(base)
    in_git = tracked is not None
    files = tracked if tracked is not None else gitutil.walk_files(base)

    # 1. configured files exist and are encrypted --------------------------
    for rel in config.files:
        target = base / rel
        if not target.is_file():
            errors.append(f"missing configured secrets file: {rel}")
            continue
        if not is_encrypted(target):
            errors.append(f"configured secrets file is NOT encrypted: {rel}")
        if config.resolve_env(rel) is None:
            errors.append(f"configured file does not match any creation rule: {rel}")

    # 2. every *.enc.* file in the tree is managed -------------------------
    for rel in files:
        name = Path(rel).name
        if ".enc." in name and rel not in config.files:
            if config.resolve_env(rel) is None:
                errors.append(
                    f"encrypted file is not covered by any creation rule: {rel}"
                )
            else:
                errors.append(f"encrypted file not listed in `files`: {rel}")

    # 3. no decrypted copies or key material are tracked -------------------
    if in_git:
        for rel in files:
            name = Path(rel).name
            if ".dec." in name:
                errors.append(f"decrypted secret must never be tracked: {rel}")
            if rel.startswith(config.key_dir.rstrip("/") + "/"):
                errors.append(f"age key material is tracked: {rel}")
                continue
            suffix = Path(name).suffix.lower()
            if suffix in {".key", ".pem", ".p12", ".pfx"} or name.endswith(_KEY_NAME_PATTERNS):
                errors.append(f"key-like file is tracked: {rel}")
            elif suffix in _KEY_CONTENT_SUFFIXES and age_key_contents(base / rel):
                errors.append(f"file contains age private key material: {rel}")
    else:
        warnings.append("not a git repository: tracked-file checks fell back to a filesystem walk")

    # 4. plaintext secret-shaped keys in tracked structured files ----------
    findings = plaintext.scan_paths(base, files, config.plaintext_patterns, config.scan_exclude)
    for finding in findings:
        errors.append(f"plaintext secret in tracked file: {finding}")

    # 5. audit trail -------------------------------------------------------
    log = AuditLog(base / config.audit_log)
    if log.path.exists():
        for violation in log.verify(check_files=True, root=base):
            errors.append(f"audit: {violation}")

    # 6. rotation reminders ------------------------------------------------
    import datetime as _dt

    today = _dt.date.today()
    for env, keys in config.keys.items():
        if keys.last_rotated is None:
            warnings.append(
                f"{env}: never rotated (set keys.{env}.last_rotated or run `rotate`)"
            )
            continue
        try:
            last = _dt.date.fromisoformat(keys.last_rotated)
        except ValueError:
            errors.append(f"{env}: keys.{env}.last_rotated is not an ISO date: {keys.last_rotated!r}")
            continue
        age_days = (today - last).days
        if age_days > keys.rotation_reminder_days:
            warnings.append(
                f"{env}: last rotation {age_days} days ago exceeds the "
                f"{keys.rotation_reminder_days}-day reminder"
            )

    return Report(errors=errors, warnings=warnings)


def _load_from_cwd() -> Config:
    from .config import find_root

    return load_config(find_root())


def config_summary(config: Config) -> list[str]:
    """Human-oriented summary used by `check --list` style output."""
    lines = [f"config: {config.root / CONFIG_NAME}"]
    for env, keys in config.keys.items():
        grace = len(keys.previous_recipients)
        lines.append(
            f"  {env}: {len(keys.recipients)} recipient(s), {grace} in grace, "
            f"reminder {keys.rotation_reminder_days}d"
        )
    lines.append(f"  managed files: {len(config.files)}")
    return lines
