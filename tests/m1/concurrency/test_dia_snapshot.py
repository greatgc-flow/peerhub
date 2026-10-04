"""Wave 5 DIA-008: Diag renders ONE SQLite snapshot while a real concurrent writer commits (barrier-synchronised, bounded waits)."""
import sqlite3
import threading
from contextlib import closing

import pytest

from peerhub.extensions.diag import ReadonlyDiag
from tests.m1.fakes.observation import FakeObservationSource, measured
from tests.m1.harness.observation import ObservationHarness
from tests.m1.helpers import req

pytestmark = [pytest.mark.concurrency, pytest.mark.diag, pytest.mark.sqlite]
SECTION_ORDER = ["peers", "streams", "resource_pools", "observations", "log"]
WAIT = 30.0


def seed(tmp_path):
    h = ObservationHarness(tmp_path / "ws")
    for p in ("a", "b"):
        h.create_peer({"peer_id": p})
    h.create_stream({"stream_id": "s", "members": ["a", "b"]})
    for i in range(2):
        h.append_record(req(body=i, key=f"k{i}"))
    h.cas_offset("b", "s", 1, 1)
    h.capture("peer:b", "quota", FakeObservationSource(measured({"remaining_fraction": 0.5})))
    return h


def raw_view(db):
    with closing(sqlite3.connect(db)) as c:
        return {"records": c.execute("SELECT COUNT(*) FROM records").fetchone()[0],
                "head": c.execute("SELECT MAX(position) FROM records WHERE stream_id='s'").fetchone()[0],
                "offset_b": c.execute("SELECT read_through_position FROM offsets WHERE peer_id='b'").fetchone()[0],
                "observations": c.execute("SELECT COUNT(*) FROM observations").fetchone()[0]}


def summarize(report):
    st = report.sections["streams"].data["streams"][0]
    return {"records": report.snapshot["records_total"], "head": st["head_position"], "offset_b": st["offsets"]["b"],
            "observations": report.snapshot["observations_total"]}


@pytest.mark.m1_id("DIA-008")
@pytest.mark.parametrize("pause_after", SECTION_ORDER[:3])
def test_dia_008_one_consistent_snapshot_during_concurrent_writes(tmp_path, pause_after):
    h = seed(tmp_path)
    old = raw_view(h.db_path)
    assert old == {"records": 2, "head": 2, "offset_b": 1, "observations": 1}
    reached, written = threading.Event(), threading.Event()
    errors = []

    def writer():
        try:
            assert reached.wait(WAIT), "reader never reached the barrier"
            w = ObservationHarness(tmp_path / "ws")  # independent connections, same workspace
            w.ids.prefix = "writer"
            for i in range(2, 5):
                w.append_record(req(body=i, key=f"k{i}"))
            w.cas_offset("b", "s", 2, 4)
            w.capture("peer:b", "quota", FakeObservationSource(measured({"remaining_fraction": 0.1})))
        except BaseException as e:  # surfaced in the main thread
            errors.append(e)
        finally:
            written.set()

    def hook(section):
        if section == pause_after:
            reached.set()
            assert written.wait(WAIT), "writer did not finish while the reader held its snapshot"

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    report = ReadonlyDiag(h.db_path, section_hook=hook).render()
    t.join(WAIT)
    assert not t.is_alive() and errors == [] and reached.is_set() and written.is_set()
    new = raw_view(h.db_path)
    assert new == {"records": 5, "head": 5, "offset_b": 4, "observations": 2}  # the writer really committed
    assert report.status == "OK"
    assert summarize(report) == old  # all sections from the OLD snapshot: never mixed old/new cross-table state
    items = report.sections["observations"].data["items"]
    assert [i["payload"] for i in items] == [{"remaining_fraction": 0.5}]
    # a render after the writer is entirely the NEW snapshot (positive control, not stuck on old data)
    assert summarize(ReadonlyDiag(h.db_path).render()) == new


@pytest.mark.m1_id("DIA-008")
def test_dia_008_writer_is_never_blocked_by_the_diag_reader(tmp_path):
    """The reader holds its snapshot open; a writer commit must succeed within the bounded wait (WAL, no reader lock)."""
    h = seed(tmp_path)
    done = threading.Event()

    def hook(section):
        if section == "peers":
            def w():
                ObservationHarness(tmp_path / "ws").append_record(req(body="late", key="late"))
                done.set()
            t = threading.Thread(target=w, daemon=True)
            t.start()
            assert done.wait(WAIT), "writer blocked by a read-only Diag snapshot"
            t.join(WAIT)

    rep = ReadonlyDiag(h.db_path, section_hook=hook).render()
    assert rep.status == "OK" and summarize(rep)["records"] == 2 and raw_view(h.db_path)["records"] == 3
