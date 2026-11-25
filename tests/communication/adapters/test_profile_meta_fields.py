"""0.25.0 plan D11/A9: optional profile fields alias_of, effort_override, suggest and measurement.

cx.deepthink is a deprecated alias of cx.effort at xhigh; cc.deepthink, ag.effort (and the alias) are not suggested;
the API peers ca, oa and ga are unmeasured. Tests assert invariants, not refreshed model ids.
"""
import json
import tomllib
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.extensions.adapters import profiles
from peerhub.extensions.adapters.profiles import inspect_profile, list_profiles, resolve_profile

REPO = Path(__file__).resolve().parents[3]
TOML = tomllib.loads((REPO / "peerhub" / "config_data" / "model-defaults.toml").read_text(encoding="utf-8"))
PACKAGED = TOML["profiles"]
API_KINDS = ("ca", "oa", "ga")


@pytest.fixture(autouse=True)
def _fresh_notices(monkeypatch):
    """Isolated global config and a clean once-per-process notice set."""
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", "unused-profile-dir")
    profiles.reset_deprecation_notices()
    yield
    profiles.reset_deprecation_notices()


def _overlay(tmp_path, monkeypatch, body: str) -> Path:
    home = tmp_path / "cfg"
    home.mkdir()
    (home / "models.toml").write_text("schema_version = 1\n" + body, encoding="utf-8")
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(home))
    return tmp_path


# ---- defaults ---------------------------------------------------------------------------------------------------

def test_defaults_are_measured_suggested_and_not_an_alias(tmp_path):
    fields = inspect_profile("cx.standard", tmp_path, include_meta=True)["fields"]
    assert fields["alias_of"] == {"value": None, "source": "default"}
    assert fields["effort_override"] == {"value": None, "source": "default"}
    assert fields["suggest"] == {"value": True, "source": "default"}
    assert fields["measurement"] == {"value": "measured", "source": "default"}


def test_meta_fields_are_left_out_unless_asked_for(tmp_path):
    """The monitor read model embeds inspect_profile(): its JSON must not grow."""
    fields = inspect_profile("cx.standard", tmp_path)["fields"]
    assert not {"alias_of", "effort_override", "suggest", "measurement"} & set(fields)
    assert not {"alias_of", "effort_override", "suggest", "measurement"} & set(list_profiles(tmp_path)[0]["fields"])


def test_packaged_flags():
    for pid, entry in PACKAGED.items():
        kind = pid.split(".", 1)[0]
        assert entry.get("measurement") == ("unmeasured" if kind in API_KINDS else None), pid
        assert entry.get("suggest") == (False if pid in ("cc.deepthink", "ag.effort", "cx.deepthink") else None), pid
    assert PACKAGED["cx.deepthink"]["alias_of"] == "cx.effort" and PACKAGED["cx.deepthink"]["effort_override"] == "xhigh"
    assert not {"alias_of", "effort_override"} & set(PACKAGED["cx.effort"])


def test_packaged_flags_resolve_through_the_loader(tmp_path):
    seen = {item["profile"]: item["fields"] for item in list_profiles(tmp_path, include_meta=True)}
    assert set(seen) == set(PACKAGED)
    for pid, fields in seen.items():
        assert fields["suggest"]["value"] is (pid not in ("cc.deepthink", "ag.effort", "cx.deepthink")), pid
        assert fields["measurement"]["value"] == ("unmeasured" if pid.split(".", 1)[0] in API_KINDS else "measured"), pid
    assert seen["cc.deepthink"]["suggest"] == {"value": False, "source": "packaged"}
    assert seen["oa.standard"]["measurement"] == {"value": "unmeasured", "source": "packaged"}


# ---- alias resolution -------------------------------------------------------------------------------------------

def test_cx_deepthink_resolves_to_the_same_model_and_effort_as_before(tmp_path):
    model, effort = resolve_profile("cx", "cx.deepthink", tmp_path)
    target_model, _ = resolve_profile("cx", "cx.effort", tmp_path)
    assert (model, effort) == (target_model, "xhigh")
    assert (model, effort) == ("gpt-6.2-sol", "xhigh")  # the packaged binding of 0.24.29, unchanged by the alias
    assert resolve_profile("cx", "deepthink", tmp_path) == (model, effort)  # the legacy short id still qualifies


