"""CLI audit 0.24.25 (#401): error codes, human formats and option help of the CLI leaves."""
import argparse
import json

from peerhub.cli.app import build_parser, main


def _run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _store(tmp_path, capsys):
    db = str(tmp_path / "core.db")
    assert main(["--db", db, "peer", "register", "--peer", "reader"]) == 0
    capsys.readouterr()
    return db


def test_ask_unknown_peer_is_a_user_error_with_its_own_code(tmp_path, capsys):
    db = str(tmp_path / "core.db")
    code, out, err = _run(capsys, "--db", db, "ask", "nosuch", "hello")
    assert code == 1
    assert err.startswith("[peer_not_found] ERROR: register this peer with --adapter")
    assert "next: peerhub explain peer_not_found" in err and "ask_unexpected_failure" not in err
    code, out, err = _run(capsys, "--db", db, "ask", "nosuch", "hello", "--json")
    payload = json.loads(out)
    assert code == 1
    assert payload["error"]["code"] == "peer_not_found"
    assert payload["guidance"]["code"] == "peer_not_found"


def test_profile_show_unknown_name_does_not_leak_the_exception_class(capsys):
    code, out, err = _run(capsys, "profile", "show", "nosuch.profile")
    assert code == 1
    assert err.startswith("[invalid_arguments] unknown model profile")
    assert "ValueError" not in err


def test_monitor_defaults_show_is_a_table_and_json_only_with_flag(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "cfg"))
    code, out, err = _run(capsys, "monitor", "defaults", "show")
    assert code == 0 and err == ""
    lines = out.splitlines()
    assert not out.lstrip().startswith("{")
    assert lines[0].split() == ["ID", "DEFAULT"]
    rows = {line.split()[0]: line.split()[1] for line in lines[1:] if line.split()[0] != "Defaults:"}
    assert rows["overview"] == "yes" and rows["store"] == "no"
    assert lines[-1] == "Defaults: packaged (OK)"
    code, out, _ = _run(capsys, "monitor", "defaults", "show", "--json")
    assert code == 0 and json.loads(out)["selected_groups"][0] == "overview"


def test_monitor_defaults_show_invalid_file_names_the_reason(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "monitor.toml").write_text("not = [toml", encoding="utf-8")
    code, out, _ = _run(capsys, "monitor", "defaults", "show")
    assert code == 1
    assert not out.lstrip().startswith("{")
    assert "Defaults: global (ERROR)" in out and "Reason: invalid_toml" in out


def test_account_list_empty_prints_the_no_x_line(tmp_path, capsys):
    db = _store(tmp_path, capsys)
    code, out, _ = _run(capsys, "--db", db, "account", "list")
    assert code == 0
    assert out == "(no accounts)\n"


def test_missing_values_print_a_dash_in_peer_get_and_stream_show(tmp_path, capsys):
    db = _store(tmp_path, capsys)
    assert main(["--db", db, "stream", "create", "--stream", "s1"]) == 0
    capsys.readouterr()
    for argv, key in ((["peer", "get", "--peer", "reader"], "display_name"),
                      (["stream", "show", "--stream", "s1"], "title")):
        code, out, _ = _run(capsys, "--db", db, *argv)
        assert code == 0
        assert f"{key}: -" in out.splitlines()
        assert "None" not in out
        code, out, _ = _run(capsys, "--db", db, *argv, "--json")
        assert json.loads(out)[key] is None


def _parsers(parser, path=()):
    yield path, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                yield from _parsers(child, (*path, name))


def test_every_visible_option_has_help_and_every_json_has_the_same_help():
    missing, json_help = [], {}
    for path, parser in _parsers(build_parser()):
        for action in parser._actions:
            if not action.option_strings or action.help == argparse.SUPPRESS:
                continue
            if not (action.help or "").strip():
                missing.append(f"{' '.join(path)} {action.option_strings[0]}")
            if "--json" in action.option_strings:
                json_help[" ".join(path) or "(top)"] = action.help
    assert not missing, "options without help:\n" + "\n".join(missing)
    assert {help_ for help_ in json_help.values()} == {"Machine-readable JSON output"}, json_help
