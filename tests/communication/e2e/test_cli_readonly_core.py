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
        assert main(["--db", str(db), "peer", "register", "--peer", peer]) == 0
        capsys.readouterr()

    first = main(["--db", str(db), "peer", "list", "--limit", "2"])
    first_out = capsys.readouterr().out
    assert first == 0
    import json
    page = json.loads(first_out)
    assert [row["peer_id"] for row in page["items"]] == ["a", "b"]
    assert page["next_cursor"]

    assert main(["--db", str(db), "peer", "list", "--limit", "2", "--cursor", page["next_cursor"]]) == 0
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
    assert main(["--db", str(db), "peer", "register", "--peer", "p1"]) == 0
    capsys.readouterr()
    before = _digest(db)

    assert main(["--db", str(db), "diag", "integrity"]) == 0
    import json
    assert json.loads(capsys.readouterr().out)["sqlite"] == ["ok"]
    assert _digest(db) == before
