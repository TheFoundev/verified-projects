"""Audit chain: append, verify, tamper/truncation detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import Repo
from secretsync.audit import GENESIS, AuditLog, sha256_bytes


def make_log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / ".secretsync" / "audit.log")


def append_events(log: AuditLog, count: int = 3) -> None:
    for i in range(count):
        log.append(
            "encrypt",
            actor=f"git:test <t@e.invalid>",
            file=f"environments/dev/f{i}.enc.yaml",
            env="dev",
            sha256="a" * 64 if i % 2 else "b" * 64,
            detail={"n": i},
        )


class TestChain:
    def test_fresh_chain_verifies(self, tmp_path):
        log = make_log(tmp_path)
        append_events(log)
        assert log.verify() == []
        records = log.records()
        assert [r["seq"] for r in records] == [0, 1, 2]
        assert records[0]["prev"] == GENESIS
        assert records[1]["prev"] == records[0]["record_sha256"]
        assert records[2]["prev"] == records[1]["record_sha256"]

    def test_empty_log_is_fine(self, tmp_path):
        assert make_log(tmp_path).verify() == []

    def test_tampered_field_detected(self, tmp_path):
        log = make_log(tmp_path)
        append_events(log)
        lines = log.path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[1])
        record["env"] = "prod"  # attacker rewrites history in place
        lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
        log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        violations = log.verify()
        assert any("record hash mismatch" in v for v in violations)

        # forging the stored chain hash as well breaks the following link
        record = json.loads(lines[1])
        record["record_sha256"] = "0" * 64
        lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
        log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        violations = log.verify()
        assert any("record hash mismatch" in v for v in violations)
        assert any("prev link broken" in v for v in violations)

    def test_tail_truncation_detected_via_checkpoint(self, tmp_path):
        log = make_log(tmp_path)
        append_events(log, count=4)
        lines = log.path.read_text(encoding="utf-8").splitlines()
        log.path.write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")

        violations = log.verify()
        assert any("truncated" in v for v in violations), violations

    def test_missing_checkpoint_detected(self, tmp_path):
        log = make_log(tmp_path)
        append_events(log)
        log.head_path.unlink()
        violations = log.verify()
        assert any("checkpoint" in v and "missing" in v for v in violations)

    def test_middle_deletion_detected(self, tmp_path):
        log = make_log(tmp_path)
        append_events(log, count=4)
        lines = log.path.read_text(encoding="utf-8").splitlines()
        del lines[1]
        log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        violations = log.verify()
        assert any("sequence gap" in v for v in violations)
        assert any("prev link broken" in v for v in violations)

    def test_forged_appended_record_detected(self, tmp_path):
        log = make_log(tmp_path)
        append_events(log)
        forged = {
            "seq": 3,
            "event": "decrypt",
            "file": "x",
            "env": "prod",
            "actor": "evil",
            "ts": "2026-01-01T00:00:00Z",
            "sha256": None,
            "detail": {},
            "prev": "f" * 64,
            "record_sha256": "0" * 64,
        }
        with log.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n")
        violations = log.verify()
        assert any("prev link broken" in v for v in violations)
        assert any("record hash mismatch" in v for v in violations)

    def test_garbage_line_detected(self, tmp_path):
        log = make_log(tmp_path)
        append_events(log)
        with log.path.open("a", encoding="utf-8") as fh:
            fh.write("not json at all\n")
        violations = log.verify()
        assert any("invalid JSON" in v for v in violations)

    def test_check_files_detects_silent_ciphertext_edit(self, tmp_path):
        log = make_log(tmp_path)
        target = tmp_path / "environments" / "dev" / "a.enc.yaml"
        target.parent.mkdir(parents=True)
        original = b"sops:\nENC[AES256_GCM,data:x]\n"
        target.write_bytes(original)
        log.append(
            "encrypt",
            actor="git:test",
            file="environments/dev/a.enc.yaml",
            env="dev",
            sha256=sha256_bytes(original),
        )
        assert log.verify(check_files=True, root=tmp_path) == []

        target.write_bytes(b"sops:\nENC[AES256_GCM,data:mutated]\n")
        violations = log.verify(check_files=True, root=tmp_path)
        assert any("modified without an audit event" in v for v in violations)


class TestAuditCli:
    def test_cli_verify_ok_and_listing(self, repo: Repo):
        ok = repo.cli(["audit", "verify"])
        assert ok.returncode == 0, ok.stderr.decode()
        assert b"audit chain OK" in ok.stdout

        listing = repo.cli(["audit"])
        assert listing.returncode == 0
        assert b"init" in listing.stdout and b"encrypt" in listing.stdout

        checked = repo.cli(["audit", "verify", "--check-files"])
        assert checked.returncode == 0, checked.stderr.decode()
        assert b"file hashes checked" in checked.stdout

    def test_cli_verify_fails_after_tamper(self, repo: Repo):
        log_path = repo.root / ".secretsync" / "audit.log"
        lines = log_path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[-1])
        record["actor"] = "someone-else"
        lines[-1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = repo.cli(["audit", "verify"])
        assert result.returncode == 1
        assert b"FAILED" in result.stderr
        assert b"record hash mismatch" in result.stderr

    def test_cli_verify_fails_after_truncation(self, repo: Repo):
        log_path = repo.root / ".secretsync" / "audit.log"
        lines = log_path.read_text(encoding="utf-8").splitlines()
        log_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

        result = repo.cli(["audit", "verify"])
        assert result.returncode == 1
        assert b"truncated" in result.stderr

    def test_cli_listing_on_empty_log(self, tmp_path, tools):
        from conftest import base_env, run_cli

        env = base_env(tools)
        root = tmp_path / "bare"
        root.mkdir()
        init = run_cli(["init", "--root", str(root), "--no-keys", "--no-samples"], env, root)
        assert init.returncode == 0, init.stderr.decode()
        # remove the audit log entirely: listing should say so, verify should pass
        (root / ".secretsync" / "audit.log").unlink()
        (root / ".secretsync" / "audit.head").unlink()
        listing = run_cli(["audit"], env, root)
        assert listing.returncode == 0
        assert b"empty" in listing.stdout
        verify = run_cli(["audit", "verify"], env, root)
        assert verify.returncode == 0
