"""Append-only, hash-chained JSONL audit trail.

Every event records ``event``, ``file``, ``env``, ``actor``, ``ts`` and
the ``sha256`` of the ciphertext involved, plus chain fields:

* ``seq``            contiguous sequence number starting at 0
* ``prev``           ``record_sha256`` of the previous line (genesis: 64 zeros)
* ``record_sha256``  sha256 over the canonical JSON of the record minus
                     the ``record_sha256`` field itself

A side-car checkpoint (``.secretsync/audit.head``) stores the last
``seq``/hash pair so that truncating the *tail* of the log is also
detectable (mid-log truncation and reordering are caught by the chain
itself).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["AuditError", "AuditLog", "GENESIS", "sha256_file", "sha256_bytes"]

GENESIS = "0" * 64


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def _canonical(record: dict) -> bytes:
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _record_hash(record: dict) -> str:
    return sha256_bytes(_canonical(record))


class AuditError(Exception):
    """Raised for structural audit-log problems."""

    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__("; ".join(violations))


class AuditLog:
    def __init__(self, path: Path, head_path: Path | None = None):
        self.path = Path(path)
        self.head_path = Path(head_path) if head_path else self.path.with_name(
            self.path.name.removesuffix(".log") + ".head"
            if self.path.name.endswith(".log")
            else self.path.name + ".head"
        )

    # -- reading ----------------------------------------------------------

    def records(self) -> list[dict]:
        if not self.path.is_file():
            return []
        out: list[dict] = []
        for lineno, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AuditError([f"audit line {lineno}: invalid JSON ({exc})"]) from None
            if not isinstance(obj, dict):
                raise AuditError([f"audit line {lineno}: expected a JSON object"])
            out.append(obj)
        return out

    def _read_head(self) -> dict | None:
        if not self.head_path.is_file():
            return None
        try:
            return json.loads(self.head_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"corrupt": True}

    # -- writing ----------------------------------------------------------

    def append(
        self,
        event: str,
        *,
        actor: str,
        file: str | None = None,
        env: str | None = None,
        sha256: str | None = None,
        detail: dict | None = None,
    ) -> dict:
        records = self.records()
        prev = records[-1]["record_sha256"] if records else GENESIS
        record: dict = {
            "seq": len(records),
            "event": event,
            "file": file,
            "env": env,
            "actor": actor,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sha256": sha256,
            "detail": detail or {},
            "prev": prev,
        }
        record["record_sha256"] = _record_hash(record)
        line = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        self._write_head(record)
        return record

    def _write_head(self, record: dict) -> None:
        payload = json.dumps(
            {"seq": record["seq"], "record_sha256": record["record_sha256"]},
            sort_keys=True,
            separators=(",", ":"),
        )
        self.head_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.head_path.parent), prefix=".audit-head-")
        try:
            os.write(fd, payload.encode("utf-8"))
            os.close(fd)
            os.replace(tmp, self.head_path)
        except BaseException:
            try:
                os.close(fd)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            raise

    # -- verification -----------------------------------------------------

    def verify(self, check_files: bool = False, root: Path | None = None) -> list[str]:
        """Return a list of violations (empty means the trail is intact)."""
        violations: list[str] = []
        try:
            records = self.records()
        except AuditError as exc:
            return exc.violations

        head = self._read_head()
        if head is not None and "corrupt" in head:
            violations.append(f"checkpoint {self.head_path.name} is not valid JSON")
            head = None

        if not records:
            if head is not None:
                violations.append("checkpoint exists but the audit log is empty (truncated?)")
            return violations
        if head is None:
            violations.append(
                f"checkpoint {self.head_path.name} missing while the audit log has "
                f"{len(records)} record(s) (possible truncation)"
            )

        last_hash = GENESIS
        expected_seq = 0
        last_seen: dict = {}
        for i, rec in enumerate(records, start=1):
            where = f"record #{i}"
            missing = [f for f in ("seq", "event", "actor", "ts", "prev", "record_sha256") if f not in rec]
            if missing:
                violations.append(f"{where}: missing field(s) {', '.join(missing)}")
                continue
            if rec["seq"] != expected_seq:
                violations.append(
                    f"{where}: sequence gap (expected seq {expected_seq}, found {rec['seq']})"
                )
                expected_seq = rec["seq"]
            if rec["prev"] != last_hash:
                violations.append(
                    f"{where}: prev link broken (chain tampering or a deleted record)"
                )
            if _record_hash(rec) != rec["record_sha256"]:
                violations.append(
                    f"{where}: record hash mismatch (content was modified after the fact)"
                )
            last_hash = rec["record_sha256"]
            expected_seq += 1
            if rec.get("file"):
                last_seen[rec["file"]] = rec

        last = records[-1]
        if head is not None:
            if head.get("seq") != last.get("seq") or head.get("record_sha256") != last.get(
                "record_sha256"
            ):
                violations.append(
                    f"checkpoint mismatch: log tail is seq {last.get('seq')} but the "
                    f"checkpoint says seq {head.get('seq')} (log was truncated)"
                )

        if check_files:
            base = Path(root) if root else self.path.parent.parent
            for rel, rec in last_seen.items():
                target = Path(rel) if os.path.isabs(rel) else base / rel
                if not target.is_file():
                    continue
                current = sha256_file(target)
                if rec.get("sha256") and current != rec["sha256"]:
                    violations.append(
                        f"{rel}: file content no longer matches the last audited sha256 "
                        "(modified without an audit event)"
                    )
        return violations
