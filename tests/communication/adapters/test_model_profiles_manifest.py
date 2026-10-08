"""One source for model profiles: docs/model-profiles/model-profiles.json must agree with the packaged toml and the adapter doc tables."""
import json
import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DIR = REPO / "docs" / "model-profiles"
MANIFEST = json.loads((DIR / "model-profiles.json").read_text(encoding="utf-8"))
TOML = tomllib.loads((REPO / "peerhub" / "config_data" / "model-defaults.toml").read_text(encoding="utf-8"))


def test_manifest_is_valid_against_its_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(MANIFEST, json.loads((DIR / "model-profiles.schema.json").read_text(encoding="utf-8")))


def test_manifest_equals_the_packaged_toml_profile_by_profile():
    assert MANIFEST["revision"] == TOML["defaults_revision"]
    packaged = {k: v for k, v in TOML["profiles"].items() if k != "*"}
    assert set(packaged) == set(MANIFEST["profiles"])
    for pid, want in MANIFEST["profiles"].items():
        got = packaged[pid]
        assert got["selection_mode"] == "pinned" and got["model"] == want["model"], pid
        assert got.get("reasoning_effort") == want.get("reasoning_effort"), pid


def test_adapter_doc_tables_show_the_manifest_values():
    rows = {}
    for doc in ("ag.md", "cc.md", "cx.md"):
        for line in (REPO / "docs" / "adapters" / doc).read_text(encoding="utf-8").splitlines():
            m = re.match(r"\| `((?:ag|cc|cx)\.\w+)` \| `([^`]+)` \| (.+?) \|$", line)
            if m:
                rows[m.group(1)] = (m.group(2), m.group(3))
    assert set(rows) == set(MANIFEST["profiles"])
    for pid, want in MANIFEST["profiles"].items():
        model, effort_cell = rows[pid]
        assert model == want["model"], pid
        assert (f"`{want['reasoning_effort']}`" in effort_cell) if "reasoning_effort" in want else ("`" not in effort_cell), pid


def test_reference_table_and_update_targets_are_consistent():
    reference = (REPO / "docs" / "adapters" / "peer-cli-reference.md").read_text(encoding="utf-8")
    for pid, want in MANIFEST["profiles"].items():
        assert f"`{want['model']}`" in reference, pid
    for rel in MANIFEST["update_targets"]:
        assert (REPO / rel).is_file(), rel
