"""Integrity of the artifacts committed in THIS repository (read-only).

The shipped ciphertext, config, hook and CI example were produced by
secretsync itself during the build; these tests re-verify them without
modifying anything.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from secretsync.check import is_encrypted
from secretsync.config import load_config
from secretsync.gitutil import tracked_files

ROOT = Path(__file__).resolve().parents[1]
RECIPIENT_RE = re.compile(r"recipient: (age1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{58})")


def test_project_config_loads():
    cfg = load_config(ROOT)
    assert cfg.envs() == ["dev", "staging", "prod"]
    assert cfg.decrypt_policy.require_ci is True
    assert cfg.decrypt_policy.enforce_local_deny is True
    assert cfg.decrypt_policy.marker_var == "SECRETSYNC_RUN_ID"
    for env in cfg.envs():
        assert cfg.keys[env].recipients
        assert cfg.keys[env].rotation_reminder_days > 0


def test_all_managed_files_exist_and_are_encrypted():
    cfg = load_config(ROOT)
    assert cfg.files, "config must manage at least the environment samples"
    for rel in cfg.files:
        path = ROOT / rel
        assert path.is_file(), f"missing managed file: {rel}"
        assert is_encrypted(path), f"not ciphertext: {rel}"
        assert cfg.resolve_env(rel) is not None, f"no creation rule for {rel}"


def test_ciphertext_recipients_match_config():
    cfg = load_config(ROOT)
    allowed = set()
    for keys in cfg.keys.values():
        allowed |= set(keys.recipients)
        allowed |= set(keys.previous_recipients)
    for rel in cfg.files:
        text = (ROOT / rel).read_text(encoding="utf-8")
        found = set(RECIPIENT_RE.findall(text))
        assert found, f"no age recipients in {rel}"
        assert found <= allowed, f"{rel} encrypted to unknown recipients: {found - allowed}"


def test_ansible_secrets_covered():
    cfg = load_config(ROOT)
    for env in ("dev", "staging", "prod"):
        rel = f"ansible/secrets/{env}.secrets.enc.yaml"
        assert rel in cfg.files, f"{rel} must be listed in config `files`"
        assert cfg.resolve_env(rel) == env
        assert is_encrypted(ROOT / rel)


def test_no_plaintext_or_key_material_tracked():
    cfg = load_config(ROOT)
    tracked = tracked_files(ROOT)
    if tracked is None:
        pytest.skip("the portfolio copy is not a git repository")
    from secretsync.plaintext import scan_paths

    findings = scan_paths(ROOT, tracked, cfg.plaintext_patterns, cfg.scan_exclude)
    assert findings == [], f"plaintext secrets tracked: {findings}"
    for rel in tracked:
        assert ".dec." not in Path(rel).name, f"decrypted copy tracked: {rel}"
        assert not rel.startswith(".secretsync/local-keys/"), f"key tracked: {rel}"
        assert not rel.startswith(".tools/"), f"binary tracked: {rel}"


def test_ci_example_shape():
    text = (ROOT / ".github" / "workflows-example" / "ci.yml").read_text(encoding="utf-8")
    assert "secretsync decrypt" in text
    assert "secretsync audit verify" in text
    assert "secretsync check" in text
    assert "SECRETSYNC_RUN_ID" in text
    assert "SOPS_AGE_KEY_FILE" in text
    assert "${{ secrets.SOPS_AGE_KEY }}" in text  # identity comes from CI secrets


def test_hook_and_gitattributes():
    hook = ROOT / ".githooks" / "pre-commit"
    assert hook.is_file()
    if os.name != "nt":
        assert hook.stat().st_mode & 0o111
    assert b"secretsync check" in hook.read_bytes()
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.enc.yaml diff=sops" in attrs
    assert "*.dec.yaml -text" in attrs


def test_ansible_playbook_documented_shape():
    text = (ROOT / "ansible" / "playbook.yml").read_text(encoding="utf-8")
    assert "secretsync" in text and "include_vars" in text
    assert "always:" in text and "state: absent" in text  # shred step
