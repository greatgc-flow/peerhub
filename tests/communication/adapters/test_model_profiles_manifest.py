"""One source for model profiles: the packaged toml holds profiles and pools; model-profiles.json holds the refresh recipe.

Tests assert invariants of the toml and of the recipe, not packaged values, so adding, changing or
deleting a profile only edits one toml table.
"""
import json
import re
import tomllib
from pathlib import Path

import pytest

from peerhub.core.peer_kinds import BUILTIN_PEER_KINDS, CLI_PEER_ALIASES

REPO = Path(__file__).resolve().parents[3]
DIR = REPO / "docs" / "model-profiles"
MANIFEST = json.loads((DIR / "model-profiles.json").read_text(encoding="utf-8"))
TOML = tomllib.loads((REPO / "peerhub" / "config_data" / "model-defaults.toml").read_text(encoding="utf-8"))
PEERS = tuple(sorted(CLI_PEER_ALIASES))  # Only CLI peers have a refresh recipe and observed version.
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def packaged_profiles(peers: tuple[str, ...] = PEERS) -> dict:
    """The packaged profiles of the CLI peers by default."""
    return {k: v for k, v in TOML["profiles"].items() if k.split(".", 1)[0] in peers}


def test_manifest_is_valid_against_its_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(MANIFEST, json.loads((DIR / "model-profiles.schema.json").read_text(encoding="utf-8")))


def test_manifest_keeps_only_the_recipe():
    assert set(MANIFEST) == {"$schema", "schema_version", "sources", "tier_rules", "update_targets", "instructions"}
    assert set(MANIFEST["sources"]) == set(PEERS)


def test_toml_metadata_is_well_formed():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", TOML["defaults_revision"])
    assert DATE.match(TOML["observed_at"])
    assert set(TOML["observed_versions"]) == set(PEERS)


def test_every_profile_id_names_a_peer_and_has_required_fields():
    for pid, p in TOML["profiles"].items():
        kind, _, name = pid.partition(".")
        assert kind in BUILTIN_PEER_KINDS and re.fullmatch(r"[a-z0-9-]+", name), pid
        if "alias_of" in p:  # 0.25.0 D11: an alias takes its binding from the target profile
            target = p["alias_of"]
            assert target in TOML["profiles"] and target != pid and "alias_of" not in TOML["profiles"][target], pid
            assert target.split(".", 1)[0] == pid.split(".", 1)[0] and not {"model", "selection_mode"} & set(p), pid
            assert p["effort_override"], pid
        else:
            assert p["selection_mode"] == "pinned" and p["model"], pid
        assert DATE.match(p["checked"]), pid
        assert p["source"], pid
    for pid, p in packaged_profiles().items():
        assert p["pools_source"], pid


def test_pool_ids_exist_in_probe_constants():
    from peerhub.extensions.quota_probes import AGY_QUOTA_FAMILIES
    from peerhub.extensions.quota_probes.claude import _CLAUDE_USAGE_SECTIONS

    known = {v for _, v in AGY_QUOTA_FAMILIES} | {v[0] for v in _CLAUDE_USAGE_SECTIONS.values()} | {"X-5H", "X-7D"}
    entries = {**packaged_profiles(), **TOML.get("exact_model_pools", {})}
    for name, p in entries.items():
        pools = p.get("pools")
        assert isinstance(pools, list) and pools, f"{name} missing pools"
        assert set(pools) <= known, f"{name} pools {pools} not in known probe constants"
        assert p["pools_source"] == "unverified" or p["pools_source"].startswith(("agy /usage", "claude /usage")), f"{name} unknown pool evidence"


def test_every_profile_model_is_registered_or_peer_scoped():
    registry = TOML.get("exact_model_pools", {})
    for pid, p in packaged_profiles().items():
        if pid.startswith("ag."):
            assert p["model"] in registry, f"{pid} model {p['model']!r} missing from exact_model_pools"
            assert p["pools"] == registry[p["model"]]["pools"], pid


def test_argon_profile_and_exact_models_share_the_gemini_quota():
    profiles = packaged_profiles()
    argon = profiles["ag.argon"]
    gemini_pools = profiles["ag.standard"]["pools"]
    assert argon["pools"] == gemini_pools
    argon_family = argon["model"].rsplit("-", 1)[0]
    for model, entry in TOML["exact_model_pools"].items():
        if model.startswith(f"{argon_family}-"):
            assert entry["pools"] == gemini_pools, model


def test_profile_source_matches_recipe_command_and_observation():
    observed_at, versions = TOML["observed_at"], TOML["observed_versions"]
    sources: dict[str, set[str]] = {}
    for pid, p in packaged_profiles().items():
        peer = pid.split(".", 1)[0]
        command = MANIFEST["sources"][peer]["command"]
        prefix = f"cli:{command}" if command else "known-id:claude --help aliases"
        match = re.fullmatch(rf"{re.escape(prefix)}@(\d+\.\d+\.\d+) (\d{{4}}-\d{{2}}-\d{{2}})", p["source"])
        assert match, f"{pid} has invalid catalog provenance {p['source']!r}"
        catalog_version, catalog_date = match.groups()
        # Recording an installed CLI version does not re-observe its model catalog.
        assert tuple(map(int, catalog_version.split("."))) <= tuple(map(int, versions[peer].split("."))), pid
        assert catalog_date <= observed_at, pid
        sources.setdefault(peer, set()).add(p["source"])
    assert all(len(peer_sources) == 1 for peer_sources in sources.values())


def test_exact_model_pools_resolve_through_the_resolver(tmp_path):
    from peerhub.extensions.adapters.profiles import resolve_model_pools

    for model, entry in TOML["exact_model_pools"].items():
        assert resolve_model_pools(model, tmp_path) == entry["pools"], model
    assert resolve_model_pools("unknown-model", tmp_path) is None


def test_packaged_profiles_have_no_stale_models():
    for name, p in packaged_profiles().items():
        model = p.get("model", "")  # an alias (0.25.0 D11) has no model of its own
        assert "gemini-3.1-pro" not in model, f"{name} contains gemini-3.1-pro"
        assert "haiku-4-5" not in model, f"{name} contains haiku-4-5"


@pytest.mark.parametrize("kind,pid", [(kind, pid) for kind in PEERS for pid in (f"{kind}.pro", "pro")])
def test_retired_pro_profiles_raise_unknown_profile_error(tmp_path, kind, pid):
    from peerhub.extensions.adapters.profiles import resolve_profile

    with pytest.raises(ValueError, match="unknown model profile"):
        resolve_profile(kind, pid, tmp_path)


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


def test_added_profile_needs_only_a_toml_table(tmp_path, monkeypatch):
    """An opt-in profile (for example cx.terra) is one global models.toml table: no code, schema or doc change."""
    from peerhub.extensions.adapters.profiles import inspect_profile, list_profiles

    home = tmp_path / "cfg"
    home.mkdir()
    (home / "models.toml").write_text(
        'schema_version = 1\n[profiles."cx.terra"]\nselection_mode = "pinned"\nmodel = "gpt-6.1-terra"\n'
        'reasoning_effort = "low"\npools = ["X-5H", "X-7D"]\npools_source = "unverified"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(home))
    names = [p["profile"] for p in list_profiles(tmp_path)]
    assert "cx.terra" in names and set(packaged_profiles(BUILTIN_PEER_KINDS)) <= set(names)
    fields = inspect_profile("cx.terra", tmp_path)["fields"]
    assert fields["model"]["value"] == "gpt-6.1-terra" and fields["effort"]["value"] == "low"
