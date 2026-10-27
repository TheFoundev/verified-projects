"""Command line interface: init, encrypt, decrypt, rotate, audit, check."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import datetime as _dt
from pathlib import Path

from . import __version__, gitutil, plaintext
from .audit import AuditLog, sha256_bytes
from .check import is_encrypted, run_check
from .config import (
    CONFIG_NAME,
    Config,
    ConfigError,
    add_configured_file,
    find_root,
    load_config,
    update_env_list,
    update_env_scalar,
)
from .policy import authorize_decrypt
from .sopsbin import (
    ToolError,
    age_keygen,
    find_tool,
    input_type_for,
    sops_decrypt,
    sops_encrypt,
)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_TOOL = 3

_RECIPIENT_RE = re.compile(r"(?m)^\s*recipient:\s*(age1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{58})\s*$")
_PLACEHOLDER_RECIPIENT = "age1" + "q" * 58


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _load(root_arg: str | None) -> tuple[Path, Config]:
    root = find_root(explicit=root_arg)
    return root, load_config(root)


def _rel(root: Path, path_str: str) -> str:
    """Normalize a user-supplied path to a root-relative POSIX path."""
    path = Path(path_str)
    if not path.is_absolute():
        cwd_rel = (Path.cwd() / path).resolve()
        if root == cwd_rel or str(cwd_rel).startswith(str(root) + os.sep):
            path = cwd_rel
        else:
            path = (root / path_str).resolve() if (root / path_str).exists() else cwd_rel
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        raise ConfigError(f"{path_str} is outside the project root {root}") from None


def _audit(config: Config) -> AuditLog:
    return AuditLog(config.root / config.audit_log)


def _tracked_or_walked(root: Path) -> list[str]:
    files = gitutil.tracked_files(root)
    if files is None:
        files = gitutil.walk_files(root)
    return files


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def _sample_plaintext(env: str) -> bytes:
    return (
        f"# sample secrets for {env} (plaintext source; encrypted by `secretsync init`)\n"
        f"db_host: db.{env}.internal\n"
        f"db_port: 5432\n"
        f"db_name: appdb\n"
        f"db_password: change-me-{env}\n"
        f"api_key: demo-{env}-key\n"
        f"feature_flags:\n"
        f"  enable_beta: false\n"
    ).encode()


def _config_text(envs: list[str], recipients: dict[str, str], key_dir: str) -> str:
    lines = [
        "# secretsync configuration (.sops.yaml-style)",
        "# Encrypt per-environment secrets with age recipients; CI holds the keys.",
        "version: 1",
        "",
        "keys:",
    ]
    for env in envs:
        lines += [
            f"  {env}:",
            "    recipients:",
            f"      - {recipients[env]}",
            "    previous_recipients: []",
            f"    rotation_reminder_days: {90 if env == 'dev' else 30}",
            f"    last_rotated: {_dt.date.today().isoformat()}",
        ]
    lines += [
        "",
        "creation_rules:",
    ]
    for env in envs:
        lines.append(f"  - env: {env}")
        lines.append(f"    path_regex: '^(environments/{env}/|ansible/secrets/{env}\\.)'")
    lines += [
        "",
        "files:",
    ]
    for env in envs:
        lines.append(f"  - environments/{env}/secrets.enc.yaml")
    lines += [
        "",
        "decrypt_policy:",
        "  require_ci: true",
        "  ci_var: CI",
        '  ci_truthy: "true"',
        "  marker_var: SECRETSYNC_RUN_ID",
        "  enforce_local_deny: true",
        "",
        "plaintext_patterns:",
    ]
    for pattern in (
        "password",
        "passwd",
        "api_key",
        "apikey",
        "secret_key",
        "client_secret",
        "private_key",
        "access_key",
        "auth_token",
        "token",
        "credentials",
    ):
        lines.append(f"  - {pattern}")
    lines += [
        "",
        "scan_exclude:",
        "  - .tools/**",
        "  - .secretsync/**",
        "  - '**/*.enc.*'",
        "  - '**/*.dec.*'",
        "",
        "audit_log: .secretsync/audit.log",
        f"key_dir: {key_dir}",
        "",
    ]
    return "\n".join(lines)


_GITATTRIBUTES = """\
# secretsync: ciphertext and key material handling
*.enc.yaml diff=sops text eol=lf
*.dec.yaml -text
*.key -text
*.agekey -text
"""

_PRECOMMIT_HOOK = """\
#!/bin/sh
# secretsync pre-commit gate: rejects plaintext secrets, unencrypted
# config files, tracked decrypted copies / key material, and a tampered
# or truncated audit trail.
#
# Enable with:  git config core.hooksPath .githooks
exec python3 -m secretsync check
"""


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve() if args.root else Path.cwd()
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / CONFIG_NAME
    if config_path.exists() and not args.force:
        _err(f"init refused: {config_path} already exists (use --force to overwrite)")
        return EXIT_REFUSED

    envs = [e.strip() for e in args.envs.split(",") if e.strip()]
    if not envs:
        _err("init refused: --envs must list at least one environment")
        return EXIT_REFUSED

    recipients: dict[str, str] = {}
    key_dir = ".secretsync/local-keys"
    if args.no_keys:
        for env in envs:
            recipients[env] = _PLACEHOLDER_RECIPIENT
        _err(
            "init: --no-keys given; writing placeholder recipients "
            "(replace them with real age public keys before encrypting)"
        )
    else:
        for env in envs:
            key_path = root / key_dir / f"{env}.key"
            if key_path.exists():
                if not args.force:
                    _err(f"init refused: {key_path} exists (use --force to rotate the local key)")
                    return EXIT_REFUSED
                key_path.unlink()  # age-keygen will not overwrite
            try:
                recipients[env] = age_keygen(root, key_path)
            except ToolError as exc:
                _err(f"init refused: {exc}")
                return EXIT_TOOL
            os.chmod(key_path, 0o600)

    config_path.write_text(_config_text(envs, recipients, key_dir), encoding="utf-8")

    gitattributes = root / ".gitattributes"
    if not gitattributes.exists() or args.force:
        gitattributes.write_text(_GITATTRIBUTES, encoding="utf-8")
    hook = root / ".githooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(_PRECOMMIT_HOOK, encoding="utf-8")
    hook.chmod(0o755)

    config = load_config(root)
    actor = gitutil.actor_for(root)
    audit = _audit(config)

    errors: list[str] = []
    for env in envs:
        (root / "environments" / env).mkdir(parents=True, exist_ok=True)

    # `init` is the first record in the trail; samples are appended after it.
    audit.append(
        "init",
        actor=actor,
        env=",".join(envs),
        detail={"version": __version__, "placeholders": args.no_keys},
    )

    if not args.no_keys and not args.no_samples:
        for env in envs:
            rel = f"environments/{env}/secrets.enc.yaml"
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                ciphertext = sops_encrypt(
                    root,
                    _sample_plaintext(env),
                    config.recipients_for(env),
                )
            except ToolError as exc:
                errors.append(str(exc))
                continue
            target.write_bytes(ciphertext)
            audit.append(
                "encrypt",
                actor=actor,
                file=rel,
                env=env,
                sha256=sha256_bytes(ciphertext),
                detail={"source": "init-sample", "recipients": config.recipients_for(env)},
            )
    elif args.no_samples or args.no_keys:
        if args.no_keys:
            _err("init: samples skipped because --no-keys leaves placeholder recipients")

    if errors:
        for message in errors:
            _err(message)
        return EXIT_TOOL

    print(f"initialized {config_path}")
    for env in envs:
        print(f"  {env}: recipient {recipients[env]}")
        print(f"           identity  {key_dir}/{env}.key (gitignored)")
    if not args.no_samples:
        print(f"  samples encrypted under environments/<env>/{'secrets.enc.yaml'}")
    print("enable the pre-commit gate:  git config core.hooksPath .githooks")
    return EXIT_OK


# ---------------------------------------------------------------------------
# encrypt
# ---------------------------------------------------------------------------

def cmd_encrypt(args: argparse.Namespace) -> int:
    root, config = _load(args.root)
    rel = _rel(root, args.file)
    source = root / rel
    if not source.is_file():
        _err(f"encrypt refused: {rel} does not exist")
        return EXIT_REFUSED
    config.require_env(args.env)

    resolved = config.resolve_env(rel)
    if resolved is None:
        _err(
            f"encrypt refused: {rel} matches no creation rule "
            f"(rules cover: {', '.join(sorted({r.path_regex for r in config.creation_rules}))})"
        )
        return EXIT_REFUSED
    if resolved != args.env:
        _err(f"encrypt refused: {rel} belongs to environment {resolved!r}, not {args.env!r}")
        return EXIT_REFUSED

    if is_encrypted(source):
        _err(f"encrypt refused: {rel} already contains ciphertext (use `rotate` to re-key)")
        return EXIT_REFUSED

    # Plaintext guard: refuse while *other* tracked files carry
    # secret-shaped keys in cleartext.
    findings = plaintext.scan_paths(
        root,
        [f for f in _tracked_or_walked(root) if f != rel],
        config.plaintext_patterns,
        config.scan_exclude,
    )
    if findings:
        _err("encrypt refused: plaintext secrets are present in tracked files:")
        for finding in findings:
            _err(f"  {finding}")
        _err("encrypt or remove them first — only ciphertext belongs in git")
        return EXIT_REFUSED

    if args.output:
        out_rel = _rel(root, args.output)
        in_place = out_rel == rel
    elif ".enc." in Path(rel).name:
        out_rel, in_place = rel, True
    else:
        path = Path(rel)
        out_rel = str(path.with_name(path.stem + ".enc" + path.suffix)).replace("\\", "/")
        in_place = False

    target = root / out_rel
    if not in_place and target.exists():
        _err(f"encrypt refused: {out_rel} already exists (use `rotate --env {args.env}`)")
        return EXIT_REFUSED
    if config.resolve_env(out_rel) != args.env:
        _err(
            f"encrypt refused: output {out_rel} does not belong to environment "
            f"{args.env!r} per creation rules"
        )
        return EXIT_REFUSED

    try:
        ciphertext = sops_encrypt(
            root,
            source.read_bytes(),
            config.recipients_for(args.env),
            input_type=input_type_for(source),
        )
    except ToolError as exc:
        _err(str(exc))
        return EXIT_TOOL

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(ciphertext)
    # keep the manifest in sync so `check` sees the new file as managed
    add_configured_file(root / CONFIG_NAME, out_rel)
    actor = gitutil.actor_for(root)
    _audit(config).append(
        "encrypt",
        actor=actor,
        file=out_rel,
        env=args.env,
        sha256=sha256_bytes(ciphertext),
        detail={"source": rel, "recipients": config.recipients_for(args.env)},
    )
    print(f"encrypted {rel} -> {out_rel} for env {args.env}")
    if not in_place:
        _err(
            f"note: plaintext source {rel} is still on disk — keep it untracked "
            "(or delete it) so `check` stays green"
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# decrypt (CI-gated)
# ---------------------------------------------------------------------------

def _resolve_key_file(config: Config, env_name: str) -> str | None:
    if os.environ.get("SOPS_AGE_KEY_FILE"):
        return None  # already provided by the environment (the CI pattern)
    keys = config.keys.get(env_name)
    if keys and keys.key_file:
        candidate = Path(keys.key_file)
        if not candidate.is_absolute():
            candidate = config.root / candidate
        if candidate.exists():
            return str(candidate)
    default = config.root / config.key_dir / f"{env_name}.key"
    if default.exists():
        return str(default)
    return None


def cmd_decrypt(args: argparse.Namespace) -> int:
    root, config = _load(args.root)
    rel = _rel(root, args.file)
    source = root / rel
    if not source.is_file():
        _err(f"decrypt refused: {rel} does not exist")
        return EXIT_REFUSED
    config.require_env(args.env)

    resolved = config.resolve_env(rel)
    if resolved is None:
        _err(f"decrypt refused: {rel} matches no creation rule")
        return EXIT_REFUSED
    if resolved != args.env:
        _err(f"decrypt refused: {rel} belongs to environment {resolved!r}, not {args.env!r}")
        return EXIT_REFUSED
    if not is_encrypted(source):
        _err(f"decrypt refused: {rel} is not a sops-encrypted file")
        return EXIT_REFUSED

    policy = config.decrypt_policy
    decision = authorize_decrypt(policy, os.environ, allow_local=args.allow_local)
    ciphertext = source.read_bytes()
    actor = gitutil.actor_for(root)
    if not decision.allowed:
        _audit(config).append(
            "decrypt_denied",
            actor=actor,
            file=rel,
            env=args.env,
            sha256=sha256_bytes(ciphertext),
            detail={"mode": decision.mode, "reason": decision.reason,
                    "allow_local": bool(args.allow_local)},
        )
        _err(f"decrypt refused: {decision.reason}")
        return EXIT_REFUSED

    key_file = _resolve_key_file(config, args.env)
    try:
        plaintext_bytes = sops_decrypt(
            root,
            ciphertext,
            key_file=key_file,
            input_type=input_type_for(source),
        )
    except ToolError as exc:
        _audit(config).append(
            "decrypt_denied",
            actor=actor,
            file=rel,
            env=args.env,
            sha256=sha256_bytes(ciphertext),
            detail={"mode": decision.mode, "reason": "sops failure", "error": str(exc)[-400:]},
        )
        _err(str(exc))
        return EXIT_TOOL

    out_path: Path | None = None
    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = (Path.cwd() / out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(plaintext_bytes)
        try:
            out_path.relative_to(root)
        except ValueError:
            pass
        else:
            _err(
                f"warning: {out_path} is inside the repository — write decrypted "
                "secrets outside the tree (e.g. $RUNNER_TEMP) so `check` cannot see them"
            )

    _audit(config).append(
        "decrypt",
        actor=actor,
        file=rel,
        env=args.env,
        sha256=sha256_bytes(ciphertext),
        detail={
            "mode": decision.mode,
            "policy": decision.reason,
            "output": str(out_path) if out_path else None,
        },
    )
    if out_path is None:
        sys.stdout.buffer.write(plaintext_bytes)
        sys.stdout.buffer.flush()
    else:
        print(f"decrypted {rel} -> {out_path} (mode: {decision.mode})", file=sys.stderr)
    return EXIT_OK


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------

def rotate_env(config: Config, env_name: str, drop_previous: bool = False) -> list[str]:
    """Re-encrypt every managed file of *env_name* to the current recipients.

    Recipients found on the ciphertext that are no longer current are
    moved into ``previous_recipients`` and kept in the encryption set
    (grace period) unless *drop_previous* is set.  Returns per-file
    human-readable summaries.
    """
    files = config.files_for_env(env_name)
    config.require_env(env_name)
    if not files:
        raise ConfigError(f"no managed files resolve to environment {env_name!r}")
    keys = config.keys[env_name]
    actor = gitutil.actor_for(config.root)
    audit = _audit(config)

    # Phase A: read + decrypt everything before touching the tree.
    plan: list[tuple[str, bytes, bytes, list[str]]] = []
    for rel in files:
        path = config.root / rel
        if not path.is_file():
            raise ConfigError(f"cannot rotate: {rel} is missing")
        ciphertext = path.read_bytes()
        if not is_encrypted(path):
            raise ConfigError(f"cannot rotate: {rel} is not encrypted")
        old_recipients = _RECIPIENT_RE.findall(
            ciphertext.decode("utf-8", "replace")
        )
        plaintext_bytes = sops_decrypt(
            config.root,
            ciphertext,
            key_file=_resolve_key_file(config, env_name),
            input_type=input_type_for(path),
        )
        plan.append((rel, ciphertext, plaintext_bytes, old_recipients))

    current = list(keys.recipients)
    grace = [r for r in keys.previous_recipients if r not in current]
    discovered: list[str] = []
    for _, _, _, old_recipients in plan:
        for rec in old_recipients:
            if rec not in current and rec not in grace and rec not in discovered:
                discovered.append(rec)
    grace = grace + discovered
    if drop_previous:
        grace = []
    target = current + [r for r in grace if r not in current]

    # Phase B: re-encrypt, audit and record.
    summaries: list[str] = []
    for rel, old_cipher, plaintext_bytes, old_recipients in plan:
        new_cipher = sops_encrypt(
            config.root,
            plaintext_bytes,
            target,
            input_type=input_type_for(config.root / rel),
        )
        path = config.root / rel
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".rotate-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(new_cipher)
        os.replace(tmp, path)
        audit.append(
            "rotate",
            actor=actor,
            file=rel,
            env=env_name,
            sha256=sha256_bytes(new_cipher),
            detail={
                "previous_sha256": sha256_bytes(old_cipher),
                "recipients": target,
                "added_to_grace": discovered,
                "dropped_previous": bool(drop_previous),
                "old_recipients": old_recipients,
            },
        )
        summaries.append(
            f"  {rel}: {len(old_recipients)} -> {len(target)} recipient(s)"
        )

    # Phase C: persist rotation state in the config.
    config_path = config.root / CONFIG_NAME
    update_env_list(config_path, env_name, "previous_recipients", grace)
    update_env_scalar(config_path, env_name, "last_rotated", _dt.date.today().isoformat())
    return summaries


def cmd_rotate(args: argparse.Namespace) -> int:
    root, config = _load(args.root)
    try:
        summaries = rotate_env(config, args.env, drop_previous=args.drop_previous)
    except ConfigError as exc:
        _err(f"rotate refused: {exc}")
        return EXIT_REFUSED
    except ToolError as exc:
        _err(str(exc))
        return EXIT_TOOL
    print(f"rotated environment {args.env}:")
    for line in summaries:
        print(line)
    keys = load_config(root).keys[args.env]
    if args.drop_previous:
        print("  grace recipients dropped: only current recipients can decrypt now")
    elif keys.previous_recipients:
        print(
            f"  grace: {len(keys.previous_recipients)} previous recipient(s) still able "
            f"to decrypt until `rotate --env {args.env} --drop-previous`"
        )
    print(f"  last_rotated: {keys.last_rotated} "
          f"(reminder every {keys.rotation_reminder_days} days)")
    return EXIT_OK


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

def cmd_audit(args: argparse.Namespace) -> int:
    root, config = _load(args.root)
    log = _audit(config)

    if args.audit_cmd == "verify":
        violations = log.verify(check_files=args.check_files, root=root)
        if violations:
            _err(f"audit verify FAILED ({len(violations)} violation(s)):")
            for violation in violations:
                _err(f"  {violation}")
            return EXIT_REFUSED
        total = len(log.records())
        print(f"audit chain OK: {total} record(s)"
              + (", file hashes checked" if args.check_files else ""))
        return EXIT_OK

    records = log.records()
    if not records:
        print("audit log is empty")
        return EXIT_OK
    shown = records[-args.tail:]
    for rec in shown:
        sha = (rec.get("sha256") or "-")[:12]
        seq = rec.get("seq")
        seq_s = f"{seq:>4}" if isinstance(seq, int) else "   ?"
        print(
            f"#{seq_s}  {rec.get('ts')}  {str(rec.get('event')):<16} "
            f"env={str(rec.get('env') or '-'):<8} file={str(rec.get('file') or '-'):<44} "
            f"sha256={sha}  actor={rec.get('actor')}"
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    report = run_check(root=Path(args.root).resolve() if args.root else None)
    for warning in report.warnings:
        print(f"warning: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")
    status = "OK" if report.ok else f"FAILED ({len(report.errors)} error(s))"
    print(f"check: {status}")
    return EXIT_OK if report.ok else EXIT_REFUSED


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="secretsync",
        description=(
            "Encrypted configuration management in Git: per-environment SOPS/age "
            "encryption, CI-only decryption, key rotation and an audit trail."
        ),
    )
    parser.add_argument("--version", action="version", version=f"secretsync {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--root", help="project root containing .sops.yaml (default: auto-detect)")

    p_init = sub.add_parser("init", help="scaffold config, directory layout, hooks and samples")
    common(p_init)
    p_init.add_argument("--envs", default="dev,staging,prod",
                        help="comma-separated environments (default: dev,staging,prod)")
    p_init.add_argument("--force", action="store_true",
                        help="overwrite an existing config and local keys")
    p_init.add_argument("--no-keys", action="store_true",
                        help="skip age-keygen and write placeholder recipients")
    p_init.add_argument("--no-samples", action="store_true",
                        help="skip encrypting the sample secrets files")
    p_init.set_defaults(func=cmd_init)

    p_enc = sub.add_parser("encrypt", help="encrypt a plaintext file for an environment")
    common(p_enc)
    p_enc.add_argument("file", help="plaintext file to encrypt")
    p_enc.add_argument("--env", required=True, help="target environment (dev/staging/prod)")
    p_enc.add_argument("--output", help="output path (default: in-place for *.enc.* or sibling)")
    p_enc.set_defaults(func=cmd_encrypt)

    p_dec = sub.add_parser("decrypt", help="decrypt a file (CI-gated)")
    common(p_dec)
    p_dec.add_argument("file", help="encrypted file to decrypt")
    p_dec.add_argument("--env", required=True, help="environment the file belongs to")
    p_dec.add_argument("--output", help="write plaintext here (default: stdout)")
    p_dec.add_argument("--allow-local", action="store_true",
                       help="developer escape hatch (refused when enforce_local_deny is true)")
    p_dec.set_defaults(func=cmd_decrypt)

    p_rot = sub.add_parser("rotate", help="re-encrypt an environment's files to current recipients")
    common(p_rot)
    p_rot.add_argument("--env", required=True, help="environment to rotate")
    p_rot.add_argument("--drop-previous", action="store_true",
                       help="end the grace period: encrypt to current recipients only")
    p_rot.set_defaults(func=cmd_rotate)

    p_audit = sub.add_parser("audit", help="show or verify the audit trail")
    common(p_audit)
    p_audit.add_argument("--tail", type=int, default=20, help="records to show (default: 20)")
    audit_sub = p_audit.add_subparsers(dest="audit_cmd", metavar="verify")
    p_verify = audit_sub.add_parser("verify", help="validate the hash chain and checkpoints")
    common(p_verify)
    p_verify.add_argument("--check-files", action="store_true",
                          help="also compare on-disk ciphertext against audited sha256")
    p_audit.set_defaults(func=cmd_audit, audit_cmd="list")

    p_check = sub.add_parser(
        "check",
        help="validate config, ciphertext coverage, tracked-file hygiene and the audit chain",
    )
    common(p_check)
    p_check.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed the message
        return int(exc.code or 0)
    try:
        return args.func(args)
    except ConfigError as exc:
        _err(f"config error: {exc}")
        return EXIT_REFUSED
    except ToolError as exc:
        _err(str(exc))
        return EXIT_TOOL
    except KeyboardInterrupt:  # pragma: no cover
        return 130
