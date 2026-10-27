"""`secretsync encrypt`: ciphertext output, env rules and the plaintext guard."""

from __future__ import annotations

import hashlib

from conftest import Repo


def test_encrypt_writes_sibling_enc_file(repo: Repo):
    repo.write("environments/dev/db.yaml", "db_password: hunter2\napi_key: k-123\n")
    result = repo.cli(["encrypt", "environments/dev/db.yaml", "--env", "dev"])
    assert result.returncode == 0, result.stderr.decode()

    out = repo.root / "environments" / "dev" / "db.enc.yaml"
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "ENC[" in text and "sops:" in text
    assert "hunter2" not in text
    # recipients come from the dev creation rule
    from secretsync.config import load_config

    cfg = load_config(repo.root)
    for rec in cfg.keys["dev"].recipients:
        assert f"recipient: {rec}" in text
    # source left behind but clearly reported
    assert (repo.root / "environments" / "dev" / "db.yaml").is_file()

    # audit event carries the ciphertext sha256
    rec = [r for r in repo.audit_records() if r["event"] == "encrypt"][-1]
    assert rec["file"] == "environments/dev/db.enc.yaml"
    assert rec["env"] == "dev"
    assert rec["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert rec["actor"].startswith("git:")


def test_encrypt_in_place_for_enc_named_source(repo: Repo):
    repo.write("environments/dev/tmp_secret.yaml", "token: abc\n")
    # pre-create the .enc sibling path via --output to overwrite explicitly
    result = repo.cli(
        ["encrypt", "environments/dev/tmp_secret.yaml", "--env", "dev", "--output",
         "environments/dev/tmp_secret.enc.yaml"]
    )
    assert result.returncode == 0, result.stderr.decode()
    assert (repo.root / "environments" / "dev" / "tmp_secret.enc.yaml").is_file()


def test_refuses_when_other_plaintext_secrets_are_tracked(repo: Repo):
    repo.write("environments/dev/db.yaml", "api_key: k\n")
    repo.write("leftover.yaml", "password: supersecret\n")
    repo.git("add", "-A")
    assert repo.git("ls-files", "--error-unmatch", "leftover.yaml").returncode == 0

    result = repo.cli(["encrypt", "environments/dev/db.yaml", "--env", "dev"])
    assert result.returncode == 1
    stderr = result.stderr.decode()
    assert "plaintext secrets are present" in stderr
    assert "leftover.yaml:2:password" in stderr or "leftover.yaml" in stderr
    # nothing was written
    assert not (repo.root / "environments" / "dev" / "db.enc.yaml").exists()

    # once the offending file is gone from the index, encryption proceeds
    repo.git("rm", "-q", "--cached", "leftover.yaml")
    retry = repo.cli(["encrypt", "environments/dev/db.yaml", "--env", "dev"])
    assert retry.returncode == 0, retry.stderr.decode()


def test_refuses_already_encrypted_input(repo: Repo):
    result = repo.cli(
        ["encrypt", "environments/dev/secrets.enc.yaml", "--env", "dev"]
    )
    assert result.returncode == 1
    assert b"already contains ciphertext" in result.stderr


def test_refuses_foreign_environment(repo: Repo):
    repo.write("environments/staging/app.yaml", "token: x\n")
    result = repo.cli(["encrypt", "environments/staging/app.yaml", "--env", "dev"])
    assert result.returncode == 1
    assert b"belongs to environment 'staging'" in result.stderr


def test_refuses_file_outside_creation_rules(repo: Repo):
    repo.write("random.yaml", "some_key: x\n")
    result = repo.cli(["encrypt", "random.yaml", "--env", "dev"])
    assert result.returncode == 1
    assert b"matches no creation rule" in result.stderr


def test_refuses_existing_output_without_force(repo: Repo):
    repo.write("environments/dev/db.yaml", "token: a\n")
    assert repo.cli(["encrypt", "environments/dev/db.yaml", "--env", "dev"]).returncode == 0
    second = repo.cli(["encrypt", "environments/dev/db.yaml", "--env", "dev"])
    assert second.returncode == 1
    assert b"already exists" in second.stderr
    assert b"rotate" in second.stderr
