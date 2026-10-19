import json

import pytest

from peerhub.cli.app import main


@pytest.mark.parametrize("reader", ["resolve_profile", "inspect_profile", "list_profiles", "resolve_model_pools", "inspect_model_pools"])
@pytest.mark.parametrize("version", ["2", "true", '"1"', "1.0"])
@pytest.mark.parametrize("scope", ["global", "workspace"])
def test_profile_readers_reject_invalid_schema_in_every_layer(tmp_path, monkeypatch, reader, version, scope):
    from peerhub.extensions.adapters import profiles

    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "global"))
    config = (tmp_path / "global" if scope == "global" else tmp_path / ".peerhub" / "config") / "models.toml"
    config.parent.mkdir(parents=True)
    config.write_text(f'schema_version={version}\n[exact_model_pools."test-model"]\npools=["TEST"]\n', encoding="utf-8")
    args = {"resolve_profile": ("cx", "cx.standard", tmp_path), "inspect_profile": ("cx.standard", tmp_path),
            "list_profiles": (tmp_path,), "resolve_model_pools": ("test-model", tmp_path),
            "inspect_model_pools": ("test-model", tmp_path)}
    with pytest.raises(ValueError, match="schema_version"):
        getattr(profiles, reader)(*args[reader])


@pytest.mark.parametrize("reader", ["inspect_profile", "list_profiles"])
def test_profile_inspection_reads_each_layer_once(tmp_path, monkeypatch, reader):
    from peerhub.extensions.adapters import profiles

    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "global"))
    config = tmp_path / ".peerhub" / "config" / "models.toml"
    config.parent.mkdir(parents=True)
    config.write_text('[profiles."cx.standard"]\nmodel="workspace-model"\n', encoding="utf-8")
    real_load = profiles.tomllib.load
    reads = []

    def load(handle):
        reads.append(handle.name)
        return real_load(handle)

    monkeypatch.setattr(profiles.tomllib, "load", load)
    if reader == "list_profiles":
        result = next(item for item in profiles.list_profiles(tmp_path) if item["profile"] == "cx.standard")
    else:
        result = profiles.inspect_profile("cx.standard", tmp_path)
    assert result["fields"]["model"] == {"value": "workspace-model", "source": "workspace"}
    assert len(reads) == 2 and len(set(reads)) == 2


def test_profile_show_resolves_fields_with_source_provenance(tmp_path, monkeypatch, capsys):
    global_home = tmp_path / "global"
    workspace = tmp_path / "workspace"
    global_home.mkdir()
    (workspace / ".peerhub" / "config").mkdir(parents=True)
    (global_home / "models.toml").write_text(
        '[profiles."cx.effort"]\nmodel = "global-model"\n', encoding="utf-8")
    (workspace / ".peerhub" / "config" / "models.toml").write_text(
        '[profiles."cx.effort"]\nreasoning_effort = "low"\n', encoding="utf-8")
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(global_home))

    code = main(["profile", "show", "cx.effort", "--workspace", str(workspace)])
    data = json.loads(capsys.readouterr().out)

    assert code == 0
    assert data["fields"]["model"] == {"value": "global-model", "source": "global"}
    assert data["fields"]["effort"] == {"value": "low", "source": "workspace"}
    assert data["fields"]["selection_mode"]["source"] == "packaged"


def test_profile_list_is_read_only_and_lists_packaged_profiles(tmp_path, capsys):
    code = main(["--db", str(tmp_path / "unused.db"), "profile", "list", "--workspace", str(tmp_path)])
    data = json.loads(capsys.readouterr().out)

    assert code == 0 and any(item["profile"] == "cx.standard" for item in data["profiles"])
    assert not (tmp_path / "unused.db").exists()


def test_profile_show_reports_invalid_config_in_json(tmp_path, capsys):
    config = tmp_path / ".peerhub" / "config" / "models.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[profiles.\"cx.effort\"\n", encoding="utf-8")

    code = main(["profile", "show", "cx.effort", "--workspace", str(tmp_path)])
    data = json.loads(capsys.readouterr().out)

    assert code == 1 and data["status"] == "ERROR"