def test_alias_inspection_reports_resolved_fields_and_the_alias(tmp_path):
    info = inspect_profile("cx.deepthink", tmp_path, include_meta=True)
    fields = info["fields"]
    assert fields["model"]["value"] == "gpt-6.2-sol" and fields["effort"]["value"] == "xhigh"
    assert fields["effort"]["source"] == "packaged" and fields["selection_mode"]["value"] == "pinned"
    assert fields["alias_of"] == {"value": "cx.effort", "source": "packaged"}
    assert fields["effort_override"] == {"value": "xhigh", "source": "packaged"}
    assert fields["pools"]["value"] == PACKAGED["cx.deepthink"]["pools"]
    assert info["card"]["use_for"]["value"] == PACKAGED["cx.deepthink"]["use_for"]  # the alias keeps its own card


def test_alias_follows_the_target_profile_of_every_layer(tmp_path, monkeypatch):
    ws = _overlay(tmp_path, monkeypatch, '[profiles."cx.effort"]\nmodel = "gpt-x-new"\nreasoning_effort = "low"\n')
    assert resolve_profile("cx", "cx.effort", ws) == ("gpt-x-new", "low")
    assert resolve_profile("cx", "cx.deepthink", ws) == ("gpt-x-new", "low")


@pytest.mark.parametrize("local_table, local_key", [
    ("cx.effort", "reasoning_effort"), ("cx.deepthink", "reasoning_effort"),
    ("cx.deepthink", "effort_override"),
])
def test_workspace_alias_effort_wins_over_lower_layers(monkeypatch, local_table, local_key):
    packaged = {"profiles": {
        "cx.effort": {"selection_mode": "pinned", "model": "gpt-test", "reasoning_effort": "high"},
        "cx.deepthink": {"alias_of": "cx.effort", "effort_override": "xhigh"},
    }}
    global_layer = {"profiles": {"cx.deepthink": {"effort_override": "low"}}}
    workspace = {"profiles": {local_table: {local_key: "medium"}}}
    layers = [("packaged", packaged), ("global", global_layer), ("workspace", workspace)]
    monkeypatch.setattr(profiles, "_load_layers", lambda ws: layers)
    assert resolve_profile("cx", "cx.deepthink", Path.cwd()) == ("gpt-test", "medium")
    assert inspect_profile("cx.deepthink", Path.cwd())["fields"]["effort"] == {"value": "medium", "source": "workspace"}


def test_an_overlay_can_change_the_override(tmp_path, monkeypatch):
    ws = _overlay(tmp_path, monkeypatch, '[profiles."cx.deepthink"]\neffort_override = "medium"\n')
    assert resolve_profile("cx", "cx.deepthink", ws) == ("gpt-6.2-sol", "medium")


def test_an_overlay_can_point_the_alias_at_another_profile(tmp_path, monkeypatch):
    ws = _overlay(tmp_path, monkeypatch, '[profiles."cx.deepthink"]\nalias_of = "cx.standard"\n')
    assert resolve_profile("cx", "cx.deepthink", ws) == (resolve_profile("cx", "cx.standard", ws)[0], "xhigh")


def test_alias_resolution_does_not_leak_the_target_card_or_flags(tmp_path, monkeypatch):
    ws = _overlay(tmp_path, monkeypatch, '[profiles."cx.effort"]\nsuggest = false\nmeasurement = "unmeasured"\n')
    fields = inspect_profile("cx.deepthink", ws, include_meta=True)["fields"]
    assert fields["suggest"]["value"] is False  # its own packaged flag
    assert fields["measurement"]["value"] == "measured"  # not inherited from the target
    assert inspect_profile("cx.effort", ws, include_meta=True)["fields"]["measurement"]["value"] == "unmeasured"


def test_explicit_effort_still_wins_at_ask_time(tmp_path):
    """The ask path takes `--effort` over the profile effort (base.py); the alias changes nothing there."""
    from peerhub.extensions.adapters.base import CliRuntimeTarget

    target = CliRuntimeTarget("cx", tmp_path, profile="cx.deepthink", effort="low")
    assert target._effort == "low" and target._model == "gpt-6.2-sol"  # pyright: ignore[reportPrivateUsage]


