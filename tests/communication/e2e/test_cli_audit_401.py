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
    assert err.startswith("[peer_not_found] unknown peer 'nosuch':")
    assert "ERROR:" not in err
    assert "next: peerhub peer register --peer nosuch --adapter <adapter> (or peerhub peer list)" in err
    assert "ask_unexpected_failure" not in err
    code, out, err = _run(capsys, "--db", db, "ask", "nosuch", "hello", "--json")
    payload = json.loads(out)
    assert code == 1
    assert payload["error"]["code"] == "peer_not_found"
    assert "ERROR:" not in payload["error"]["message"]
    assert payload["guidance"]["code"] == "peer_not_found"
    code, out, _ = _run(capsys, "explain", "exit-codes")
    assert code == 0 and "1 for an unregistered peer (peer_not_found)" in out


def test_profile_show_unknown_name_does_not_leak_the_exception_class(capsys):
    code, out, err = _run(capsys, "profile", "show", "nosuch.profile")
    assert code == 1
    assert err.startswith("[invalid_arguments] unknown model profile")
    assert "ValueError" not in err


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


def test_help_profile_check_ok_and_uppercase_list_headers(tmp_path, monkeypatch, capsys):
    import pytest
    from peerhub.extensions import profile_stats

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    top_help = capsys.readouterr().out
    assert "Manage account-bound cc/cx peers" in top_help
    assert "peer register overwrite" not in top_help

    with pytest.raises(SystemExit) as exc:
        main(["profile", "check", "--help"])
    assert exc.value.code == 0
    check_help = " ".join(capsys.readouterr().out.split())
    assert "Compare configured profile model IDs" in check_help

    monkeypatch.setattr(profile_stats, "installed_models", lambda provider, workspace: None)
    monkeypatch.setattr(profile_stats, "read_profile_stats", lambda db, workspace, profiles: [
        {"profile": p["profile"], "last_measured_at": "2099-01-01T00:00:00Z", "stale": False} for p in profiles
    ])
    code, out, _ = _run(capsys, "profile", "check")
    assert code == 0 and "OK:" in out and "no missing models or overdue measurements" in out

    db = _store(tmp_path, capsys)
    assert main(["--db", db, "stream", "create", "--stream", "s1"]) == 0
    capsys.readouterr()
    code, peer_out, _ = _run(capsys, "--db", db, "peer", "list")
    assert code == 0 and peer_out.splitlines()[0].split() == ["PEER_ID", "DISPLAY_NAME"]
    code, stream_out, _ = _run(capsys, "--db", db, "stream", "list")
    assert code == 0 and stream_out.splitlines()[0].split() == ["STREAM_ID", "STATE", "TITLE", "MEMBERS"]


def test_error_line_strips_only_single_sentence_period(capsys):
    from peerhub.cli.support import error_line

    for message, expected in (
        ("Invalid ask arguments.", "Invalid ask arguments"),
        ("still waiting...", "still waiting..."),
        ("parent ..", "parent .."),
        ("workspace .", "workspace ."),
        ("path /tmp/.", "path /tmp/."),
        ("path C:\\dir\\.", "path C:\\dir\\."),
        (".", "."),
    ):
        error_line("test_code", message, "next step")
        err = capsys.readouterr().err
        assert err == f"[test_code] {expected}; next: next step\n", (message, err)
