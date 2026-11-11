"""#338: diag/monitor name the store they read (full path + source) and warn when --db and PEERHUB_DB differ."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.cli.store_select import env_override_note
from peerhub.cli.view import render_view, visible_len
from peerhub.extensions.diag import DiagnosticReport

LONG = r"D:\PkgDev\workspace\peerhub\.peerhub\core.db"


@pytest.fixture(autouse=True)
def _wide(monkeypatch):
    monkeypatch.setenv("COLUMNS", "400")  # long tmp paths must not wrap in CLI assertions


def _store(root: Path) -> Path:
    db = root / ".peerhub" / "core.db"
    assert main(["--db", str(db), "peer", "register", "--peer", "local", "--name", "local"]) == 0
    return db


# --- note helper ---------------------------------------------------------------------------------

def test_note_names_both_stores_when_explicit_db_and_env_differ(tmp_path):
    note = env_override_note(str(tmp_path / "a.db"), "explicit", env={"PEERHUB_DB": "b.db"}, cwd=tmp_path)
    assert note.count("\n") == 0
    assert str(tmp_path / "a.db") in note and str((tmp_path / "b.db").resolve()) in note
    assert "--db" in note and "PEERHUB_DB" in note


@pytest.mark.parametrize("source,env", [
    ("explicit", {}),                          # no PEERHUB_DB
    ("explicit", {"PEERHUB_DB": "a.db"}),      # same store, relative spelling
    ("env", {"PEERHUB_DB": "b.db"}),           # env is the selection itself
    ("discovered", {"PEERHUB_DB": "b.db"}),
])
def test_no_note_without_a_conflict(tmp_path, source, env):
    assert env_override_note(str((tmp_path / "a.db").resolve()), source, env=env, cwd=tmp_path) == ""


# --- diag header ---------------------------------------------------------------------------------

def test_diag_header_shows_full_path_and_source():
    out = render_view(DiagnosticReport("OK", 1000000.0, {}, {}), width=120, unicode=False,
                      store_path=LONG, store_source="env")
    assert out.splitlines()[0].endswith(f"{LONG} (env)")
    assert ".../" not in out


@pytest.mark.parametrize("width", [45, 60, 80])
def test_narrow_header_wraps_instead_of_truncating(width):
    out = render_view(DiagnosticReport("OK", 1000000.0, {}, {}), width=width, unicode=False,
                      store_path=LONG, store_source="explicit")
    lines = out.splitlines()
    head = "\n".join(lines[:next(i for i, line in enumerate(lines) if line.startswith("Threats"))])
    assert any(LONG in line for line in head.splitlines())  # the path is never cut
    assert head.endswith("(explicit)")
    assert all(visible_len(line) <= width for line in head.splitlines())


def test_diag_header_note_line_follows_header():
    out = render_view(DiagnosticReport("OK", 1000000.0, {}, {}), width=120, unicode=False,
                      store_path=LONG, store_source="explicit", store_note="note: --db A overrides PEERHUB_DB=B")
    lines = out.splitlines()
    assert lines[1] == "note: --db A overrides PEERHUB_DB=B"


# --- CLI -----------------------------------------------------------------------------------------

def test_cli_diag_prints_note_when_env_points_elsewhere(tmp_path, capsys, monkeypatch):
    a, b = _store(tmp_path / "wsA"), _store(tmp_path / "wsB")
    capsys.readouterr()
    monkeypatch.setenv("PEERHUB_DB", str(b))
    main(["--db", str(a), "diag"])
    out = capsys.readouterr().out
    assert f"{a} (explicit)" in out.splitlines()[0]
    note = [line for line in out.splitlines() if line.startswith("note:")]
    assert len(note) == 1 and str(a) in note[0] and str(b) in note[0]


def test_cli_diag_no_note_without_env(tmp_path, capsys):
    a = _store(tmp_path / "wsA")
    capsys.readouterr()
    main(["--db", str(a), "diag"])
    out = capsys.readouterr().out
    assert str(a) in out and "note:" not in out


def test_cli_diag_json_names_store(tmp_path, capsys, monkeypatch):
    a = _store(tmp_path / "wsA")
    capsys.readouterr()
    monkeypatch.setenv("PEERHUB_DB", str(a))
    main(["diag", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["store_selection"] == {"path": str(a), "source": "env"}
    assert {"schema_version", "status", "read_at", "sections", "threats"} <= set(data)  # additive only


def test_cli_monitor_snapshot_prints_note_and_path(tmp_path, capsys, monkeypatch):
    a, b = _store(tmp_path / "wsA"), _store(tmp_path / "wsB")
    capsys.readouterr()
    monkeypatch.setenv("PEERHUB_DB", str(b))
    main(["--db", str(a), "monitor", "--snapshot", "--view", "plain"])
    out = capsys.readouterr().out
    assert sum(line.startswith("note:") for line in out.splitlines()) == 1
    flat = "".join(out[out.index("note:"):].split())  # compact monitor caps width at 99 and wraps long tmp paths
    assert str(a) in flat and str(b) in flat


def test_cli_diag_relative_db_shows_resolved_path(tmp_path, capsys, monkeypatch):
    a = _store(tmp_path / "wsA")
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)
    main(["--db", "wsA/.peerhub/core.db", "diag"])
    assert f"{a.resolve()} (explicit)" in capsys.readouterr().out.splitlines()[0]
    main(["--db", "wsA/.peerhub/core.db", "diag", "--json"])
    assert json.loads(capsys.readouterr().out)["store_selection"]["path"] == str(a.resolve())


def test_cli_monitor_expand_wraps_path_and_note(tmp_path, capsys, monkeypatch):
    a, b = _store(tmp_path / "wsA"), _store(tmp_path / "wsB")
    capsys.readouterr()
    monkeypatch.setenv("COLUMNS", "80")  # narrower than the tmp paths: must wrap, never truncate
    monkeypatch.setenv("PEERHUB_DB", str(b))
    main(["--db", str(a), "monitor", "--snapshot", "--expand", "--view", "plain"])
    out = capsys.readouterr().out
    assert all(len(line) <= 80 for line in out.splitlines())
    head = "".join(out[:out.index("note:")].split())
    assert str(a) in head  # the DB line before the note
    flat = "".join(out[out.index("note:"):].split())
    assert str(a) in flat and str(b) in flat


def test_cli_monitor_snapshot_json_keeps_store_selection(tmp_path, capsys):
    a = _store(tmp_path / "wsA")
    capsys.readouterr()
    main(["--db", str(a), "monitor", "--snapshot", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["store_selection"] == {"path": str(a), "source": "explicit"}