# ---- deprecation line -------------------------------------------------------------------------------------------

def test_using_the_alias_prints_one_stderr_line(tmp_path, capsys):
    resolve_profile("cx", "cx.deepthink", tmp_path, notice_alias=True)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ("peerhub: profile cx.deepthink is deprecated: an alias of cx.effort with effort xhigh; "
                            "use --profile cx.effort --effort xhigh\n")


def test_the_line_is_printed_once_per_process_and_alias(tmp_path, capsys):
    for _ in range(3):
        resolve_profile("cx", "cx.deepthink", tmp_path, notice_alias=True)
    assert capsys.readouterr().err.count("deprecated") == 1
    profiles.reset_deprecation_notices()
    resolve_profile("cx", "cx.deepthink", tmp_path, notice_alias=True)
    assert capsys.readouterr().err.count("deprecated") == 1


def test_the_ask_target_resolves_through_the_same_line(tmp_path, capsys):
    from peerhub.extensions.adapters.base import CliRuntimeTarget

    CliRuntimeTarget("cx", tmp_path, profile="cx.deepthink", notice_alias=True)
    assert capsys.readouterr().err.count("cx.deepthink is deprecated") == 1


def test_no_other_use_prints_anything(tmp_path, capsys):
    resolve_profile("cx", "cx.effort", tmp_path)
    resolve_profile("cc", "cc.deepthink", tmp_path)
    inspect_profile("cx.deepthink", tmp_path, include_meta=True)
    list_profiles(tmp_path, include_meta=True)
    assert main(["profile", "show", "cx.deepthink", "--workspace", str(tmp_path)]) == 0
    assert main(["profile", "list", "--workspace", str(tmp_path)]) == 0
    assert capsys.readouterr().err == ""


# ---- schema validation --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("body,match", [
    ('suggest = "no"', "suggest must be true or false"),
    ('suggest = 1', "suggest must be true or false"),
    ('measurement = "maybe"', "measurement must be measured or unmeasured"),
    ('measurement = true', "measurement must be measured or unmeasured"),
    ('alias_of = 5', "alias_of must be"),
    ('alias_of = "cx.effort"\neffort_override = ""', "effort_override must be a nonempty string"),
    ('alias_of = "cx.deepthink"', "alias_of cannot name itself"),
    ('alias_of = "cx.does-not-exist"', "unknown model profile"),
    ('alias_of = "cc.effort"', "alias_of must name a cx profile"),
    ('alias_of = "ghost"', "alias_of must be"),
])
def test_invalid_values_are_rejected(tmp_path, monkeypatch, body, match):
    ws = _overlay(tmp_path, monkeypatch, f'[profiles."cx.deepthink"]\n{body}\n')
    with pytest.raises(ValueError, match=match):
        inspect_profile("cx.deepthink", ws, include_meta=True)
    with pytest.raises(ValueError, match=match):
        resolve_profile("cx", "cx.deepthink", ws)


def test_an_override_needs_an_alias(tmp_path, monkeypatch):
    ws = _overlay(tmp_path, monkeypatch, '[profiles."cx.extra"]\nselection_mode = "pinned"\nmodel = "m"\neffort_override = "xhigh"\n')
    with pytest.raises(ValueError, match="effort_override needs alias_of"):
        resolve_profile("cx", "cx.extra", ws)
    with pytest.raises(ValueError, match="effort_override needs alias_of"):
        inspect_profile("cx.extra", ws, include_meta=True)


def test_alias_chains_are_rejected(tmp_path, monkeypatch):
    ws = _overlay(tmp_path, monkeypatch, '[profiles."cx.effort"]\nalias_of = "cx.standard"\n')
    with pytest.raises(ValueError, match="alias chains"):
        resolve_profile("cx", "cx.deepthink", ws)


