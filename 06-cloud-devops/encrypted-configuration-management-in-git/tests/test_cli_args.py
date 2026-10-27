"""CLI argument handling and exit-code contract (no side effects)."""

from __future__ import annotations

from conftest import Repo, run_cli


def test_no_arguments_prints_usage(repo: Repo):
    result = run_cli([], repo.env, repo.root)
    assert result.returncode == 2
    assert b"usage" in result.stderr.lower()


def test_unknown_command_rejected(repo: Repo):
    result = run_cli(["frobnicate"], repo.env, repo.root)
    assert result.returncode == 2
    assert b"invalid choice" in result.stderr


def test_encrypt_requires_env(repo: Repo):
    result = run_cli(
        ["encrypt", "environments/dev/secrets.enc.yaml"], repo.env, repo.root
    )
    assert result.returncode == 2
    assert b"--env" in result.stderr


def test_decrypt_requires_env(repo: Repo):
    result = run_cli(["decrypt", "x.enc.yaml"], repo.env, repo.root)
    assert result.returncode == 2
    assert b"--env" in result.stderr


def test_help_and_version(repo: Repo):
    help_result = run_cli(["--help"], repo.env, repo.root)
    assert help_result.returncode == 0
    assert b"encrypt" in help_result.stdout and b"rotate" in help_result.stdout

    version_result = run_cli(["--version"], repo.env, repo.root)
    assert version_result.returncode == 0
    assert b"secretsync" in version_result.stdout


def test_subcommand_help(repo: Repo):
    result = run_cli(["decrypt", "--help"], repo.env, repo.root)
    assert result.returncode == 0
    assert b"--allow-local" in result.stdout


def test_unknown_environment_refused(repo: Repo):
    result = repo.cli(["decrypt", "environments/dev/secrets.enc.yaml", "--env", "qa"])
    assert result.returncode == 1
    assert b"unknown environment" in result.stderr


def test_missing_file_refused(repo: Repo):
    result = repo.cli(["encrypt", "environments/dev/nope.yaml", "--env", "dev"])
    assert result.returncode == 1
    assert b"does not exist" in result.stderr


def test_environment_mismatch_refused(repo: Repo):
    # dev file asked for with --env staging
    result = repo.cli(["decrypt", "environments/dev/secrets.enc.yaml", "--env", "staging"])
    assert result.returncode == 1
    assert b"belongs to environment 'dev'" in result.stderr


def test_root_flag_points_elsewhere(repo: Repo, tmp_path):
    result = run_cli(
        ["check", "--root", str(repo.root)], repo.env, tmp_path
    )
    assert result.returncode == 0, result.stderr.decode()
    assert b"check: OK" in result.stdout
