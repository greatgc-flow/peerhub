"""gemini-4-hydrogen recorded 2026-11-13 as a known model without a profile (unanimous profile vote).

Like test_model_profiles_manifest.py this asserts invariants, not refresh values (revision, versions, dates),
so a later toml-only refresh does not touch this file.
"""
import tomllib
from importlib import resources

from peerhub.extensions.adapters.profiles import inspect_model_pools, known_model_pools

HYDROGEN = [f"gemini-4-hydrogen-{effort}" for effort in ("low", "medium", "high", "xhigh")]


def _packaged():
    return tomllib.loads(resources.files("peerhub.config_data").joinpath("model-defaults.toml").read_text(encoding="utf-8"))


def test_unverified_pools_are_never_reported_as_verified(tmp_path):
    for model, entry in known_model_pools(tmp_path).items():
        if entry.get("pools_source") == "unverified":
            assert inspect_model_pools(model, tmp_path)["verified"] is False, model


def test_hydrogen_is_known_with_unverified_pools_and_no_profile(tmp_path):
    known = known_model_pools(tmp_path)
    for model in HYDROGEN:
        assert known[model]["pools_source"] == "unverified", model
        assert "18/115" in known[model]["note"] and "100/100" in known[model]["note"]
        assert ("inferred from the -high run" in known[model]["note"]) == (model != "gemini-4-hydrogen-high"), model
    assert not any("hydrogen" in str(entry.get("model")) for entry in _packaged()["profiles"].values())
