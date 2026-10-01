"""Keep docs/model-profiles/model-profiles.json, the packaged toml and the
adapter doc tables in sync (the manifest is the AI-facing refresh contract)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs" / "model-profiles" / "model-profiles.json"
SCHEMA = ROOT / "docs" / "model-profiles" / "model-profiles.schema.json"
TOML = ROOT / "peerhub" / "config_data" / "model-defaults.toml"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_matches_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(_manifest(), json.loads(SCHEMA.read_text(encoding="utf-8")))


def test_manifest_matches_packaged_defaults() -> None:
    manifest = _manifest()
    packaged = tomllib.loads(TOML.read_text(encoding="utf-8"))
    assert manifest["revision"] == packaged["defaults_revision"]
    for profile_id, expected in manifest["profiles"].items():
        entry = packaged["profiles"][profile_id]
        assert entry["model"] == expected["model"], profile_id
        assert entry.get("reasoning_effort") == expected.get("reasoning_effort"), profile_id


def test_manifest_matches_doc_tables() -> None:
    manifest = _manifest()
    docs = {
        peer: (ROOT / "docs" / "adapters" / f"{name}.md").read_text(encoding="utf-8").splitlines()
        for peer, name in (("ag", "ag"), ("cc", "cc"), ("cx", "cx"))
    }
    reference = (ROOT / "docs" / "adapters" / "peer-cli-reference.md").read_text(
        encoding="utf-8"
    ).splitlines()
    for profile_id, expected in manifest["profiles"].items():
        peer = profile_id.split(".")[0]
        row = next(line for line in docs[peer] if line.startswith(f"| `{profile_id}` |"))
        assert f"`{expected['model']}`" in row, (profile_id, row)
        if "reasoning_effort" in expected:
            assert f"`{expected['reasoning_effort']}`" in row, (profile_id, row)
        ref_row = next(
            line
            for line in reference
            if line.startswith(f"| `{peer}` | `") and "agy.exe" not in line
            and "claude.cmd" not in line and "codex.cmd" not in line
        )
        assert f"`{expected['model']}`" in ref_row, (profile_id, ref_row)


def test_update_targets_exist() -> None:
    for target in _manifest()["update_targets"]:
        assert (ROOT / target).is_file(), target
