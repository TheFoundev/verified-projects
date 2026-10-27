"""Configuration loading, validation and the YAML-subset parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from secretsync import miniyaml
from secretsync.config import (
    ConfigError,
    find_root,
    load_config,
    update_env_list,
    update_env_scalar,
)

REC_A = "age1" + "q" * 58
REC_B = "age1" + "p" * 58


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / ".sops.yaml"
    path.write_text(body, encoding="utf-8")
    return path


BASE = f"""\
version: 1
keys:
  dev:
    recipients:
      - {REC_A}
    previous_recipients: []
    rotation_reminder_days: 180
    last_rotated: 2026-01-15
  prod:
    recipients:
      - {REC_B}
    previous_recipients: []
    rotation_reminder_days: 30
    last_rotated: 2026-10-01
creation_rules:
  - env: dev
    path_regex: '^environments/dev/'
  - env: prod
    path_regex: '^environments/prod/'
files:
  - environments/dev/secrets.enc.yaml
decrypt_policy:
  require_ci: true
  ci_var: CI
  ci_truthy: "true"
  marker_var: SECRETSYNC_RUN_ID
  enforce_local_deny: true
"""


class TestMiniYaml:
    def test_scalars_and_nesting(self):
        doc = miniyaml.loads(
            "a: 1\nb: text\nc: true\nd: null\ne:\n  f: 2.5\ng: 'quoted # not comment'\nh: \"double\"\n"
        )
        assert doc == {
            "a": 1,
            "b": "text",
            "c": True,
            "d": None,
            "e": {"f": 2.5},
            "g": "quoted # not comment",
            "h": "double",
        }

    def test_flow_list_and_comments(self):
        doc = miniyaml.loads("# leading comment\nitems: [alpha, 'b', 3]  # trailing\nempty: []\n")
        assert doc == {"items": ["alpha", "b", 3], "empty": []}

    def test_block_sequence_and_inline_map(self):
        doc = miniyaml.loads("rules:\n  - env: dev\n    path_regex: x\n  - env: prod\n    path_regex: y\n")
        assert doc["rules"] == [
            {"env": "dev", "path_regex": "x"},
            {"env": "prod", "path_regex": "y"},
        ]

    def test_rejects_block_scalar(self):
        with pytest.raises(miniyaml.YamlError, match="block scalars"):
            miniyaml.loads("key: |\n  literal text\n")

    def test_rejects_duplicate_keys(self):
        with pytest.raises(miniyaml.YamlError, match="duplicate"):
            miniyaml.loads("a: 1\na: 2\n")

    def test_rejects_tabs(self):
        with pytest.raises(miniyaml.YamlError, match="tab"):
            miniyaml.loads("a:\n\tb: 1\n")


class TestLoadConfig:
    def test_loads_valid_config(self, tmp_path):
        write_config(tmp_path, BASE)
        cfg = load_config(tmp_path)
        assert cfg.envs() == ["dev", "prod"]
        assert cfg.keys["dev"].recipients == [REC_A]
        assert cfg.keys["dev"].rotation_reminder_days == 180
        assert cfg.keys["dev"].last_rotated == "2026-01-15"
        assert cfg.decrypt_policy.enforce_local_deny is True
        assert cfg.decrypt_policy.marker_var == "SECRETSYNC_RUN_ID"
        assert cfg.files == ["environments/dev/secrets.enc.yaml"]

    def test_resolve_env_and_files_for_env(self, tmp_path):
        write_config(tmp_path, BASE)
        cfg = load_config(tmp_path)
        assert cfg.resolve_env("environments/dev/secrets.enc.yaml") == "dev"
        assert cfg.resolve_env("environments/prod/x.enc.yaml") == "prod"
        assert cfg.resolve_env("random/file.yaml") is None
        assert cfg.files_for_env("dev") == ["environments/dev/secrets.enc.yaml"]
        assert cfg.files_for_env("prod") == []

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path)

    def test_missing_keys_section(self, tmp_path):
        write_config(tmp_path, "version: 1\ncreation_rules:\n  - env: dev\n    path_regex: x\n")
        with pytest.raises(ConfigError, match="keys"):
            load_config(tmp_path)

    def test_bad_recipient(self, tmp_path):
        write_config(tmp_path, BASE.replace(REC_A, "age1notakey"))
        with pytest.raises(ConfigError, match="not a valid age public key"):
            load_config(tmp_path)

    def test_unknown_env_in_rule(self, tmp_path):
        write_config(tmp_path, BASE.replace("  - env: prod\n", "  - env: staging\n"))
        with pytest.raises(ConfigError, match="unknown environment"):
            load_config(tmp_path)

    def test_overlapping_grace_recipients(self, tmp_path):
        write_config(tmp_path, BASE.replace("    previous_recipients: []", f"    previous_recipients: [{REC_A}]"))
        with pytest.raises(ConfigError, match="overlap"):
            load_config(tmp_path)

    def test_bad_rotation_reminder(self, tmp_path):
        write_config(tmp_path, BASE.replace("rotation_reminder_days: 180", "rotation_reminder_days: 0"))
        with pytest.raises(ConfigError, match="rotation_reminder_days"):
            load_config(tmp_path)

    def test_invalid_regex(self, tmp_path):
        write_config(tmp_path, BASE.replace("'^environments/dev/'", "'('"))
        with pytest.raises(ConfigError, match="invalid regex"):
            load_config(tmp_path)

    def test_absolute_file_path_rejected(self, tmp_path):
        write_config(tmp_path, BASE.replace("files:\n  - environments", "files:\n  - /etc/passwd"))
        with pytest.raises(ConfigError, match="relative path"):
            load_config(tmp_path)


class TestFindRoot:
    def test_walks_up(self, tmp_path):
        write_config(tmp_path, BASE)
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert find_root(nested) == tmp_path.resolve()

    def test_explicit_root(self, tmp_path):
        write_config(tmp_path, BASE)
        assert find_root(explicit=str(tmp_path)) == tmp_path.resolve()

    def test_missing_root(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            find_root(tmp_path)


class TestConfigEditing:
    """`rotate` persists previous_recipients / last_rotated line-wise."""

    def _roundtrip(self, tmp_path) -> None:
        path = write_config(tmp_path, BASE)
        cfg = load_config(path.parent)
        assert cfg.keys["dev"].previous_recipients == []

    def test_update_list_and_scalar(self, tmp_path):
        path = write_config(tmp_path, BASE)
        update_env_list(path, "dev", "previous_recipients", [REC_B])
        update_env_scalar(path, "dev", "last_rotated", "2026-10-20")
        cfg = load_config(path.parent)
        assert cfg.keys["dev"].previous_recipients == [REC_B]
        assert cfg.keys["dev"].last_rotated == "2026-10-20"
        # other env untouched
        assert cfg.keys["prod"].previous_recipients == []
        assert cfg.keys["prod"].last_rotated == "2026-10-01"

    def test_clear_list_to_inline_empty(self, tmp_path):
        path = write_config(tmp_path, BASE)
        update_env_list(path, "dev", "previous_recipients", [REC_B])
        update_env_list(path, "dev", "previous_recipients", [])
        cfg = load_config(path.parent)
        assert cfg.keys["dev"].previous_recipients == []
        # no duplicate keys / no leftover block items
        assert path.read_text(encoding="utf-8").count("previous_recipients") == 2

    def test_repeat_updates_do_not_duplicate_keys(self, tmp_path):
        path = write_config(tmp_path, BASE)
        for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
            update_env_scalar(path, "dev", "last_rotated", day)
        text = path.read_text(encoding="utf-8")
        assert text.count("last_rotated") == 2  # dev + prod
        cfg = load_config(path.parent)
        assert cfg.keys["dev"].last_rotated == "2026-10-03"

    def test_update_inline_list(self, tmp_path):
        # dev starts with an inline flow list; the editor converts it to a block list
        write_config(tmp_path, BASE.replace("previous_recipients: []", f"previous_recipients: [{REC_B}]", 1))
        path = tmp_path / ".sops.yaml"
        rec_c = "age1" + "z" * 58
        update_env_list(path, "dev", "previous_recipients", [rec_c])
        cfg = load_config(path.parent)
        assert cfg.keys["dev"].previous_recipients == [rec_c]
        assert cfg.keys["prod"].previous_recipients == []

    def test_roundtrip_helper(self, tmp_path):
        # ensure the canonical init layout survives a no-op load
        write_config(tmp_path, BASE)
        self._roundtrip(tmp_path)