def test_a_plain_profile_may_set_the_flags_without_an_alias(tmp_path, monkeypatch):
    ws = _overlay(tmp_path, monkeypatch, '[profiles."cx.extra"]\nselection_mode = "pinned"\nmodel = "m"\n'
                  'pools = ["X-5H"]\nsuggest = false\nmeasurement = "unmeasured"\n')
    fields = inspect_profile("cx.extra", ws, include_meta=True)["fields"]
    assert fields["suggest"] == {"value": False, "source": "global"}
    assert fields["measurement"] == {"value": "unmeasured", "source": "global"}
    assert resolve_profile("cx", "cx.extra", ws) == ("m", None)


def test_flags_do_not_count_as_a_binding_change(tmp_path, monkeypatch):
    ws = _overlay(tmp_path, monkeypatch, '[profiles."cx.effort"]\nsuggest = false\n')
    assert inspect_profile("cx.effort", ws)["card"] is not None


# ---- CLI ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("columns,stacked", [(80, True), (240, False)], ids=["stacked", "table"])
def test_profile_list_shows_alias_not_suggested_and_unmeasured(columns, stacked, monkeypatch, capsys):
    workspace = Path(__file__).parent
    monkeypatch.setenv("COLUMNS", str(columns))
    assert main(["--db", str(workspace / "unused.db"), "profile", "list", "--workspace", str(workspace)]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    expected = {"cx.deepthink": "alias of cx.effort (xhigh); not suggested",
                "cc.deepthink": "not suggested", "ag.effort": "not suggested",
                "ca.standard": "unmeasured", "oa.effort": "unmeasured", "ga.effort": "unmeasured",
                "cx.standard": "-"}
    if stacked:
        for pid, notes in expected.items():
            block = next(block for block in out.split("\n\n") if block.startswith(pid + "\n"))
            assert f"  NOTES: {notes}" in block.splitlines()
    else:
        header = lines[0]
        assert "USE FOR" in header
        notes_start, notes_end = header.index("NOTES"), header.index("STATS")
        for pid, notes in expected.items():
            row = next(line for line in lines if line.startswith(pid + " "))
            assert row[notes_start:notes_end].strip() == notes


def test_profile_show_lists_the_four_fields(tmp_path, capsys):
    assert main(["profile", "show", "cx.deepthink", "--workspace", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    rows = {line.split()[0]: line for line in out.splitlines()[2:] if line.strip()}
    assert "cx.effort" in rows["alias_of"] and "packaged" in rows["alias_of"]
    assert "xhigh" in rows["effort_override"]
    assert rows["suggest"].split()[1] == "false" and rows["measurement"].split()[1] == "measured"
    assert main(["profile", "show", "cx.standard", "--workspace", str(tmp_path)]) == 0
    rows = {line.split()[0]: line for line in capsys.readouterr().out.splitlines()[2:] if line.strip()}
    assert rows["suggest"].split()[1] == "true" and "(unset)" in rows["alias_of"] and "default" in rows["alias_of"]


def test_profile_json_carries_the_fields(tmp_path, capsys):
    assert main(["profile", "list", "--workspace", str(tmp_path), "--format", "json"]) == 0
    listed = {p["profile"]: p["fields"] for p in json.loads(capsys.readouterr().out)["profiles"]}
    assert listed["cx.deepthink"]["alias_of"]["value"] == "cx.effort" and listed["ag.effort"]["suggest"]["value"] is False
    assert listed["ga.standard"]["measurement"]["value"] == "unmeasured"
    assert main(["profile", "show", "oa.standard", "--workspace", str(tmp_path), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["fields"]["measurement"]["value"] == "unmeasured"


def test_explain_profile_id_does_not_warn_about_the_alias(tmp_path, monkeypatch, capsys):
    from peerhub.cli.recovery import run_explain_cli

    monkeypatch.chdir(tmp_path)
    assert run_explain_cli("cx.deepthink") == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("Profile: cx.deepthink (gpt-6.2-sol / xhigh)") and captured.err == ""


def test_the_schema_doc_lists_the_new_keys():
    text = (REPO / "docs" / "model-profiles" / "README.md").read_text(encoding="utf-8")
    for key in ("alias_of", "effort_override", "suggest", "measurement", "unmeasured"):
        assert key in text, key
