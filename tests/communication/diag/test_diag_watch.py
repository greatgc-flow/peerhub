import json
import math

import pytest

from peerhub.cli.app import main
from peerhub.core.models import Peer
from peerhub.core.store import CoreStore
from peerhub.extensions.diag_watch import dashboard_snapshots
from peerhub.extensions.observation import ObservationStore


def test_watch_reads_new_snapshot_without_mutating_or_collecting(tmp_path):
    db = tmp_path / "core.db"
    store = CoreStore(db)
    ObservationStore(db)
    pauses = []
    def between(seconds):
        pauses.append(seconds)
        store.register_peer(Peer(peer_id="new"))  # independent writer, not watcher
    frames = list(dashboard_snapshots(db, interval_s=.01, count=2, sleep=between))
    assert pauses == [.01]
    assert frames[0].sections["peers"].data["peers"] == []
    assert frames[1].sections["peers"].data["peers"][0]["peer_id"] == "new"
    assert frames[1].sections["observations"].data["items"] == []


@pytest.mark.parametrize("interval,count", [(0, 1), (-1, 1), (math.nan, 1), (math.inf, 1), (1, -1), (1, True)])
def test_watch_rejects_invalid_bounds_without_creating_store(tmp_path, interval, count):
    db = tmp_path / "missing.db"
    with pytest.raises(ValueError):
        list(dashboard_snapshots(db, interval_s=interval, count=count))
    assert not db.exists()


def test_live_json_is_ndjson_and_preserves_db_bytes(tmp_path, capsys):
    db = tmp_path / "core.db"
    CoreStore(db)
    ObservationStore(db)
    before = db.read_bytes()
    assert main(["--db", str(db), "diag", "--live", "--json", "--cycles", "2", "--interval-seconds", ".001"]) == 0
    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(frames) == 2
    assert all(frame["status"] == "OK" for frame in frames)
    assert db.read_bytes() == before


def test_missing_live_store_fails_once_without_bootstrap(tmp_path, capsys):
    db = tmp_path / "missing.db"
    assert main(["--db", str(db), "diag", "--live", "--json"]) == 5
    assert json.loads(capsys.readouterr().out)["status"] == "FAILED"
    assert not db.exists()


@pytest.mark.parametrize("options", [["--cycles", "2"], ["--interval-seconds", "1"], ["--live", "health"]])
def test_watch_controls_never_become_inert(tmp_path, options):
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(tmp_path / "missing.db"), "diag", *options])
    assert exc.value.code == 2
