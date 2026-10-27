"""The API of the project: the environments, the audit trail and the checks.

The numbers come from the real core of secretsync: the configuration is read
with `secretsync.config.load_config`, the trail is read and verified with
`secretsync.audit.AuditLog` and the pre-commit gate is the very one the
`.githooks/pre-commit` hook runs, `secretsync.check.run_check`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from secretsync.audit import AuditLog  # noqa: E402
from secretsync.check import run_check  # noqa: E402
from secretsync.config import find_root, load_config  # noqa: E402
from secretsync.policy import authorize_decrypt  # noqa: E402


def config():
    """The configuration of the repository, read from the project root."""
    return load_config(find_root(explicit=str(ROOT)))


def audit_log(cfg) -> AuditLog:
    return AuditLog(cfg.root / cfg.audit_log)


def records(cfg) -> list:
    """Every record of the hash-chained trail."""
    try:
        return audit_log(cfg).records()
    except Exception:
        return []


def environments(cfg) -> list:
    """The recipients and the rotation state of every environment."""
    rows = []
    for env, keys in cfg.keys.items():
        rows.append({
            "environment": env,
            "recipients": len(keys.recipients),
            "previous": len(keys.previous_recipients),
            "last_rotated": keys.last_rotated or "never",
            "reminder_days": keys.rotation_reminder_days,
        })
    return rows


def summary() -> dict:
    """The state of the repository: the environments, the trail and the chain."""
    cfg = config()
    trail = records(cfg)
    violations = audit_log(cfg).verify(check_files=True, root=cfg.root)
    encrypted = 0
    for rel in cfg.files:
        path = cfg.root / rel
        if path.is_file():
            encrypted += 1
    events: dict[str, int] = {}
    for record in trail:
        events[record.get("event", "?")] = events.get(record.get("event", "?"), 0) + 1
    return {
        "stats": {
            "environments": len(cfg.keys),
            "encrypted_files": encrypted,
            "audit_records": len(trail),
            "chain": "intact" if not violations else "broken",
        },
        "series": [
            {"label": event, "value": count}
            for event, count in sorted(events.items(), key=lambda item: -item[1])
        ],
        "lists": {
            "audit": [
                {
                    "seq": record.get("seq"),
                    "event": record.get("event"),
                    "file": record.get("file") or "-",
                    "env": record.get("env") or "-",
                    "actor": record.get("actor"),
                    "ts": record.get("ts"),
                }
                for record in trail
            ],
            "environments": environments(cfg),
        },
    }


def handle(method, path, payload, user):
    """The read and the write endpoints of the project."""
    if method == "GET" and path == "/api/audit":
        return 200, {"records": records(config())}
    if method == "GET" and path == "/api/environments":
        return 200, {"environments": environments(config())}
    if method == "POST" and path == "/api/check":
        cfg = config()
        report = run_check(cfg)
        return 200, {
            "ok": report.ok,
            "errors": len(report.errors),
            "warnings": len(report.warnings),
            "first_error": report.errors[0] if report.errors else "",
            "first_warning": report.warnings[0] if report.warnings else "",
        }
    if method == "POST" and path == "/api/verify":
        cfg = config()
        log = audit_log(cfg)
        violations = log.verify(check_files=True, root=cfg.root)
        return 200, {
            "records": len(log.records()),
            "violations": len(violations),
            "ok": not violations,
            "first_violation": violations[0] if violations else "",
        }
    if method == "POST" and path == "/api/decrypt":
        cfg = config()
        allow_local = bool(payload.get("allow_local"))
        decision = authorize_decrypt(cfg.decrypt_policy, os.environ, allow_local=allow_local)
        return 200, {
            "allowed": decision.allowed,
            "mode": decision.mode,
            "reason": decision.reason,
            "require_ci": cfg.decrypt_policy.require_ci,
            "marker_var": cfg.decrypt_policy.marker_var,
        }
    return 404, {"error": "not found"}
