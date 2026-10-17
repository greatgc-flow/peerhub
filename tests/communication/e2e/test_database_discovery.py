"""Store discovery: nearest workspace, explicit choices, and provenance."""
from pathlib import Path

from peerhub.cli.app import DEFAULT_DB_PATH, _resolve_db_path
from peerhub.cli.store_select import select_store
from peerhub.core.models import Peer
from peerhub.core.store import CoreStore


def seed(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    CoreStore(path).register_peer(Peer(peer_id="existing"))
    return path


def test_nearest_workspace_store_wins_over_parent_workspace(tmp_path, monkeypatch):
    seed(tmp_path / DEFAULT_DB_PATH)
    project = tmp_path / "project"
    near = seed(project / DEFAULT_DB_PATH)
    nested = project / "src"
    nested.mkdir()
    monkeypatch.chdir(nested)
    monkeypatch.delenv("PEERHUB_DB", raising=False)
    assert Path(_resolve_db_path(DEFAULT_DB_PATH)) == near


def test_workspace_root_discovery_never_escapes_its_owner(tmp_path, monkeypatch):
    seed(tmp_path / DEFAULT_DB_PATH)
    provider = tmp_path / "provider"
    provider.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PEERHUB_DB", raising=False)
    assert Path(_resolve_db_path(DEFAULT_DB_PATH, workspace_root=provider)) == provider / DEFAULT_DB_PATH


def test_explicit_observation_path_ignores_primary_db_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PEERHUB_DB", "primary.db")
    assert _resolve_db_path("observations.db", use_env=False, discover=False) == "observations.db"


def test_selection_reports_how_the_store_was_chosen(tmp_path):
    assert select_store("x.db", env={"PEERHUB_DB": "e.db"}).source == "env"
    assert select_store("x.db", env={}).source == "explicit"
    assert select_store(DEFAULT_DB_PATH, env={}, cwd=tmp_path).source == "default"
    seed(tmp_path / DEFAULT_DB_PATH)
    (tmp_path / "deep").mkdir()
    found = select_store(DEFAULT_DB_PATH, env={}, cwd=tmp_path / "deep")
    assert found.source == "discovered" and found.path.endswith("core.db")
    assert select_store(DEFAULT_DB_PATH, env={}, workspace_root=tmp_path / "fresh").source == "workspace-default"


import pytest


@pytest.fixture
def polluted_subfolder_workspace(tmp_path: Path):
    """Pollution fixture: workspace with an ambient ./peerhub/.peerhub/core.db subfolder."""
    sub_store = tmp_path / "peerhub" / DEFAULT_DB_PATH
    seed(sub_store)
    return tmp_path, sub_store


def test_implicit_peerhub_subfolder_is_not_discovered(polluted_subfolder_workspace, monkeypatch):
    """O-1: Remove implicit ./peerhub sub-folder lookup in store_select.py.

    Walking into ./peerhub breaks root-rename resilience and causes workspace store pollution.
    """
    ws, sub_store = polluted_subfolder_workspace
    monkeypatch.chdir(ws)
    monkeypatch.delenv("PEERHUB_DB", raising=False)
    # Without an explicit path or cwd .peerhub/core.db, it must NOT walk into ./peerhub
    selection = select_store(DEFAULT_DB_PATH, env={}, cwd=ws)
    assert selection.source == "default"
    assert selection.path != str(sub_store)

    # Resolving path via CLI helper returns default path, NOT the subfolder store
    resolved = _resolve_db_path(DEFAULT_DB_PATH)
    assert resolved == DEFAULT_DB_PATH
    assert Path(resolved) != sub_store


def test_explicit_env_and_workspace_selection_preserved(polluted_subfolder_workspace, monkeypatch):
    ws, sub_store = polluted_subfolder_workspace
    monkeypatch.chdir(ws)
    # Explicit --db wins
    assert select_store("explicit.db", env={}, cwd=ws).source == "explicit"
    # PEERHUB_DB environment wins
    assert select_store(DEFAULT_DB_PATH, env={"PEERHUB_DB": "env.db"}, cwd=ws).source == "env"
    # Workspace root selection is preserved
    assert select_store(DEFAULT_DB_PATH, env={}, workspace_root=ws / "fresh").source == "workspace-default"
    # CWD .peerhub/core.db is still discovered
    cwd_store = seed(ws / DEFAULT_DB_PATH)
    discovered = select_store(DEFAULT_DB_PATH, env={}, cwd=ws)
    assert discovered.source == "discovered"
    assert discovered.path == str(cwd_store)


import json
import sqlite3
from peerhub.cli.app import main


def test_legacy_store_present_emits_hint_line(polluted_subfolder_workspace, monkeypatch, capsys):
    ws, sub_store = polluted_subfolder_workspace
    monkeypatch.chdir(ws)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    code = main(["peer", "list"])
    assert code == 5
    out, err = capsys.readouterr()
    expected_hint = (
        f'Ignored legacy store: {sub_store.resolve()} (no longer auto-discovered). '
        f'Use --db "{sub_store.resolve()}" to keep using it; stores of other schema versions are refused as usual.'
    )
    assert expected_hint in err
    assert f"database not found: {DEFAULT_DB_PATH}" in err

    # With JSON format, exit code 5 and JSON payload remain unchanged while stderr retains the hint
    code_json = main(["peer", "list", "--format", "json"])
    assert code_json == 5
    out_json, err_json = capsys.readouterr()
    payload = json.loads(out_json)
    assert payload["status"] == "UNAVAILABLE"
    assert "database not found" in payload["error"]
    assert expected_hint in err_json


def test_legacy_store_flat_filename_emits_hint_line(tmp_path, monkeypatch, capsys):
    flat_store = tmp_path / "peerhub" / "core.db"
    seed(flat_store)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    code = main(["peer", "list"])
    assert code == 5
    _, err = capsys.readouterr()
    expected_hint = (
        f'Ignored legacy store: {flat_store.resolve()} (no longer auto-discovered). '
        f'Use --db "{flat_store.resolve()}" to keep using it; stores of other schema versions are refused as usual.'
    )
    assert expected_hint in err


def test_legacy_store_absent_emits_no_hint_line(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    code = main(["peer", "list"])
    assert code == 5
    _, err = capsys.readouterr()
    assert "Ignored legacy store:" not in err
    assert f"database not found: {DEFAULT_DB_PATH}" in err


def test_source_package_dir_without_store_file_emits_no_hint_line(tmp_path, monkeypatch, capsys):
    # Source package dir with Python code but no store file
    pkg_dir = tmp_path / "peerhub"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("# package\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    code = main(["peer", "list"])
    assert code == 5
    _, err = capsys.readouterr()
    assert "Ignored legacy store:" not in err
    assert f"database not found: {DEFAULT_DB_PATH}" in err


def test_explicit_db_to_legacy_store_works_and_preserves_schema_gate(polluted_subfolder_workspace, monkeypatch, capsys):
    ws, sub_store = polluted_subfolder_workspace
    monkeypatch.chdir(ws)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    # Explicit --db to legacy store works
    code = main(["--db", str(sub_store), "peer", "list"])
    assert code == 0
    out, err = capsys.readouterr()
    assert "existing" in out
    assert "Ignored legacy store:" not in err

    # Other schema version is refused as usual (schema gate unchanged)
    with sqlite3.connect(sub_store) as conn:
        conn.execute("PRAGMA user_version = 999")
    code_schema_err = main(["--db", str(sub_store), "peer", "list"])
    assert code_schema_err == 6
    _, err_schema = capsys.readouterr()
    assert "SCHEMA VERSION ERROR" in err_schema


def test_explicit_missing_db_with_legacy_store_present_emits_no_hint_line(polluted_subfolder_workspace, monkeypatch, capsys):
    ws, _ = polluted_subfolder_workspace
    monkeypatch.chdir(ws)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    code = main(["--db", "nonexistent.db", "peer", "list"])
    assert code == 5
    _, err = capsys.readouterr()
    assert "database not found: nonexistent.db" in err
    assert "Ignored legacy store:" not in err
