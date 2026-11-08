import hashlib

from peerhub.cli.app import main


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_missing_core_read_does_not_create_database(tmp_path, capsys):
    db = tmp_path / "missing" / "core.db"

    code = main(["--db", str(db), "peer", "get", "--peer", "nobody"])

    assert code == 5
    assert not db.exists()


def test_existing_core_read_does_not_change_database(tmp_path, capsys):
    db = tmp_path / "core.db"
    assert main(["--db", str(db), "peer", "register", "--peer", "p1"]) == 0
    capsys.readouterr()
    before = _digest(db)

    assert main(["--db", str(db), "peer", "get", "--peer", "p1"]) == 0
    capsys.readouterr()

    assert _digest(db) == before


def test_reading_older_stamped_store_does_not_migrate_it(tmp_path, capsys):
    import sqlite3

    db = tmp_path / "core.db"
    assert main(["--db", str(db), "peer", "register", "--peer", "p1"]) == 0
    capsys.readouterr()
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 2")

    assert main(["--db", str(db), "peer", "get", "--peer", "p1"]) == 0
    capsys.readouterr()
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_peer_list_is_bounded_and_uses_opaque_cursor(tmp_path, capsys):
    db = tmp_path / "core.db"
    for peer in ("a", "b", "c"):
        assert main(["--db", str(db), "peer", "register", "--peer", peer, "--json"]) == 0
        capsys.readouterr()

    first = main(["--db", str(db), "peer", "list", "--limit", "2", "--json"])
    first_out = capsys.readouterr().out
    assert first == 0
    import json
    page = json.loads(first_out)
    assert [row["peer_id"] for row in page["items"]] == ["a", "b"]
    assert page["next_cursor"]

    assert main(["--db", str(db), "peer", "list", "--limit", "2", "--cursor", page["next_cursor"], "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert [row["peer_id"] for row in second["items"]] == ["c"]
    assert second["next_cursor"] is None


def test_peer_list_rejects_limits_outside_bounds_before_opening_store(tmp_path, capsys):
    db = tmp_path / "missing.db"
    try:
        main(["--db", str(db), "peer", "list", "--limit", "201"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("invalid limit should fail argument parsing")
    assert not db.exists()


def test_integrity_check_is_explicit_and_read_only(tmp_path, capsys):
    db = tmp_path / "core.db"
    assert main(["--db", str(db), "peer", "register", "--peer", "p1", "--json"]) == 0
    capsys.readouterr()
    before = _digest(db)

    assert main(["--db", str(db), "diag", "integrity", "--json"]) == 0
    import json
    assert json.loads(capsys.readouterr().out)["sqlite"] == ["ok"]
    assert _digest(db) == before


def test_text_default_and_explicit_json_are_tty_independent(tmp_path, capsys, monkeypatch):
    import json
    import sys
    db = tmp_path / "core.db"
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert main(["--db", str(db), "peer", "register", "--peer", "reader"]) == 0
    assert "peer_id: reader" in capsys.readouterr().out
    for tty in (False, True):
        monkeypatch.setattr(sys.stdout, "isatty", lambda: tty)
        assert main(["--db", str(db), "peer", "list"]) == 0
        captured = capsys.readouterr()
        assert not captured.out.lstrip().startswith("{")
        assert captured.err == ""
        assert main(["--db", str(db), "peer", "list", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["items"][0]["peer_id"] == "reader"


def test_missing_store_error_is_one_line_with_code_and_next(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "absent.db"), "peer", "list"]) == 5
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert "[store_missing]" in captured.err and "next:" in captured.err


def test_json_is_available_on_every_command_and_format_is_hidden():
    import argparse
    from peerhub.cli.app import build_parser
    def walk(parser):
        assert "--json" in parser._option_string_actions
        if "--format" in parser._option_string_actions:
            assert parser._option_string_actions["--format"].help == argparse.SUPPRESS
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    walk(child)
    parser = build_parser()
    walk(parser)
    assert "Advanced: stream, record, offset" in parser.format_help()


def test_parse_and_lookup_errors_have_one_diagnostic(tmp_path, capsys):
    import json
    db = tmp_path / "core.db"
    assert main(["--db", str(db), "peer", "register", "--peer", "reader"]) == 0
    capsys.readouterr()
    cases = [
        (["peer", "get", "--peer", "missing"], "peer_not_found"),
        (["stream", "show", "--stream", "missing"], "stream_not_found"),
        (["profile", "show", "no-such-profile", "--json"], "invalid_arguments"),
        (["explain", "no-such-code", "--json"], "invalid_arguments"),
        (["peer", "list", "--limit", "201"], "invalid_arguments"),
    ]
    for command, code in cases:
        try:
            result = main(["--db", str(db), *command])
        except SystemExit as exc:
            result = exc.code
        assert result != 0
        output = capsys.readouterr()
        assert len(output.err.splitlines()) == 1
        assert f"[{code}]" in output.err and "next:" in output.err
        if "--json" in command:
            assert isinstance(json.loads(output.out), dict)
