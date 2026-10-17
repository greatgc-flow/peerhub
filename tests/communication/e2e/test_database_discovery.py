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
