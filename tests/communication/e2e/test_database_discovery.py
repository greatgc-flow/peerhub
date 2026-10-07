"""Naming cleanup must never orphan existing stores or move live SQLite/WAL files."""
from pathlib import Path

import pytest

from peerhub.cli.app import DEFAULT_DB_PATH, _resolve_db_path, main
from peerhub.core.models import Peer
from peerhub.core.store import CoreStore


def seed(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    CoreStore(path).register_peer(Peer(peer_id="existing"))
    return path


def test_previous_default_is_discovered_without_rename_or_new_store(tmp_path, monkeypatch):
    old = seed(tmp_path / ".peerhub" / "m1.db")
    monkeypatch.chdir(tmp_path)
    before = old.read_bytes()
    assert Path(_resolve_db_path(DEFAULT_DB_PATH)) == old
    assert old.read_bytes() == before
    assert not (tmp_path / DEFAULT_DB_PATH).exists()


def test_both_names_in_one_workspace_are_ambiguous_and_refused(tmp_path, monkeypatch):
    seed(tmp_path / ".peerhub" / "m1.db")
    seed(tmp_path / DEFAULT_DB_PATH)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _resolve_db_path(DEFAULT_DB_PATH)
    assert exc.value.code == 2  # neither file may silently hide the other's data


def test_nearest_previous_store_wins_over_parent_workspace(tmp_path, monkeypatch):
    seed(tmp_path / DEFAULT_DB_PATH)
    project = tmp_path / "project"
    old = seed(project / ".peerhub" / "m1.db")
    nested = project / "src"
    nested.mkdir()
    monkeypatch.chdir(nested)
    assert Path(_resolve_db_path(DEFAULT_DB_PATH)) == old


def test_explicit_missing_default_is_not_redirected_to_existing_old_store(tmp_path, monkeypatch, capsys):
    seed(tmp_path / ".peerhub" / "m1.db")
    monkeypatch.chdir(tmp_path)
    assert main(["--db", DEFAULT_DB_PATH, "diag", "--json"]) == 5
    assert not Path(DEFAULT_DB_PATH).exists()


def test_workspace_root_discovery_never_escapes_its_owner(tmp_path, monkeypatch):
    seed(tmp_path / DEFAULT_DB_PATH)
    provider = tmp_path / "provider"
    provider.mkdir()
    monkeypatch.chdir(tmp_path)
    assert Path(_resolve_db_path(DEFAULT_DB_PATH, workspace_root=provider)) == provider / DEFAULT_DB_PATH


def test_explicit_observation_path_ignores_primary_db_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PEERHUB_DB", "primary.db")
    assert _resolve_db_path("observations.db", use_env=False, discover=False) == "observations.db"
