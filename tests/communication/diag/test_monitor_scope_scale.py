"""Grouped scope selection remains bounded when peer scope counts are skewed."""

from __future__ import annotations

import sqlite3
import pytest

from peerhub.extensions import monitor_read_model
from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
from tests.communication.bridge_helpers import bseed
from tests.communication.harness.bridge import BridgeHarness

pytestmark = [pytest.mark.unit, pytest.mark.diag, pytest.mark.platform_sensitive]


def test_grouped_frame_scope_query_seeks_past_unrelated_scopes(tmp_path, monkeypatch):
    h = BridgeHarness(tmp_path / "workspace")
    bseed(h)
    h.delivery_cycle("b", "s", _ProviderTelemetryRuntime())

    with h.cs.transaction() as conn:
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(bridge_monitor_delivery_scope)")]
        template = list(conn.execute(
            "SELECT * FROM bridge_monitor_delivery_scope WHERE peer_id='b' AND stream_id='s'"
        ).fetchone())
        insert = (f"INSERT INTO bridge_monitor_delivery_scope ({','.join(columns)}) "
                  f"VALUES ({','.join('?' for _ in columns)})")

        def scope(stream: str, peer: str):
            values = template.copy()
            values[0], values[1] = stream, peer
            return values

        conn.executemany(insert, (scope(f"{peer}-{n:03d}", peer)
                                  for peer in ("a", "b", "c") for n in range(21)))
        conn.execute(insert, scope("d-000", "d"))  # fourth-peer sentinel

    original_connect = sqlite3.connect

    def frame_and_work():
        ticks = [0]
        statements: list[str] = []

        def connect(*args, **kwargs):
            conn = original_connect(*args, **kwargs)
            conn.set_progress_handler(lambda: ticks.__setitem__(0, ticks[0] + 1) or 0, 100)
            return conn

        with monkeypatch.context() as patch:
            patch.setattr(monitor_read_model.sqlite3, "connect", connect)
            frame = monitor_read_model.render_monitor_frame(
                h.db_path, ["execution"], read_at=1792022400.0, trace_callback=statements.append
            )
        peer_sql = [s for s in statements if s.lstrip().upper().startswith("WITH RECURSIVE")
                    and "bridge_monitor_delivery_scope" in s]
        scope_sql = [s for s in statements if s.lstrip().upper().startswith("WITH")
                     and "bridge_monitor_delivery_day" in s]
        assert len(peer_sql) == len(scope_sql) == 1  # inspect both actual frame queries
        assert frame["query_families"] <= 6
        scopes = frame["groups"]["execution"]["data"]["scopes"]
        assert [(s["peer_id"], s["stream_id"]) for s in scopes] == [
            (peer, f"{peer}-{n:03d}") for peer in ("a", "b", "c") for n in range(20)
        ]
        assert frame["groups"]["execution"]["data"]["truncated"] is True
        return ticks[0] * 100, peer_sql[0], scope_sql[0]

    baseline_work, _, _ = frame_and_work()
    with h.cs.transaction() as conn:
        conn.executemany(insert, (scope(f"a-zzzz-{n:05d}", "a") for n in range(20_000)))
    skewed_work, actual_peer_sql, actual_scope_sql = frame_and_work()

    with sqlite3.connect(h.db_path) as conn:
        plan_steps = [str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + actual_scope_sql)]
        peer_plan_steps = [str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + actual_peer_sql)]
    assert any("idx_monitor_delivery_peer" in step for step in plan_steps)
    assert any("idx_monitor_delivery_peer" in step for step in peer_plan_steps), peer_plan_steps

    # Indexed peer/scope seeks keep VM work close to the bounded baseline.
    assert skewed_work <= baseline_work * 3 + 25_000, (baseline_work, skewed_work)
    assert not any("SCAN bridge_monitor_delivery_scope" in step for step in plan_steps), plan_steps
    assert not any("SCAN bridge_monitor_delivery_scope" in step for step in peer_plan_steps), peer_plan_steps
