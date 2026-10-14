import json

from peerhub.cli.app import main


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
