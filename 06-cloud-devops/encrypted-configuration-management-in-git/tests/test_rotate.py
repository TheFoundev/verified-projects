"""Key rotation: grace recipients, drop-previous, config persistence."""

from __future__ import annotations

import re

from conftest import Repo

RECIPIENT_RE = re.compile(r"recipient: (age1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{58})")


def file_recipients(repo: Repo, rel: str) -> list[str]:
    return RECIPIENT_RE.findall((repo.root / rel).read_text(encoding="utf-8"))


def test_rotation_keeps_old_recipient_in_grace(repo: Repo):
    rel = "environments/dev/secrets.enc.yaml"
    key_a = repo.key_path("dev")
    assert key_a.is_file()
    recipients_a = file_recipients(repo, rel)
    assert len(recipients_a) == 1

    # rotate: dev's public key is replaced with a brand new one
    key_b = repo.root.parent / "key-b.txt"
    recipients_b = repo.tools.gen_key(key_b)
    cfg = repo.config_path()
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(recipients_a[0], recipients_b),
        encoding="utf-8",
    )

    result = repo.cli(["rotate", "--env", "dev"])
    assert result.returncode == 0, result.stderr.decode()
    assert recipients_b in file_recipients(repo, rel)

    # old recipient moved into previous_recipients and kept in the ciphertext
    from secretsync.config import load_config

    cfg_after = load_config(repo.root)
    assert cfg_after.keys["dev"].recipients == [recipients_b]
    assert cfg_after.keys["dev"].previous_recipients == recipients_a
    assert cfg_after.keys["dev"].last_rotated is not None
    assert recipients_a[0] in file_recipients(repo, rel), "grace recipient must still decrypt"

    # both identities can decrypt during the grace period
    grace_ok = repo.cli(
        ["decrypt", rel, "--env", "dev"], **repo.ci_env(), SOPS_AGE_KEY_FILE=str(key_a)
    )
    assert grace_ok.returncode == 0, grace_ok.stderr.decode()
    new_ok = repo.cli(
        ["decrypt", rel, "--env", "dev"], **repo.ci_env(), SOPS_AGE_KEY_FILE=str(key_b)
    )
    assert new_ok.returncode == 0, new_ok.stderr.decode()

    # rotation recorded + chain intact
    rotates = [r for r in repo.audit_records() if r["event"] == "rotate"]
    assert len(rotates) == 1
    assert rotates[0]["detail"]["added_to_grace"] == recipients_a
    assert set(rotates[0]["detail"]["recipients"]) == {recipients_b, recipients_a[0]}
    verify = repo.cli(["audit", "verify"])
    assert verify.returncode == 0, verify.stderr.decode()


def test_drop_previous_ends_grace(repo: Repo):
    rel = "environments/dev/secrets.enc.yaml"
    key_a = repo.key_path("dev")
    recipients_a = file_recipients(repo, rel)[0]
    key_b = repo.root.parent / "key-b.txt"
    recipients_b = repo.tools.gen_key(key_b)
    cfg = repo.config_path()
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(recipients_a, recipients_b),
        encoding="utf-8",
    )
    assert repo.cli(["rotate", "--env", "dev"]).returncode == 0

    drop = repo.cli(["rotate", "--env", "dev", "--drop-previous"])
    assert drop.returncode == 0, drop.stderr.decode()
    assert b"grace recipients dropped" in drop.stdout

    assert file_recipients(repo, rel) == [recipients_b]
    from secretsync.config import load_config

    cfg_after = load_config(repo.root)
    assert cfg_after.keys["dev"].previous_recipients == []

    # old identity locked out, new identity works
    old = repo.cli(
        ["decrypt", rel, "--env", "dev"], **repo.ci_env(), SOPS_AGE_KEY_FILE=str(key_a)
    )
    assert old.returncode == 3
    new = repo.cli(
        ["decrypt", rel, "--env", "dev"], **repo.ci_env(), SOPS_AGE_KEY_FILE=str(key_b)
    )
    assert new.returncode == 0
    verify = repo.cli(["audit", "verify", "--check-files"])
    assert verify.returncode == 0, verify.stderr.decode()


def test_rotation_covers_all_env_files(repo: Repo):
    repo.write("environments/staging/extra.yaml", "token: staging-token\n")
    enc = repo.cli(["encrypt", "environments/staging/extra.yaml", "--env", "staging"])
    assert enc.returncode == 0, enc.stderr.decode()

    dev_before = file_recipients(repo, "environments/dev/secrets.enc.yaml")
    key_a = repo.key_path("staging")
    recipients_a = file_recipients(repo, "environments/staging/secrets.enc.yaml")[0]
    key_b = repo.root.parent / "staging-b.txt"
    recipients_b = repo.tools.gen_key(key_b)
    cfg = repo.config_path()
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(recipients_a, recipients_b),
        encoding="utf-8",
    )
    result = repo.cli(["rotate", "--env", "staging"])
    assert result.returncode == 0, result.stderr.decode()

    for rel in (
        "environments/staging/secrets.enc.yaml",
        "environments/staging/extra.enc.yaml",
    ):
        recs = file_recipients(repo, rel)
        assert recipients_b in recs and recipients_a in recs, rel

    rotates = [r for r in repo.audit_records() if r["event"] == "rotate"]
    assert len(rotates) == 2
    assert {r["file"] for r in rotates} == {
        "environments/staging/secrets.enc.yaml",
        "environments/staging/extra.enc.yaml",
    }
    # dev untouched
    assert file_recipients(repo, "environments/dev/secrets.enc.yaml") == dev_before
    assert repo.cli(["audit", "verify"]).returncode == 0


def test_rotate_refused_for_unknown_env(repo: Repo):
    result = repo.cli(["rotate", "--env", "qa"])
    assert result.returncode == 1
    assert b"unknown environment" in result.stderr


def test_rotate_refused_for_plaintext_file(repo: Repo):
    # a configured file that was replaced by plaintext cannot be rotated
    rel = repo.root / "environments" / "dev" / "secrets.enc.yaml"
    rel.write_text("db_password: leaked\n", encoding="utf-8")
    result = repo.cli(["rotate", "--env", "dev"])
    assert result.returncode == 1
    assert b"not encrypted" in result.stderr
