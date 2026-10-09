"""One source for model profiles: docs/model-profiles/model-profiles.json must agree with the packaged toml and the adapter documentation links."""
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


def test_adapter_docs_link_to_the_manifest_without_profile_tables():
    for doc in ("ag.md", "cc.md", "cx.md", "peer-cli-reference.md"):
        path = REPO / "docs" / "adapters" / doc
        text = path.read_text(encoding="utf-8")
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
        assert "../model-profiles/model-profiles.json" in links, doc
        assert (path.parent / "../model-profiles/model-profiles.json").resolve() == DIR / "model-profiles.json"
        assert not re.search(r"^\| (?:`(?:ag|cc|cx)\.\w+`|Peer \| `standard`)", text, re.MULTILINE), doc


def test_manifest_update_targets_exist():
    for rel in MANIFEST["update_targets"]:
        assert (REPO / rel).is_file(), rel
