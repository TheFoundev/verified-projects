"""`secretsync check`: the pre-commit gate in throwaway git repositories."""

from __future__ import annotations

import os

import pytest

from conftest import Repo


def run_check(repo: Repo):
    return repo.cli(["check"])


def test_clean_repo_passes(repo: Repo):
    repo.git("add", "-A")
    result = run_check(repo)
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    assert b"check: OK" in result.stdout


def test_plaintext_secret_in_tracked_file_fails(repo: Repo):
    repo.write("config/prod.yaml", "db_password: hunter2\n")
    repo.git("add", "-A")
    result = run_check(repo)
    assert result.returncode == 1
    assert b"plaintext secret in tracked file" in result.stdout
    assert b"db_password" in result.stdout


def test_api_key_pattern_detected(repo: Repo):
    repo.write("settings.yaml", "api_key: sk-live-123\n")
    repo.git("add", "-A")
    result = run_check(repo)
    assert result.returncode == 1
    assert b"api_key" in result.stdout


def test_tracked_decrypted_copy_fails(repo: Repo):
    repo.write("environments/dev/secrets.dec.yaml", "anything: goes\n")
    # .dec.yaml is gitignored by convention, so prove detection even when
    # someone force-adds one
    repo.git("add", "-f", "environments/dev/secrets.dec.yaml")
    assert b"secrets.dec.yaml" in repo.git("ls-files").stdout
    result = run_check(repo)
    assert result.returncode == 1
    assert b"must never be tracked" in result.stdout


def test_tracked_key_material_fails(repo: Repo):
    repo.write("identity.txt", "# public key: age1x\nAGE-SECRET-KEY-1ABCDEF\n")
    repo.git("add", "-A")
    result = run_check(repo)
    assert result.returncode == 1
    assert b"age private key material" in result.stdout


def test_tracked_key_extension_fails(repo: Repo):
    repo.write("ops/dev.key", "AGE-SECRET-KEY-1ABCDEF\n")
    repo.git("add", "-A")
    result = run_check(repo)
    assert result.returncode == 1
    assert b"key-like file is tracked" in result.stdout


def test_unencrypted_configured_file_fails(repo: Repo):
    sample = repo.root / "environments" / "dev" / "secrets.enc.yaml"
    sample.write_text("db_password: leaked-in-plaintext\n", encoding="utf-8")
    repo.git("add", "-A")
    result = run_check(repo)
    assert result.returncode == 1
    out = result.stdout.decode()
    assert "NOT encrypted" in out
    assert "environments/dev/secrets.enc.yaml" in out


def test_missing_configured_file_fails(repo: Repo):
    (repo.root / "environments" / "staging" / "secrets.enc.yaml").unlink()
    result = run_check(repo)
    assert result.returncode == 1
    assert b"missing configured secrets file" in result.stdout


def test_unmanaged_encrypted_file_fails(repo: Repo):
    src = repo.root / "environments" / "dev" / "secrets.enc.yaml"
    stray = repo.root / "misc" / "stray.enc.yaml"
    stray.parent.mkdir()
    stray.write_bytes(src.read_bytes())
    repo.git("add", "-A")
    result = run_check(repo)
    assert result.returncode == 1
    assert b"not covered by any creation rule" in result.stdout


def test_invalid_config_fails(repo: Repo):
    cfg = repo.config_path()
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("version: 1", "version: 9"), "utf-8")
    result = run_check(repo)
    assert result.returncode == 1
    assert b"unsupported config version" in result.stdout


def test_tampered_audit_fails_check(repo: Repo):
    log_path = repo.root / ".secretsync" / "audit.log"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"init"', '"boot"')
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = run_check(repo)
    assert result.returncode == 1
    assert b"audit:" in result.stdout


def test_rotation_overdue_warns_but_passes(repo: Repo):
    from secretsync.config import update_env_scalar

    update_env_scalar(repo.config_path(), "dev", "last_rotated", "2020-01-01")
    repo.git("add", "-A")
    result = run_check(repo)
    assert result.returncode == 0, result.stdout.decode()
    assert b"warning:" in result.stdout
    assert b"exceeds the" in result.stdout


def test_non_git_directory_warns_but_passes(repo: Repo):
    # remove the .git directory: tracked checks fall back to a filesystem walk
    import shutil

    shutil.rmtree(repo.root / ".git")
    result = run_check(repo)
    assert result.returncode == 0, result.stdout.decode()
    assert b"fell back to a filesystem walk" in result.stdout


def test_hook_invokes_check(repo: Repo):
    hook = repo.root / ".githooks" / "pre-commit"
    import shutil
    import subprocess

    if os.name == "nt":
        pytest.skip("the hook is a POSIX shell script; run it on Linux or macOS")

    proc = subprocess.run(
        ["sh", str(hook)],
        cwd=str(repo.root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=repo.env,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout.decode() + proc.stderr.decode()
    assert b"check: OK" in proc.stdout
