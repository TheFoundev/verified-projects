"""`secretsync init` scaffolding."""

from __future__ import annotations

import os

from conftest import Repo, base_env, run_cli


def test_scaffolds_config_layout_and_samples(repo: Repo):
    assert (repo.root / ".sops.yaml").is_file()
    assert (repo.root / ".gitattributes").is_file()
    hook = repo.root / ".githooks" / "pre-commit"
    assert hook.is_file()
    if os.name != "nt":
        assert hook.stat().st_mode & 0o111, "hook must be executable"
    assert b"secretsync check" in hook.read_bytes()
    for env_name in ("dev", "staging"):
        sample = repo.root / "environments" / env_name / "secrets.enc.yaml"
        assert sample.is_file(), sample
        text = sample.read_text(encoding="utf-8")
        assert "ENC[" in text and "sops:" in text
        assert "change-me" not in text, "plaintext must not survive init"


def test_init_records_audit_events(repo: Repo):
    events = [rec["event"] for rec in repo.audit_records()]
    assert "init" in events
    assert events.count("encrypt") == 2  # dev + staging samples
    init_rec = next(r for r in repo.audit_records() if r["event"] == "init")
    assert init_rec["actor"].startswith("git:")
    assert init_rec["seq"] == 0
    assert init_rec["prev"] == "0" * 64


def test_local_keys_ignored_by_shipped_gitignore_patterns(repo: Repo):
    key = repo.key_path("dev")
    assert key.is_file()
    assert "AGE-SECRET-KEY" in key.read_text(encoding="utf-8")
    # mirror the shipped .gitignore and prove the identity never gets tracked
    repo.write(
        ".gitignore",
        "__pycache__/\n.pytest_cache/\n.tools/\n.secretsync/local-keys/\n*.dec.yaml\n.env\n",
    )
    repo.git("add", "-A")
    tracked = repo.git("ls-files").stdout.decode()
    assert ".secretsync/local-keys/" not in tracked
    assert "environments/dev/secrets.enc.yaml" in tracked


def test_second_init_refused_without_force(repo: Repo):
    result = repo.cli(["init"])
    assert result.returncode == 1
    assert b"already exists" in result.stderr
    forced = repo.cli(["init", "--force"])
    assert forced.returncode == 0


def test_no_keys_scaffold(tmp_path, tools):
    env = base_env(tools)
    root = tmp_path / "bare"
    root.mkdir()
    result = run_cli(["init", "--root", str(root), "--no-keys"], env, root)
    assert result.returncode == 0, result.stderr.decode()
    from secretsync.config import load_config

    cfg = load_config(root)
    assert cfg.envs() == ["dev", "staging", "prod"]
    assert not (root / "environments" / "dev" / "secrets.enc.yaml").exists()
    assert "placeholder" in result.stderr.decode()


def test_custom_envs(tmp_path, tools):
    env = base_env(tools)
    root = tmp_path / "single"
    root.mkdir()
    result = run_cli(["init", "--root", str(root), "--envs", "sandbox"], env, root)
    assert result.returncode == 0, result.stderr.decode()
    from secretsync.config import load_config

    cfg = load_config(root)
    assert cfg.envs() == ["sandbox"]
    assert (root / "environments" / "sandbox" / "secrets.enc.yaml").is_file()
