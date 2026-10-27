"""Detect plaintext secret-shaped keys in tracked configuration files.

This is a heuristic guard, not a secret scanner: it looks for obvious
YAML/INI/ENV style keys (``password:``, ``api_key:`` ...) in the
tracked, structured files of a repository.  Encrypted (``*.enc.*``),
decrypted (``*.dec.*``) and excluded paths are skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

__all__ = ["Finding", "scan_text", "scan_paths", "TEXT_SUFFIXES"]

TEXT_SUFFIXES = {
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".cfg",
    ".conf",
    ".toml",
    ".env",
    ".properties",
    ".xml",
    ".tf",
    ".hcl",
}

# file-name extensions that indicate (de)ciphertext or key material
_ENC_MARKERS = (".enc.", ".dec.")
_KEY_SUFFIXES = {".key", ".agekey", ".pem", ".p12", ".pfx"}


@dataclass
class Finding:
    path: str
    lineno: int
    key: str

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.path}:{self.lineno}: plaintext secret key {self.key!r}"


def _key_regex(patterns: list[str]) -> re.Pattern:
    # longest first so that e.g. api_key wins over key-ish substrings;
    # patterns match at the END of a key so db_password / MY_API_KEY are
    # caught while max_tokens: is not (the pattern must touch the colon)
    alts = "|".join(
        re.escape(p) for p in sorted(patterns, key=len, reverse=True)
    )
    return re.compile(
        rf"^\s*(?:-\s+)?[\"']?(?P<key>[A-Za-z0-9_.\-]*?(?:{alts}))[\"']?\s*:(?:\s|$)",
        re.IGNORECASE,
    )


def scan_text(text: str, patterns: list[str], origin: str = "<text>") -> list[Finding]:
    regex = _key_regex(patterns)
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        match = regex.match(line)
        if match:
            findings.append(Finding(origin, lineno, match.group("key")))
    return findings


def _excluded(relpath: str, globs: list[str]) -> bool:
    path = PurePosixPath(relpath)
    name = path.name
    if any(marker in name for marker in _ENC_MARKERS):
        return True
    if Path(name).suffix.lower() in _KEY_SUFFIXES:
        return True
    for glob in globs:
        if path.match(glob) or PurePosixPath(name).match(glob):
            return True
        # treat a bare "dir/**" glob as a prefix match
        if glob.endswith("/**") and relpath.startswith(glob[:-3]):
            return True
    return False


def scan_paths(
    root: Path,
    relpaths: list[str],
    patterns: list[str],
    exclude: list[str],
) -> list[Finding]:
    findings: list[Finding] = []
    for rel in relpaths:
        rel = rel.replace("\\", "/")
        if _excluded(rel, exclude):
            continue
        suffix = Path(rel).suffix.lower()
        if suffix not in TEXT_SUFFIXES and not Path(rel).name.startswith(".env"):
            continue
        target = root / rel
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        findings.extend(scan_text(text, patterns, origin=rel))
    return findings
