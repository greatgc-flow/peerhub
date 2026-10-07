"""Store naming policy: provenance, ambiguity refusal, and the explicit, safe legacy rename."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

from peerhub.cli.app import main
from peerhub.cli.store_cmd import rename_legacy
from peerhub.cli.store_select import StoreAmbiguousError, select_store


def _make(path, rows=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS t(v)")
    for i in range(rows):
        conn.execute("INSERT INTO t VALUES (?)", (i,))
    conn.commit()
    return conn  # left open on purpose: committed rows stay in the -wal until a checkpoint


def test_selection_order_and_sources(tmp_path):
    assert select_store("x.db", env={"PEERHUB_DB": "e.db"}).source == "env"
    assert select_store("x.db", env={}).source == "explicit"
    assert select_store(".peerhub/core.db", env={}, cwd=tmp_path).source == "default"
    _make(tmp_path / ".peerhub" / "m1.db").close()
    sel = select_store(".peerhub/core.db", env={}, cwd=tmp_path / "deep" if (tmp_path / "deep").mkdir() is None else tmp_path)
    assert sel.source == "discovered" and sel.path.endswith("m1.db")  # legacy reused without renaming, found from a parent too
    assert select_store(".peerhub/core.db", env={}, workspace_root=tmp_path / "fresh").path.endswith("core.db")


def test_both_names_in_one_workspace_are_refused_but_explicit_choice_works(tmp_path, capsys, monkeypatch):
    _make(tmp_path / ".peerhub" / "m1.db").close()
    _make(tmp_path / ".peerhub" / "core.db").close()
    with pytest.raises(StoreAmbiguousError):
        select_store(".peerhub/core.db", env={}, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PEERHUB_DB", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["diag", "health"])
    assert exc.value.code == 2 and "both" in capsys.readouterr().err
    assert select_store("x.db", env={}, cwd=tmp_path).source == "explicit"  # --db bypasses the guard
    assert select_store(".peerhub/core.db", env={"PEERHUB_DB": "e.db"}, cwd=tmp_path).source == "env"


def test_diag_health_reports_how_the_store_was_chosen(tmp_path, capsys):
    db = tmp_path / "w.db"
    assert main(["--db", str(db), "peer", "register", "--peer", "a"]) == 0
    capsys.readouterr()
    assert main(["--db", str(db), "diag", "health"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["store_selection"] == {"path": str(db), "source": "explicit"}


def test_rename_legacy_preserves_committed_wal_rows(tmp_path):
    holder = _make(tmp_path / ".peerhub" / "m1.db", rows=5)
    holder.close()  # last connection closed: sidecars may remain until checkpoint
    res = rename_legacy(tmp_path)
    assert res["status"] == "RENAMED"
    root = tmp_path / ".peerhub"
    assert sorted(p.name for p in root.iterdir()) == ["core.db"]  # no stray sidecars under either name
    with closing(sqlite3.connect(root / "core.db")) as c:
        assert c.execute("SELECT count(*) FROM t").fetchone()[0] == 5


def test_rename_legacy_refuses_while_another_connection_is_open(tmp_path):
    holder = _make(tmp_path / ".peerhub" / "m1.db", rows=2)
    try:
        res = rename_legacy(tmp_path)
        assert res["status"] == "REFUSED"
        assert (tmp_path / ".peerhub" / "m1.db").is_file() and not (tmp_path / ".peerhub" / "core.db").exists()
    finally:
        holder.close()
    assert rename_legacy(tmp_path)["status"] == "RENAMED"  # once nothing holds it, the same call succeeds


def test_rename_legacy_never_overwrites_an_existing_destination(tmp_path):
    _make(tmp_path / ".peerhub" / "m1.db").close()
    (tmp_path / ".peerhub" / "core.db-wal").write_bytes(b"")
    res = rename_legacy(tmp_path)
    assert res["status"] == "REFUSED" and (tmp_path / ".peerhub" / "m1.db").is_file()
    (tmp_path / ".peerhub" / "core.db-wal").unlink()
    _make(tmp_path / ".peerhub" / "core.db").close()
    assert rename_legacy(tmp_path)["status"] == "REFUSED"


def test_rename_legacy_with_nothing_to_rename(tmp_path):
    assert rename_legacy(tmp_path)["status"] == "NOTHING_TO_DO"
    assert main(["store", "rename-legacy", "--workspace", str(tmp_path)]) == 0
