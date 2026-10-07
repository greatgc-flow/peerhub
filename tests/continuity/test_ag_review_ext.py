"""AG review triage regressions for extensions: bounded artifact indexing, evaluator strictness, explicit clear
failures, telemetry durability."""
import os
import sqlite3
import tracemalloc
from pathlib import Path

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore
from peerhub.extensions import eval as eval_mod
from peerhub.extensions.artifact import ArtifactStore, ArtifactTamperedError
from peerhub.extensions.eval import (EvalDataset, ExactMatchEvaluator, ExecutionTrace, JsonLinesTelemetrySink, TraceSpan)
from peerhub.extensions.search import SearchIndex


def _core(tmp_path):
    store = CoreStore(tmp_path / "core.db")
    store.register_peer(Peer(peer_id="a"))
    store.create_stream(Stream(stream_id="s", members=["a"]))
    return store


# (11) artifact indexing is bounded in memory but still verified
def test_index_rebuild_never_loads_whole_artifact_and_keeps_tamper_detection(tmp_path, monkeypatch):
    core = _core(tmp_path)
    store = ArtifactStore(tmp_path / "artifacts")
    big = b"headneedle " + b"x" * (4 * 1024 * 1024) + b" tailneedle"
    digest = store.commit_staged(store.stage_bytes(big))
    index = SearchIndex(tmp_path / "search.db")

    def forbidden(self, *a, **k):
        raise AssertionError("whole-blob read")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(ArtifactStore, "read_bytes", forbidden)
    tracemalloc.start()
    try:
        index.rebuild_from_sources(core, store)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < len(big) // 2  # a full read alone would exceed the blob size
    hit = index.search("headneedle")
    assert len(hit) == 1 and hit[0].source_ref == f"artifact:{digest}"
    assert str(len(big)) in str(hit[0].metadata)  # size comes from the streamed total, not len(data)
    assert index.search("tailneedle") == []  # only the 64 KiB prefix is indexed (unchanged behavior)
    monkeypatch.undo()
    # tamper at the END of the blob (beyond the indexed prefix) is still detected, and the old index survives
    path = store.resolve_path(digest)
    path.chmod(0o600)
    with open(path, "r+b") as f:
        f.seek(-3, os.SEEK_END)
        f.write(b"ZZZ")
    with pytest.raises(ArtifactTamperedError):
        index.rebuild_from_sources(core, store)
    assert len(index.search("headneedle")) == 1


def test_read_verified_prefix_matches_read_bytes_oracle(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    data = bytes(range(256)) * 1000
    digest = store.commit_staged(store.stage_bytes(data))
    for limit in (0, 1, 70000, len(data), len(data) + 5):
        prefix, size = store.read_verified_prefix(digest, limit, chunk_size=4096)
        assert prefix == data[:limit] and size == len(data)
    with pytest.raises(ValueError):
        store.read_verified_prefix(digest, -1)


# (13) evaluator strictness
def _trace(*attrs):
    return ExecutionTrace("t", [TraceSpan(f"s{i}", None, "n", 0, 0, a) for i, a in enumerate(attrs)], {}, "d")


def _dataset(*expected):
    return EvalDataset("d", "d", len(expected), "now", tuple({"expected": e} for e in expected))


def test_exact_match_missing_attribute_is_not_an_empty_output():
    r = ExactMatchEvaluator().evaluate(_trace({}), _dataset(""))
    assert r["verdict"] == "FAILED" and r["evidence"]["matched"] == 0
    ok = ExactMatchEvaluator().evaluate(_trace({"output": ""}), _dataset(""))  # control: a real empty output matches
    assert ok["verdict"] == "PASSED" and ok["evidence"]["matched"] == 1


def test_exact_match_one_span_satisfies_one_expected_item_only():
    r = ExactMatchEvaluator().evaluate(_trace({"output": "x"}), _dataset("x", "x"))
    assert r["verdict"] == "FAILED" and r["evidence"] == {"matched": 1, "total": 2}
    ok = ExactMatchEvaluator().evaluate(_trace({"output": "x"}, {"output": "x"}), _dataset("x", "x"))
    assert ok["verdict"] == "PASSED" and ok["evidence"]["matched"] == 2


def test_exact_match_item_without_expected_key_is_not_vacuously_matched():
    ds = EvalDataset("d", "d", 1, "now", ({"input": "q"},))
    r = ExactMatchEvaluator().evaluate(_trace({"output": ""}), ds)
    assert r["verdict"] == "FAILED" and r["evidence"]["matched"] == 0


# (14) clear_path failures are explicit
def test_clear_path_propagates_unlink_failure_and_clears_when_possible(tmp_path, monkeypatch):
    db = tmp_path / "search.db"
    SearchIndex(db)
    for suffix in ("-wal", "-shm"):
        Path(str(db) + suffix).write_bytes(b"x")
    real = Path.unlink

    def locked(self, missing_ok=False):
        if self.name == "search.db":
            raise PermissionError("file is locked")
        return real(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", locked)
    with pytest.raises(PermissionError, match="locked"):
        SearchIndex.clear_path(db)
    assert db.exists()  # nothing falsely reported as cleared
    monkeypatch.undo()
    SearchIndex.clear_path(db)  # positive control
    assert not db.exists() and not Path(str(db) + "-wal").exists() and not Path(str(db) + "-shm").exists()
    SearchIndex.clear_path(db)  # idempotent on missing files


# (16) telemetry durability
def test_telemetry_emit_flushes_and_fsyncs_each_line(tmp_path, monkeypatch):
    out = tmp_path / "t.jsonl"
    synced = []
    real = os.fsync
    monkeypatch.setattr(eval_mod.os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    sink = JsonLinesTelemetrySink(out)
    sink.emit({"a": 1})
    sink.emit({"b": 2})
    assert len(synced) == 2
    assert out.read_text(encoding="utf-8").splitlines() == ['{"a":1}', '{"b":2}']
