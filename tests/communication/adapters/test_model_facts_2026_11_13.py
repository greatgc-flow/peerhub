"""Model facts re-observed 2026-11-13 (claude 2.1.323, codex 0.167.2, agy 1.5.7); unanimous profile vote (cx.deepthink, ag.deepthink)."""
import tomllib
from importlib import resources

from peerhub.extensions.adapters.profiles import inspect_model_pools, known_model_pools

HYDROGEN = [f"gemini-4-hydrogen-{effort}" for effort in ("low", "medium", "high", "xhigh")]


def _packaged():
    return tomllib.loads(resources.files("peerhub.config_data").joinpath("model-defaults.toml").read_text(encoding="utf-8"))


def test_observed_versions_and_revision_are_current():
    data = _packaged()
    assert data["defaults_revision"] == "2026-11-13.1"
    assert data["observed_at"] == "2026-11-13"
    assert data["observed_versions"] == {"ag": "1.5.7", "cc": "2.1.323", "cx": "0.167.2"}


def test_hydrogen_is_a_known_model_without_a_profile_or_verified_pool(tmp_path):
    known = known_model_pools(tmp_path)
    for model in HYDROGEN:
        assert model in known, model
        assert known[model]["pools"] == ["G-5H", "G-7D"] and known[model]["pools_source"] == "unverified"
        assert "18/115" in known[model]["note"] and "100/100" in known[model]["note"]
        assert inspect_model_pools(model, tmp_path) == {"pools": ["G-5H", "G-7D"], "verified": False}
    profiles = _packaged()["profiles"]
    assert not any("hydrogen" in str(entry.get("model")) for entry in profiles.values())
    assert len(profiles) == 12 and "cx.terra" not in profiles


def test_every_packaged_profile_was_rechecked_on_the_observation_date():
    data = _packaged()
    assert {pid: p["checked"] for pid, p in data["profiles"].items()} == {pid: data["observed_at"] for pid in data["profiles"]}


def test_hydrogen_notes_do_not_overstate_runs_for_unbenchmarked_efforts(tmp_path):
    known = known_model_pools(tmp_path)
    for model in HYDROGEN:
        note = known[model]["note"]
        assert ("inferred from the -high run" in note) == (model != "gemini-4-hydrogen-high"), model
