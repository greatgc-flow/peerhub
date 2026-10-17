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
    assert MANIFEST["observed_at"] == TOML["observed_at"]
    assert MANIFEST["observed_versions"] == TOML["observed_versions"]
    packaged = {k: v for k, v in TOML["profiles"].items() if k != "*"}
    assert set(packaged) == set(MANIFEST["profiles"])
    for pid, want in MANIFEST["profiles"].items():
        got = packaged[pid]
        assert got["selection_mode"] == "pinned" and got["model"] == want["model"], pid
        assert got.get("reasoning_effort") == want.get("reasoning_effort"), pid
        assert got.get("pools") == want.get("pools"), pid
        assert got.get("source") == want.get("source"), pid
        assert got.get("checked") == want.get("checked"), pid


def test_manifest_and_toml_every_pool_id_exists_in_probe_constants():
    from peerhub.extensions.quota_probes import AGY_QUOTA_FAMILIES, _CLAUDE_USAGE_SECTIONS

    ag_pools = {v for _, v in AGY_QUOTA_FAMILIES}
    cc_pools = {v[0] for v in _CLAUDE_USAGE_SECTIONS.values()}
    cx_pools = {"X-5H", "X-7D"}
    all_known_pools = ag_pools | cc_pools | cx_pools

    for source_name, prof_dict in [("manifest", MANIFEST["profiles"]), ("toml", TOML["profiles"])]:
        for pid, p in prof_dict.items():
            pools = p.get("pools")
            assert isinstance(pools, list) and len(pools) > 0, f"{source_name} {pid} missing pools"
            for pool_id in pools:
                assert pool_id in all_known_pools, f"{source_name} {pid} pool {pool_id!r} not in known probe constants"


def test_manifest_and_toml_source_and_date_present():
    date_pat = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for source_name, prof_dict in [("manifest", MANIFEST["profiles"]), ("toml", TOML["profiles"])]:
        for pid, p in prof_dict.items():
            source = p.get("source")
            checked = p.get("checked")
            assert isinstance(source, str) and len(source) > 0, f"{source_name} {pid} missing source"
            assert isinstance(checked, str) and date_pat.match(checked), f"{source_name} {pid} invalid checked date: {checked!r}"


def test_manifest_pools_evidenced_mappings():
    assert MANIFEST["profiles"]["ag.standard"]["pools"] == ["G-5H", "G-7D"]
    assert MANIFEST["profiles"]["ag.effort"]["pools"] == ["G-5H", "G-7D"]
    assert MANIFEST["profiles"]["ag.deepthink"]["pools"] == ["3P-5H", "3P-7D"]
    assert MANIFEST["profiles"]["cc.standard"]["pools"] == ["C-5H", "C-7D"]
    assert MANIFEST["profiles"]["cc.effort"]["pools"] == ["C-5H", "C-7D"]
    assert MANIFEST["profiles"]["cc.deepthink"]["pools"] == ["C-5H", "C-7D"]
    assert MANIFEST["profiles"]["cx.standard"]["pools"] == ["X-5H", "X-7D"]
    assert MANIFEST["profiles"]["cx.effort"]["pools"] == ["X-5H", "X-7D"]
    assert MANIFEST["profiles"]["cx.deepthink"]["pools"] == ["X-5H", "X-7D"]


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


def test_packaged_defaults_revision_and_expected_models():
    assert TOML["defaults_revision"] == "2026-10-16.1"
    assert TOML["observed_at"] == "2026-10-16"
    profiles = TOML["profiles"]
    assert len(profiles) == 9
    assert profiles["ag.standard"]["model"] == "gemini-3.8-flash-low"
    assert profiles["ag.effort"]["model"] == "gemini-3.8-flash-medium"
    assert profiles["ag.deepthink"]["model"] == "claude-sonnet-5-5-high"
    assert profiles["cc.standard"]["model"] == "claude-haiku-5-5"
    assert profiles["cc.effort"]["model"] == "claude-sonnet-5-5"
    assert profiles["cc.effort"].get("reasoning_effort") == "high"
    assert profiles["cc.deepthink"]["model"] == "claude-opus-5-5"
    assert profiles["cc.deepthink"].get("reasoning_effort") == "high"
    assert profiles["cx.standard"]["model"] == "gpt-6-luna"
    assert profiles["cx.standard"].get("reasoning_effort") == "low"
    assert profiles["cx.effort"]["model"] == "gpt-6.1-sol"
    assert profiles["cx.effort"].get("reasoning_effort") == "medium"
    assert profiles["cx.deepthink"]["model"] == "gpt-6.1-sol"
    assert profiles["cx.deepthink"].get("reasoning_effort") == "xhigh"
    assert "ag.pro" not in profiles
    assert "cc.pro" not in profiles
    assert "cx.pro" not in profiles


def test_no_gemini_3_1_pro_or_haiku_4_5_in_packaged_profiles():
    for name, p in TOML["profiles"].items():
        assert "gemini-3.1-pro" not in p["model"], f"{name} contains gemini-3.1-pro"
        assert "haiku-4-5" not in p["model"], f"{name} contains haiku-4-5"


@pytest.mark.parametrize("kind,pid", [
    ("ag", "ag.pro"),
    ("cc", "cc.pro"),
    ("cx", "cx.pro"),
    ("ag", "pro"),
    ("cc", "pro"),
    ("cx", "pro"),
])
def test_retired_pro_profiles_raise_unknown_profile_error(tmp_path, kind, pid):
    from peerhub.extensions.adapters.profiles import resolve_profile
    with pytest.raises(ValueError, match="unknown model profile"):
        resolve_profile(kind, pid, tmp_path)


def expected_profile_source(manifest: dict, pid: str) -> str:
    peer = pid.split(".", 1)[0]
    observed_at = manifest["observed_at"]
    if peer == "cc":
        return f"known-id:claude --help aliases@{manifest['observed_versions']['cc']} {observed_at}"
    return f"cli:{manifest['sources'][peer]['command']}@{manifest['observed_versions'][peer]} {observed_at}"


def test_manifest_every_profile_has_source_matching_sources_keys():
    for pid, profile in MANIFEST["profiles"].items():
        peer = pid.split(".", 1)[0]
        assert peer in MANIFEST["sources"]
        want = expected_profile_source(MANIFEST, pid)
        assert profile.get("source") == want, f"{pid} source mismatch: got {profile.get('source')!r}, want {want!r}"


def test_profile_source_comparison_rejects_mutated_source():
    bad_manifest = {
        "observed_at": "2026-10-16",
        "observed_versions": {"ag": "1.4.0", "cc": "2.1.301", "cx": "0.163.0"},
        "sources": {
            "cx": {"command": "codex debug models"},
            "ag": {"command": "agy.exe models"},
            "cc": {"command": None},
        },
        "profiles": {
            "cx.standard": {"source": "cli:agy.exe models@WRONG invalid-date"},
        },
    }
    want = expected_profile_source(bad_manifest, "cx.standard")
    with pytest.raises(AssertionError):
        assert bad_manifest["profiles"]["cx.standard"]["source"] == want



