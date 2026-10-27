"""The CI gate: decryption only when CI is detected and the marker matches."""

from __future__ import annotations

from conftest import Repo


def test_refused_outside_ci(repo: Repo):
    proc = repo.cli(["decrypt", "environments/dev/secrets.enc.yaml", "--env", "dev"])
    assert proc.returncode == 1
    assert b"not running in CI" in proc.stderr
    assert proc.stdout == b""
    # the refusal itself is audited
    denied = [r for r in repo.audit_records() if r["event"] == "decrypt_denied"]
    assert len(denied) == 1
    assert denied[0]["detail"]["allow_local"] is False
    assert denied[0]["file"] == "environments/dev/secrets.enc.yaml"
    assert denied[0]["env"] == "dev"


def test_refused_in_ci_without_marker(repo: Repo):
    proc = repo.cli(
        ["decrypt", "environments/dev/secrets.enc.yaml", "--env", "dev"], CI="true"
    )
    assert proc.returncode == 1
    assert b"SECRETSYNC_RUN_ID" in proc.stderr
    assert b"missing or empty" in proc.stderr


def test_refused_when_marker_present_but_ci_flag_missing(repo: Repo):
    proc = repo.cli(
        ["decrypt", "environments/dev/secrets.enc.yaml", "--env", "dev"],
        SECRETSYNC_RUN_ID="run-1",
    )
    assert proc.returncode == 1
    assert b"not running in CI" in proc.stderr


def test_allowed_in_ci_with_marker(repo: Repo):
    proc = repo.cli(
        ["decrypt", "environments/dev/secrets.enc.yaml", "--env", "dev"], **repo.ci_env()
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert b"db_password: change-me-dev" in proc.stdout
    assert b"sops:" not in proc.stdout

    decrypts = [r for r in repo.audit_records() if r["event"] == "decrypt"]
    assert len(decrypts) == 1
    rec = decrypts[0]
    assert rec["detail"]["mode"] == "ci"
    assert rec["actor"] == "ci:github:foundev-bot:12345"
    assert rec["env"] == "dev"
    assert rec["sha256"]  # ciphertext hash recorded


def test_decrypt_to_output_file(repo: Repo):
    out_file = repo.root.parent / "decrypted.yaml"
    proc = repo.cli(
        [
            "decrypt",
            "environments/dev/secrets.enc.yaml",
            "--env",
            "dev",
            "--output",
            str(out_file),
        ],
        **repo.ci_env(),
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert out_file.is_file()
    assert b"api_key: demo-dev-key" in out_file.read_bytes()
    # status goes to stderr so stdout can stay reserved for plaintext
    assert b"decrypted" in proc.stderr


def test_allow_local_refused_when_enforce_local_deny(repo: Repo):
    proc = repo.cli(
        ["decrypt", "environments/dev/secrets.enc.yaml", "--env", "dev", "--allow-local"]
    )
    assert proc.returncode == 1
    assert b"enforce_local_deny" in proc.stderr
    denied = [r for r in repo.audit_records() if r["event"] == "decrypt_denied"]
    assert denied[-1]["detail"]["allow_local"] is True
    assert denied[-1]["detail"]["mode"] == "denied"


def test_allow_local_works_when_policy_permits(repo: Repo):
    # flip enforce_local_deny off (legitimate developer laptop policy)
    cfg = repo.config_path()
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            "enforce_local_deny: true", "enforce_local_deny: false"
        ),
        encoding="utf-8",
    )
    proc = repo.cli(
        ["decrypt", "environments/dev/secrets.enc.yaml", "--env", "dev", "--allow-local"]
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert b"db_password: change-me-dev" in proc.stdout
    rec = [r for r in repo.audit_records() if r["event"] == "decrypt"][-1]
    assert rec["detail"]["mode"] == "local"
    assert rec["actor"].startswith("git:")


def test_wrong_identity_fails_with_tool_error(repo: Repo, tmp_path):
    # an identity that cannot decrypt a file sealed to the dev recipient
    repo.tools.gen_key(tmp_path / "stranger.key")
    proc = repo.cli(
        ["decrypt", "environments/dev/secrets.enc.yaml", "--env", "dev"],
        **repo.ci_env(),
        SOPS_AGE_KEY_FILE=str(tmp_path / "stranger.key"),
    )
    assert proc.returncode == 3
    assert b"decrypt" in proc.stderr.lower()
    # the failure is audited as a denial carrying the sops error
    denied = [r for r in repo.audit_records() if r["event"] == "decrypt_denied"]
    assert denied and "sops" in denied[-1]["detail"]["reason"]
