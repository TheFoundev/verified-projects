"""Configuration model for secretsync (``.sops.yaml``-style file).

The configuration is a YAML-subset document with a ``keys`` section per
environment (age recipients, grace recipients, rotation policy), an
ordered list of ``creation_rules`` mapping file paths to environments,
the set of managed secret files, and a ``decrypt_policy`` describing
when decryption is allowed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import miniyaml

__all__ = [
    "ConfigError",
    "DecryptPolicy",
    "CreationRule",
    "EnvKeys",
    "Config",
    "load_config",
    "find_root",
    "update_env_list",
    "update_env_scalar",
    "CONFIG_NAME",
]

CONFIG_NAME = ".sops.yaml"

# age recipients are bech32 strings: "age1" + 58 characters from the
# bech32 alphabet (no '1', 'b', 'i', 'o').
_AGE_RE = re.compile(r"^age1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{58}$")

DEFAULT_PLAINTEXT_PATTERNS = [
    "password",
    "passwd",
    "api_key",
    "apikey",
    "api-key",
    "secret_key",
    "client_secret",
    "private_key",
    "access_key",
    "auth_token",
    "token",
    "credentials",
]

DEFAULT_SCAN_EXCLUDE = [
    ".tools/**",
    ".secretsync/**",
    ".git/**",
    "**/*.enc.*",
    "**/*.dec.*",
    "tests/fixtures/**",
]


class ConfigError(ValueError):
    """Raised when the configuration file is missing or invalid."""


@dataclass
class EnvKeys:
    env: str
    recipients: list[str]
    previous_recipients: list[str] = field(default_factory=list)
    rotation_reminder_days: int = 90
    last_rotated: str | None = None
    key_file: str | None = None


@dataclass
class CreationRule:
    env: str
    path_regex: str

    def matches(self, relpath: str) -> bool:
        return re.search(self.path_regex, relpath) is not None


@dataclass
class DecryptPolicy:
    require_ci: bool = True
    ci_var: str = "CI"
    ci_truthy: str = "true"
    marker_var: str = "SECRETSYNC_RUN_ID"
    marker: str | None = None
    enforce_local_deny: bool = True


@dataclass
class Config:
    root: Path
    version: int
    keys: dict[str, EnvKeys]
    creation_rules: list[CreationRule]
    files: list[str]
    decrypt_policy: DecryptPolicy
    plaintext_patterns: list[str]
    scan_exclude: list[str]
    audit_log: str
    key_dir: str
    sops_bin: str | None = None

    def envs(self) -> list[str]:
        return list(self.keys)

    def require_env(self, env: str) -> EnvKeys:
        if env not in self.keys:
            raise ConfigError(
                f"unknown environment {env!r} (configured: {', '.join(self.envs()) or 'none'})"
            )
        return self.keys[env]

    def resolve_env(self, relpath: str) -> str | None:
        relpath = relpath.replace("\\", "/")
        for rule in self.creation_rules:
            if rule.matches(relpath):
                return rule.env
        return None

    def files_for_env(self, env: str) -> list[str]:
        return [f for f in self.files if self.resolve_env(f) == env]

    def recipients_for(self, env: str) -> list[str]:
        return list(self.require_env(env).recipients)


def find_root(start: Path | None = None, explicit: str | None = None) -> Path:
    """Locate the project root: --root wins, else walk up for ``.sops.yaml``."""
    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        if not (root / CONFIG_NAME).is_file():
            raise ConfigError(f"{root / CONFIG_NAME} not found (root was given explicitly)")
        return root
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    raise ConfigError(
        f"{CONFIG_NAME} not found in {here} or any parent directory "
        "(run `python3 -m secretsync init` or pass --root)"
    )


def _require(mapping: dict, key: str, where: str):
    if key not in mapping or mapping[key] is None:
        raise ConfigError(f"missing required key {key!r} in {where}")
    return mapping[key]


def _as_str_list(value, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(i, str) for i in value):
        raise ConfigError(f"{where} must be a list of strings")
    return value


def _check_recipients(recipients: list[str], where: str) -> None:
    if not recipients:
        raise ConfigError(f"{where} must not be empty")
    for rec in recipients:
        if not _AGE_RE.match(rec):
            raise ConfigError(
                f"{where}: {rec!r} is not a valid age public key "
                "(expected age1 followed by 58 bech32 characters)"
            )


def load_config(root: Path) -> Config:
    path = root / CONFIG_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{path} not found") from None
    try:
        data = miniyaml.loads(text)
    except miniyaml.YamlError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")

    version = data.get("version", 1)
    if version != 1:
        raise ConfigError(f"{path}: unsupported config version {version!r}")

    raw_keys = _require(data, "keys", CONFIG_NAME)
    if not isinstance(raw_keys, dict) or not raw_keys:
        raise ConfigError(f"{path}: `keys` must map at least one environment")
    keys: dict[str, EnvKeys] = {}
    for env, body in raw_keys.items():
        if not isinstance(body, dict):
            raise ConfigError(f"{path}: keys.{env} must be a mapping")
        recipients = _as_str_list(
            _require(body, "recipients", f"keys.{env}"), f"keys.{env}.recipients"
        )
        _check_recipients(recipients, f"keys.{env}.recipients")
        previous = _as_str_list(body.get("previous_recipients"), f"keys.{env}.previous_recipients")
        for rec in previous:
            if not _AGE_RE.match(rec):
                raise ConfigError(
                    f"keys.{env}.previous_recipients: {rec!r} is not a valid age public key"
                )
        overlap = set(recipients) & set(previous)
        if overlap:
            raise ConfigError(
                f"keys.{env}: recipients and previous_recipients overlap: {sorted(overlap)}"
            )
        reminder = body.get("rotation_reminder_days", 90)
        if not isinstance(reminder, int) or isinstance(reminder, bool) or reminder <= 0:
            raise ConfigError(f"keys.{env}.rotation_reminder_days must be a positive integer")
        last_rotated = body.get("last_rotated")
        if last_rotated is not None and not isinstance(last_rotated, str):
            raise ConfigError(f"keys.{env}.last_rotated must be a date string")
        key_file = body.get("key_file")
        if key_file is not None and not isinstance(key_file, str):
            raise ConfigError(f"keys.{env}.key_file must be a path string")
        keys[env] = EnvKeys(
            env=env,
            recipients=recipients,
            previous_recipients=previous,
            rotation_reminder_days=reminder,
            last_rotated=last_rotated,
            key_file=key_file,
        )

    raw_rules = _require(data, "creation_rules", CONFIG_NAME)
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ConfigError(f"{path}: `creation_rules` must be a non-empty list")
    rules: list[CreationRule] = []
    for i, body in enumerate(raw_rules):
        if not isinstance(body, dict):
            raise ConfigError(f"{path}: creation_rules[{i}] must be a mapping")
        env = _require(body, "env", f"creation_rules[{i}]")
        pattern = _require(body, "path_regex", f"creation_rules[{i}]")
        if env not in keys:
            raise ConfigError(
                f"{path}: creation_rules[{i}] references unknown environment {env!r}"
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(
                f"{path}: creation_rules[{i}] has invalid regex {pattern!r}: {exc}"
            ) from None
        rules.append(CreationRule(env=env, path_regex=pattern))

    files = _as_str_list(data.get("files"), f"{CONFIG_NAME}.files")
    for rel in files:
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise ConfigError(f"{CONFIG_NAME}.files: {rel!r} must be a relative path inside the repo")

    raw_policy = data.get("decrypt_policy") or {}
    if not isinstance(raw_policy, dict):
        raise ConfigError(f"{path}: decrypt_policy must be a mapping")
    policy = DecryptPolicy(
        require_ci=bool(raw_policy.get("require_ci", True)),
        ci_var=str(raw_policy.get("ci_var", "CI")),
        ci_truthy=str(raw_policy.get("ci_truthy", "true")),
        marker_var=str(raw_policy.get("marker_var", "SECRETSYNC_RUN_ID")),
        marker=raw_policy.get("marker"),
        enforce_local_deny=bool(raw_policy.get("enforce_local_deny", True)),
    )
    if policy.require_ci and not policy.marker_var:
        raise ConfigError("decrypt_policy.marker_var must be set when require_ci is true")

    patterns = _as_str_list(
        data.get("plaintext_patterns") or DEFAULT_PLAINTEXT_PATTERNS,
        "plaintext_patterns",
    )
    if not patterns:
        raise ConfigError("plaintext_patterns must not be empty")

    exclude = _as_str_list(
        data.get("scan_exclude") or DEFAULT_SCAN_EXCLUDE, "scan_exclude"
    )

    return Config(
        root=root,
        version=version,
        keys=keys,
        creation_rules=rules,
        files=files,
        decrypt_policy=policy,
        plaintext_patterns=patterns,
        scan_exclude=exclude,
        audit_log=str(data.get("audit_log") or ".secretsync/audit.log"),
        key_dir=str(data.get("key_dir") or ".secretsync/local-keys"),
        sops_bin=data.get("sops_bin"),
    )


# ---------------------------------------------------------------------------
# Targeted, line-based config editing used by `rotate` (and by tests).
# ---------------------------------------------------------------------------


def _format_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if text == "" or text != text.strip() or any(c in text for c in ":#[]{}\"'"):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _find_env_block(lines: list[str], env: str) -> tuple[int, int]:
    """Return (block_start, block_end) line indexes of ``  <env>:`` under ``keys:``."""
    keys_idx = None
    for i, line in enumerate(lines):
        if line.rstrip() == "keys:":
            keys_idx = i
            break
    if keys_idx is None:
        raise ConfigError("`keys:` section not found in config")
    env_indent = None
    start = None
    for i in range(keys_idx + 1, len(lines)):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent < 2:
            break
        if env_indent is None:
            env_indent = indent
        if indent == env_indent and line.strip().rstrip(":") == env:
            start = i
            break
    if start is None:
        raise ConfigError(f"environment {env!r} not found under `keys:`")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= env_indent and not line.strip().startswith("- "):
            end = i
            break
    return start, end


def _find_key_line(lines: list[str], start: int, end: int, key: str) -> int | None:
    for i in range(start + 1, end):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            continue
        head = stripped.split(":", 1)[0].strip()
        if head == key and ":" in stripped:
            return i
    return None


def update_env_list(config_path: Path, env: str, key: str, items: list[str]) -> None:
    """Replace ``keys.<env>.<key>`` (scalar or list) in place, preserving layout."""
    lines = config_path.read_text(encoding="utf-8").splitlines()
    start, end = _find_env_block(lines, env)
    key_line = _find_key_line(lines, start, end, key)

    if not items:  # collapse to an inline empty list
        block = []
        value = f"{' ' * 4}{key}: []"
    else:
        block = [f"{' ' * 6}- {item}" for item in items]
        value = None

    if key_line is None:
        if items:
            # insert after the last list item of the env block, else right
            # after the ``<env>:`` header line
            insert_at = start + 1
            for i in range(start + 1, end):
                if lines[i].strip().startswith("- "):
                    insert_at = i + 1
            lines[insert_at:insert_at] = [f"    {key}:"] + block
        else:
            lines.insert(end, value)
    else:
        indent = len(lines[key_line]) - len(lines[key_line].lstrip(" "))
        inline = lines[key_line].split(":", 1)[1].strip()
        if not items:
            # collapse block form too: swallow the trailing "- item" lines
            j = key_line + 1
            while j < end:
                stripped = lines[j].strip()
                child_indent = len(lines[j]) - len(lines[j].lstrip(" "))
                if stripped.startswith("- ") and child_indent > indent:
                    j += 1
                else:
                    break
            lines[key_line:j] = [f"{' ' * indent}{key}: []"]
        elif inline:
            lines[key_line : key_line + 1] = [f"{' ' * indent}{key}:"] + block
        else:
            j = key_line + 1
            while j < end:
                stripped = lines[j].strip()
                child_indent = len(lines[j]) - len(lines[j].lstrip(" "))
                if stripped.startswith("- ") and child_indent > indent:
                    j += 1
                else:
                    break
            lines[key_line + 1 : j] = block
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_env_scalar(config_path: Path, env: str, key: str, value) -> None:
    """Replace or insert ``keys.<env>.<key>: value`` in place."""
    lines = config_path.read_text(encoding="utf-8").splitlines()
    start, end = _find_env_block(lines, env)
    formatted = _format_scalar(value)
    key_line = _find_key_line(lines, start, end, key)
    if key_line is not None:
        indent = len(lines[key_line]) - len(lines[key_line].lstrip(" "))
        lines[key_line] = f"{' ' * indent}{key}: {formatted}"
    else:
        lines.insert(end, f"    {key}: {formatted}")
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def add_configured_file(config_path: Path, rel: str) -> bool:
    """Register *rel* under the top-level ``files:`` list.

    Returns True when a line was actually inserted.
    """
    lines = config_path.read_text(encoding="utf-8").splitlines()
    header = None
    for i, line in enumerate(lines):
        if line.rstrip() == "files:":
            header = i
            break
    if header is None:
        raise ConfigError("`files:` section not found in config")
    items: list[int] = []
    j = header + 1
    while j < len(lines):
        stripped = lines[j].strip()
        if stripped.startswith("- "):
            items.append(j)
            j += 1
            continue
        if not stripped or stripped.startswith("#"):
            j += 1
            continue
        break
    if any(lines[i].strip()[2:].strip().strip("'\"") == rel for i in items):
        return False
    insert_at = items[-1] + 1 if items else header + 1
    lines.insert(insert_at, f"  - {rel}")
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True
