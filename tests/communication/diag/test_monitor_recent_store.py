"""The read model joins each recent ask to its stream's newest session, and reads the ask's own cache tokens."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import Any

import pytest

from peerhub.extensions import bridge_registry, delivery_projections
from peerhub.extensions import monitor_read_model as mrm
from peerhub.extensions.session_receipts import CONTEXT_SOURCE
from tests.communication.diag.test_monitor_activity import (  # noqa: F401
    NOW, caller, claim, db, delivery, frame, iso)

pytestmark = [pytest.mark.integration, pytest.mark.diag]

USAGE = {"input_tokens": 41_000, "cached_input_tokens": 38_000, "cache_write_tokens": 3_000, "output_tokens": 2_000}


def sessions_schema(db) -> None:
    with closing(sqlite3.connect(db)) as c:
        for stmt in (*bridge_registry.DDL, *delivery_projections.DDL):
            c.execute(stmt)
        c.commit()


def session_row(db, stream: str, peer: str, generation: int, *, reuse: int | None = 2, fresh: int | None = 1,
                tombstoned: bool = False, provider: str = "cc", context: tuple[int, int] | None = (62_000, 1_000_000),
                delivery_id: str | None = None) -> None:
    handle = f"h-{stream}-{peer}-{generation}"
    seq = None
    with closing(sqlite3.connect(db)) as c:
        c.execute("INSERT INTO bridge_session_history(session_handle,stream_id,peer_id,runtime_kind,external_session_id,"
                  "session_generation,adapter_fingerprint,binding,resumable,state,last_seen,created_at,tombstoned_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (handle, stream, peer, provider, f"ext-{handle}", generation, "fp", '{"profile":"cc.effort"}', 1,
                   "TOMBSTONED" if tombstoned else "ACTIVE", NOW - 50 + generation, NOW - 100, NOW - 10 if tombstoned else None))
        if context is not None and delivery_id is not None:
            tokens, window = context
            detail = {"schema_version": 3, "handle": handle, "generation": generation, "provider_kind": provider,
                      "source": CONTEXT_SOURCE, "last_input_tokens": tokens, "context_window": window,
                      "context_pct": tokens * 100 // window, "observed_at": NOW - 100, "window_reported_at": NOW - 100}
            cur = c.execute("INSERT INTO bridge_evidence (delivery_id,stream_id,peer_id,kind,certainty,detail) "
                            "VALUES (?,?,?,?,?,?)", (delivery_id, stream, peer, "session_telemetry", None, json.dumps(detail)))
            seq = cur.lastrowid
        c.execute("INSERT INTO bridge_session_monitor_stats(session_handle,reuse_count,fresh_count,counts_complete,binding_json,"
                  "latest_telemetry_seq) VALUES (?,?,?,?,?,?)", (handle, reuse, fresh, 1, "{}", seq))
        c.commit()


def finished(db, stream: str, peer: str = "cc", *, age: float = 100.0, usage: dict[str, Any] | None = USAGE) -> str:
    claim(db, stream, peer, expires=NOW - age)
    result = {"response": "VERDICT: OK", "error": None, **({"usage": usage} if usage is not None else {})}
    return delivery(db, stream, peer, certainty="TERMINAL", result=result, terminal_at=iso(NOW - age),
                    caller_detail=caller(), binding="cc.effort/claude-opus-4/high#0123456789ab")


def recent(db) -> dict[str, dict[str, Any]]:
    out = mrm.render_monitor_frame(db, ["activity"], read_at=NOW, liveness=lambda *a, **k: True)
    return {a["name"]: a for a in out["groups"]["activity"]["data"]["asks"]}


def test_an_ask_row_carries_cache_tokens_and_the_session_of_its_stream(db):
    sessions_schema(db)
    did = finished(db, "s-one")
    session_row(db, "s-one", "cc", 1, reuse=0, fresh=1, context=None)
    session_row(db, "s-one", "cc", 2, reuse=2, fresh=1, delivery_id=did)  # the newest generation answers
    row = recent(db)["s-one"]
    assert row["tokens"] == {"input": 41_000, "output": 2_000, "cache_read": 38_000, "cache_write": 3_000}
    assert row["session"]["generation"] == 2 and row["session"]["execution_mode"] == "reused" and row["session"]["uses"] == 3
    assert row["session"]["context"]["percent"] == 6 and row["session"]["context"]["occupancy_tokens"] == 62_000
    assert row["session"]["context"]["window_tokens"] == 1_000_000


def test_a_fresh_session_and_a_missing_one_are_none_fields_not_zeros(db):
    sessions_schema(db)
    did = finished(db, "s-fresh")
    session_row(db, "s-fresh", "cc", 1, reuse=0, fresh=1, context=None, delivery_id=did)
    finished(db, "s-none")
    rows = recent(db)
    assert rows["s-fresh"]["session"]["execution_mode"] == "fresh" and rows["s-fresh"]["session"]["uses"] == 1
    assert rows["s-fresh"]["session"]["context"]["percent"] is None
    assert rows["s-none"]["session"] is None


def test_a_tombstoned_stream_has_no_session(db):
    sessions_schema(db)
    did = finished(db, "s-gone")
    session_row(db, "s-gone", "cc", 1, tombstoned=True, delivery_id=did)
    assert recent(db)["s-gone"]["session"] is None


def test_the_join_does_not_depend_on_the_visible_session_sample(db):
    """SESSIONS shows the newest 100 sessions only; an ask whose session is older still resolves."""
    sessions_schema(db)
    did = finished(db, "s-old", age=3000.0)
    session_row(db, "s-old", "cc", 1, delivery_id=did)
    with closing(sqlite3.connect(db)) as c:
        c.executemany(
            "INSERT INTO bridge_session_history(session_handle,stream_id,peer_id,runtime_kind,external_session_id,"
            "session_generation,adapter_fingerprint,binding,resumable,state,last_seen,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(f"x-{i}", f"x{i}", "cc", "cc", f"e{i}", 1, "fp", "{}", 1, "ACTIVE", NOW + i, NOW) for i in range(130)])
        c.commit()
    out = mrm.render_monitor_frame(db, ["activity", "sessions"], read_at=NOW, liveness=lambda *a, **k: True)
    listed = {item["stream_id"] for item in out["groups"]["sessions"]["data"]["items"]}
    assert "s-old" not in listed and out["groups"]["sessions"]["data"]["truncated"] is True
    ask = next(a for a in out["groups"]["activity"]["data"]["asks"] if a["name"] == "s-old")
    assert ask["session"] is not None and ask["session"]["generation"] == 1 and ask["session"]["uses"] == 3


def test_the_join_adds_no_select_statement(db):
    sessions_schema(db)
    did = finished(db, "s-one")
    session_row(db, "s-one", "cc", 1, delivery_id=did)
    out = mrm.render_monitor_frame(db, ["activity"], read_at=NOW, liveness=lambda *a, **k: True)
    assert out["query_families"] == 1


@pytest.mark.parametrize("dropped", [("bridge_session_monitor_stats",), ("bridge_session_history", "bridge_session_monitor_stats")])
def test_a_store_without_the_session_tables_still_reads_activity(db, dropped):
    with closing(sqlite3.connect(db)) as c:  # a store from before the registry: only the Bridge asks exist
        for table in dropped:
            c.execute(f"DROP TABLE {table}")
        c.commit()
    finished(db, "s-bare")
    out = mrm.render_monitor_frame(db, ["activity"], read_at=NOW, liveness=lambda *a, **k: True)
    row = next(a for a in out["groups"]["activity"]["data"]["asks"] if a["name"] == "s-bare")
    assert out["groups"]["activity"]["status"] == "OK" and not out["errors"]
    assert row["session"] is None and row["tokens"]["cache_read"] == 38_000


def test_a_result_without_cache_counters_gives_none_fields(db):
    finished(db, "s-plain", usage={"input_tokens": 10, "output_tokens": 5})
    assert recent(db)["s-plain"]["tokens"] == {"input": 10, "output": 5, "cache_read": None, "cache_write": None}
