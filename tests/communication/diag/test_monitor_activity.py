"""`activity` group of the monitor read model (#374 S1): one claims-first statement, states, text rule, liveness, caps."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from peerhub.extensions import bridge_claims, bridge_registry, bridge_schema, delivery_projections
from peerhub.extensions import monitor_queries, monitor_read_model as mrm
from peerhub.extensions import monitor_activity, monitor_values
from tests.communication.harness.core import CoreHarness

pytestmark = [pytest.mark.integration, pytest.mark.diag]

NOW = 1_790_812_800.0
BINDING = "cx.standard/test-model/low#0123456789ab"
START_KINDS = ("attempt_created", "about_to_invoke", "started")


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def db(tmp_path):
    h = CoreHarness(tmp_path / "ws")
    with closing(sqlite3.connect(h.db_path)) as c:
        for stmt in bridge_schema._DDL:  # pyright: ignore[reportPrivateUsage]
            c.execute(stmt)
        c.execute(bridge_claims._DDL)  # pyright: ignore[reportPrivateUsage]
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_be_session_call_once ON bridge_evidence(delivery_id) WHERE kind='session_call'")
        for stmt in (*bridge_registry.DDL, *delivery_projections.DDL):  # the tables RECENT's session join reads (#412)
            c.execute(stmt)
        c.commit()
    return h.db_path


def wsgen(db) -> str:
    return Path(db).parent.joinpath("workspace.generation").read_text(encoding="utf-8")


def caller(**kw: Any) -> dict[str, Any]:
    base = {"v": 1, "tool": "cc", "label": "peerhub", "session": "sess-raw-1", "host": monitor_activity._this_host(),  # pyright: ignore[reportPrivateUsage]
            "ns": monitor_activity._this_ns(), "ask_pid": 4242, "ask_created": 1790812000.5,  # pyright: ignore[reportPrivateUsage]
            "cwd": "C:/very/secret/path", "batch": None, "task": None}
    base.update(kw)
    return base


_SEQ = [0]


def claim(db, stream, peer="cx", *, generation=1, expires=NOW + 60.0, lease=120.0, workspace_generation=None):
    with closing(sqlite3.connect(db)) as c:
        c.execute("INSERT OR REPLACE INTO bridge_claims (stream_id,peer_id,owner_id,generation,expires_at,lease_sec,workspace_generation) "
                  "VALUES (?,?,?,?,?,?,?)", (stream, peer, "owner", generation, expires, lease,
                                             workspace_generation if workspace_generation is not None else wsgen(db)))
        c.commit()


def delivery(db, stream, peer="cx", *, certainty="STARTED", claim_generation=1, result=None, result_json=None, terminal_at=None,
             prompt: str | None = "Review the plan", body_json=None, binding=BINDING, metadata_json=None, record=True,
             kinds=None, started_at=NOW - 30.0, caller_detail=None, seq=None) -> str:
    """One delivery with its record and evidence. `kinds` are extra evidence kinds appended after the start kinds."""
    _SEQ[0] += 1
    n = _SEQ[0] if seq is None else seq
    did, rid = f"{stream}:{peer}:d{n}", f"{stream}:r{n}"
    if result is not None:
        result_json = json.dumps(result)
    with closing(sqlite3.connect(db)) as c:
        if record:
            c.execute("INSERT INTO records (record_id,stream_id,position,author_peer_id,kind,body_json,metadata_json,idempotency_key,"
                      "payload_digest,created_at,appended_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (rid, stream, n, "user", "prompt", body_json if body_json is not None else json.dumps(prompt),
                       metadata_json if metadata_json is not None else json.dumps({"binding": binding}),
                       f"ask:req-{n}", "digest", "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"))
        c.execute("INSERT INTO bridge_deliveries (delivery_id,seq,stream_id,peer_id,record_id,record_position,attempt,certainty,"
                  "claim_generation,result_json,result_digest,terminal_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (did, n, stream, peer, rid, n, 1, "NOT_STARTED", claim_generation, result_json,
                   "dig" if result_json is not None else None, terminal_at))
        steps = {"NOT_STARTED": [], "MAY_HAVE_STARTED": ["MAY_HAVE_STARTED"], "STARTED": ["STARTED"],
                 "TERMINAL": ["STARTED", "TERMINAL"]}[certainty]
        for step in steps:
            c.execute("UPDATE bridge_deliveries SET certainty=? WHERE delivery_id=?", (step, did))
        rows: list[tuple[str, str | None, str]] = [("attempt_created", "NOT_STARTED", "{}")]
        if caller_detail is not None:
            rows.append(("caller", "NOT_STARTED", caller_detail if isinstance(caller_detail, str) else json.dumps(caller_detail)))
        if certainty in ("STARTED", "TERMINAL"):
            rows += [("about_to_invoke", "NOT_STARTED", "{}"), ("started", "STARTED", "{}"),
                     ("session_call", "STARTED", json.dumps({"started_at": started_at}))]
        if certainty == "TERMINAL":
            rows += [("terminal", "TERMINAL", "{}"), ("response_appended", "TERMINAL", "{}"), ("offset_acked", "TERMINAL", "{}")]
        for kind in kinds or []:
            rows.append((kind, None, "{}"))
        for kind, cert, detail in rows:
            c.execute("INSERT INTO bridge_evidence (delivery_id,stream_id,peer_id,kind,certainty,detail) VALUES (?,?,?,?,?,?)",
                      (did, stream, peer, kind, cert, detail))
        c.commit()
    return did


def frame(db, groups=("activity",), **kw):
    kw.setdefault("read_at", NOW)
    kw.setdefault("liveness", lambda *a, **k: True)
    return mrm.render_monitor_frame(db, list(groups), **kw)


def data(db, **kw) -> dict[str, Any]:
    out = frame(db, **kw)
    assert out["groups"]["activity"]["status"] == "OK", out
    return out["groups"]["activity"]["data"]


def asks(db, **kw) -> dict[str, dict[str, Any]]:
    return {a["name"]: a for a in data(db, **kw)["asks"] if not a["superseded"]}


def ok_result(text="First line\nVERDICT: OK", **kw):
    return {"response": text, "error": None, **kw}


# ---- states ---------------------------------------------------------------------------------------

def test_run_row_fields(db):
    claim(db, "s-run")
    delivery(db, "s-run", caller_detail=caller())
    row = asks(db)["s-run"]
    assert row["state"] == "run" and row["superseded"] is False
    assert row["alive"] is True and row["alive_reason"] is None
    assert row["peer"] == "cx" and row["profile"] == "cx.standard" and row["model"] == "test-model" and row["effort"] == "low"
    assert row["stream"] == "s-run" and row["name"] == "s-run"
    assert row["started_at"] == NOW - 30.0 and row["elapsed_s"] == 30.0 and row["lease_left_s"] == 60.0
    assert row["ended_at"] is None and row["note"] is None
    assert row["ask_line"] == "Review the plan" and row["reply_line"] is None
    assert row["lead"] == {"label": "peerhub", "tool": "cc", "id": hashlib.sha256(b"sess-raw-1").hexdigest()[:8]}
    assert set(row) >= {"state", "superseded", "alive", "alive_reason", "peer", "profile", "model", "effort", "stream", "name",
                        "lead", "started_at", "elapsed_s", "ended_at", "ended_exact", "age_s", "lease_left_s", "tokens",
                        "ask_line", "reply_line", "ask_line_long", "reply_line_long", "note"}


def test_not_started_delivery_under_a_live_lease_is_run(db):
    claim(db, "s-ns")
    delivery(db, "s-ns", certainty="NOT_STARTED")
    row = asks(db)["s-ns"]
    assert row["state"] == "run" and row["started_at"] is None and row["elapsed_s"] is None


def test_ok_row(db):
    claim(db, "s-ok", expires=NOW - 100.0)
    delivery(db, "s-ok", certainty="TERMINAL", result=ok_result(usage={"input_tokens": 10, "output_tokens": 3}),
             terminal_at=iso(NOW - 100.0))
    row = asks(db)["s-ok"]
    assert row["state"] == "ok" and row["note"] is None
    assert row["ended_at"] == NOW - 100.0 and row["ended_exact"] is True and row["age_s"] == 100.0
    assert row["reply_line"] == "VERDICT: OK" and row["tokens"] == {"input": 10, "output": 3, "cache_read": None, "cache_write": None}
    assert row["alive"] is None and row["lease_left_s"] is None


def test_ok_with_cc_permission_denials_stays_ok(db):
    claim(db, "s-okd", expires=NOW - 100.0)
    delivery(db, "s-okd", certainty="TERMINAL", result=ok_result(denied_actions=["Bash: rm -rf"]), terminal_at=iso(NOW - 100.0))
    assert asks(db)["s-okd"]["state"] == "ok"


def test_empty_row_names_denied_actions(db):
    claim(db, "s-empty", expires=NOW - 100.0)
    delivery(db, "s-empty", certainty="TERMINAL", terminal_at=iso(NOW - 100.0),
             result={"response": "  \n", "error": None, "empty_response": True, "denied_actions": ["command", "write_file"]})
    row = asks(db)["s-empty"]
    assert row["state"] == "empty" and row["note"] == "denied: command, write_file"
    assert row["reply_line"] is None


def test_empty_without_flag_is_still_empty(db):
    claim(db, "s-empty2", expires=NOW - 100.0)
    delivery(db, "s-empty2", certainty="TERMINAL", terminal_at=iso(NOW - 100.0), result={"response": "", "error": None})
    row = asks(db)["s-empty2"]
    assert row["state"] == "empty" and row["note"] is None


def test_error_from_result_code(db):
    claim(db, "s-err", expires=NOW - 100.0)
    delivery(db, "s-err", certainty="TERMINAL", terminal_at=iso(NOW - 100.0),
             result={"response": "partial", "error": {"code": "denied_empty", "message": "x"}})
    row = asks(db)["s-err"]
    assert row["state"] == "error" and row["note"] == "denied_empty"


def test_refusal_and_request_rejected_are_errors(db):
    claim(db, "s-ref", expires=NOW - 100.0)
    delivery(db, "s-ref", certainty="STARTED", kinds=["refusal_detected", "offset_acked"],
             result={"response": None, "error": {"code": "quota_exhausted", "message": "m"}, "task_outcome": "NOT_EXECUTED"})
    claim(db, "s-rej", expires=NOW - 100.0)
    delivery(db, "s-rej", certainty="NOT_STARTED", kinds=["request_rejected", "offset_acked"],
             result={"response": None, "error": {"code": "legacy_selector_unsupported", "message": "m"}})
    rows = asks(db)
    assert (rows["s-ref"]["state"], rows["s-ref"]["note"]) == ("error", "quota_exhausted")
    assert (rows["s-rej"]["state"], rows["s-rej"]["note"]) == ("error", "legacy_selector_unsupported")


@pytest.mark.parametrize("kind", ["prespawn_failure", "control_halt", "session_unavailable"])
def test_known_not_run_endings_in_evidence_are_errors(db, kind):
    claim(db, "s-pre", expires=NOW - 100.0)
    delivery(db, "s-pre", certainty="NOT_STARTED", kinds=["about_to_invoke", kind])
    row = asks(db)["s-pre"]
    assert (row["state"], row["note"]) == ("error", kind)


@pytest.mark.parametrize("kind", ["runtime_error", "runtime_timeout", "runtime_disconnect", "no_terminal", "session_tainted",
                                  "fenced_mid_delivery"])
def test_uncertain_endings_are_unsure(db, kind):
    claim(db, "s-uns", expires=NOW - 100.0)
    delivery(db, "s-uns", certainty="STARTED", kinds=[kind])
    row = asks(db)["s-uns"]
    assert (row["state"], row["note"]) == ("unsure", kind)


def test_start_uncertain_and_may_have_started_are_always_unsure(db):
    claim(db, "s-mhs")  # live lease
    delivery(db, "s-mhs", certainty="MAY_HAVE_STARTED", kinds=["about_to_invoke", "start_uncertain"])
    row = asks(db)["s-mhs"]
    assert (row["state"], row["note"]) == ("unsure", "start_uncertain")
    assert row["alive"] is None


def test_ending_kind_while_the_lease_is_still_live_is_the_ending(db):
    claim(db, "s-live-end")
    delivery(db, "s-live-end", certainty="STARTED", kinds=["no_terminal"])
    assert asks(db)["s-live-end"]["state"] == "unsure"


def test_retry_after_prespawn_failure_is_run_again(db):
    claim(db, "s-retry")
    delivery(db, "s-retry", certainty="STARTED", kinds=["about_to_invoke", "started"])  # newest non-bookkeeping kind is a start
    with closing(sqlite3.connect(db)) as c:  # the older prespawn_failure is not the last kind
        did = c.execute("SELECT delivery_id FROM bridge_deliveries WHERE stream_id='s-retry'").fetchone()[0]
        c.execute("INSERT INTO bridge_evidence (delivery_id,stream_id,peer_id,kind,certainty,detail) VALUES (?,?,?,?,?,?)",
                  (did, "s-retry", "cx", "offset_acked", None, "{}"))
        c.commit()
    assert asks(db)["s-retry"]["state"] == "run"


def test_overdue_row(db):
    claim(db, "s-over", expires=NOW - 60.0)
    delivery(db, "s-over", certainty="STARTED", caller_detail=caller())
    row = asks(db)["s-over"]
    assert row["state"] == "overdue" and row["lease_left_s"] == -60.0 and row["elapsed_s"] == 30.0
    assert row["ended_at"] is None and row["alive"] is True


@pytest.mark.parametrize("past,running", [(monitor_queries.ALERT_WINDOW_S, True), (monitor_queries.ALERT_WINDOW_S + 1, False)])
def test_overdue_is_in_the_running_set_only_for_the_window(db, past, running):
    claim(db, "s-late", expires=NOW - past)
    delivery(db, "s-late", certainty="STARTED", caller_detail=caller(), started_at=NOW - past - 30.0)
    calls = []
    out = frame(db, liveness=lambda *a, **k: calls.append(a) or True)
    d = out["groups"]["activity"]["data"]
    row = d["asks"][0]
    assert row["state"] == "overdue"
    assert d["counts"]["running"] == d["counts"]["overdue"] == (1 if running else 0)
    assert [(a["code"], a["kind"]) for a in out["alerts"]] == ([("a2", "overdue")] if running else [])
    if running:
        assert row["ended_at"] is None and row["lease_left_s"] == -past and row["alive"] is True
    else:
        assert (row["ended_at"], row["ended_exact"], row["age_s"]) == (NOW - past, True, float(past))
        assert row["alive"] is None and row["lease_left_s"] is None and calls == []


def test_other_workspace_generation_is_unsure_also_after_its_lease_expired(db):
    claim(db, "s-restored-old", expires=NOW - 60.0, workspace_generation="another-lineage")
    delivery(db, "s-restored-old", certainty="STARTED", caller_detail=caller())
    out = frame(db, liveness=lambda *a, **k: False)
    row = out["groups"]["activity"]["data"]["asks"][0]
    assert (row["state"], row["note"]) == ("unsure", "other generation")
    assert out["alerts"] == []  # A4: neither a2 nor a1


def test_other_generation_does_not_sort_with_recent_bad_outcomes(db):
    claim(db, "s-restored-new", expires=NOW - 5.0, workspace_generation="another-lineage")
    delivery(db, "s-restored-new", certainty="STARTED")
    claim(db, "s-bad", expires=NOW - 600.0)
    delivery(db, "s-bad", certainty="TERMINAL", terminal_at=iso(NOW - 600.0), result={"response": "", "error": None})
    assert [a["name"] for a in data(db)["asks"]] == ["s-bad", "s-restored-new"]


def test_deeply_nested_evidence_details_are_unreadable_not_errors(db):
    deep = "[" * 100_000 + "]" * 100_000
    claim(db, "s-deep")
    did = delivery(db, "s-deep", certainty="STARTED", caller_detail=deep, started_at=NOW - 30.0)
    with closing(sqlite3.connect(db)) as c:  # a newer start detail, nested past Python's recursion limit
        c.execute("DROP INDEX IF EXISTS idx_be_session_call_once")
        c.execute("INSERT INTO bridge_evidence (delivery_id,stream_id,peer_id,kind,certainty,detail) VALUES (?,?,?,?,?,?)",
                  (did, "s-deep", "cx", "session_call", "STARTED", '{"started_at": ' + deep + "}"))
        c.commit()
    out = frame(db)
    assert out["groups"]["activity"]["status"] == "OK", out["errors"]
    row = out["groups"]["activity"]["data"]["asks"][0]
    assert "caller unreadable" in row["reasons"] and row["started_at"] is None and row["alive_reason"] == "no caller row"


def test_a_reply_that_starts_with_nul_is_not_empty(db):
    claim(db, "s-nulreply", expires=NOW - 10.0)
    delivery(db, "s-nulreply", certainty="TERMINAL", terminal_at=iso(NOW - 10.0), result=ok_result("\x00 real answer"))
    out = frame(db)
    assert out["groups"]["activity"]["data"]["asks"][0]["state"] == "ok"
    assert out["alerts"] == []


def test_expired_lease_before_any_invocation_is_error_not_started(db):
    claim(db, "s-nst", expires=NOW - 60.0)
    delivery(db, "s-nst", certainty="NOT_STARTED")
    row = asks(db)["s-nst"]
    assert (row["state"], row["note"]) == ("error", "not started")


def test_unreadable_result_json_is_unsure_with_a_reason_and_never_aborts(db):
    claim(db, "s-bad", expires=NOW - 100.0)
    delivery(db, "s-bad", certainty="TERMINAL", result_json="{not json", terminal_at=iso(NOW - 100.0))
    claim(db, "s-good", expires=NOW - 100.0)
    delivery(db, "s-good", certainty="TERMINAL", result=ok_result(), terminal_at=iso(NOW - 100.0))
    rows = asks(db)
    assert rows["s-bad"]["state"] == "unsure" and "result unreadable" in rows["s-bad"]["reasons"]
    assert rows["s-good"]["state"] == "ok"


# ---- scope selection ------------------------------------------------------------------------------

def test_twenty_four_hour_bound(db):
    claim(db, "s-in", expires=NOW - 86_399.0)
    delivery(db, "s-in", certainty="TERMINAL", result=ok_result(), terminal_at=iso(NOW - 86_399.0))
    claim(db, "s-out", expires=NOW - 86_401.0)
    delivery(db, "s-out", certainty="TERMINAL", result=ok_result(), terminal_at=iso(NOW - 86_401.0))
    assert set(asks(db)) == {"s-in"}


def test_a_later_ask_on_the_same_stream_and_peer_replaces_the_row(db):
    claim(db, "s-same", generation=2, expires=NOW - 10.0)
    delivery(db, "s-same", certainty="TERMINAL", claim_generation=1, terminal_at=iso(NOW - 200.0),
             result={"response": "", "error": None, "empty_response": True})
    delivery(db, "s-same", certainty="TERMINAL", claim_generation=2, terminal_at=iso(NOW - 10.0), result=ok_result())
    out = frame(db)
    rows = out["groups"]["activity"]["data"]["asks"]
    assert [r["state"] for r in rows] == ["ok"]
    assert [a for a in out["alerts"] if a["code"] == "a1"] == []


def test_a_generation_without_a_delivery_keeps_the_last_ask_visible(db):
    claim(db, "s-gen", generation=5, expires=NOW - 10.0)  # later acquires that created no delivery
    delivery(db, "s-gen", certainty="TERMINAL", claim_generation=3, terminal_at=iso(NOW - 500.0), result=ok_result())
    row = asks(db)["s-gen"]
    assert row["state"] == "ok" and row["ended_at"] == NOW - 500.0


def test_run_requires_the_claim_generation_of_the_delivery(db):
    claim(db, "s-taken", generation=4)  # live lease of a later claim that has no delivery yet
    delivery(db, "s-taken", certainty="STARTED", claim_generation=3)
    row = asks(db)["s-taken"]
    assert row["state"] == "unsure" and row["note"] == "claim taken"


def test_superseded_started_deliveries_are_returned_but_never_alert(db):
    claim(db, "s-sup", generation=2, expires=NOW - 10.0)
    delivery(db, "s-sup", certainty="STARTED", claim_generation=1)
    delivery(db, "s-sup", certainty="TERMINAL", claim_generation=2, terminal_at=iso(NOW - 10.0), result=ok_result())
    out = frame(db)
    rows = out["groups"]["activity"]["data"]["asks"]
    assert [(r["state"], r["superseded"], r["note"]) for r in rows] == [("ok", False, None), ("unsure", True, "superseded")]
    assert out["alerts"] == []
    assert out["groups"]["activity"]["data"]["counts"]["running"] == 0


def test_superseded_rows_are_capped_at_fifty(db):
    claim(db, "s-many", generation=2, expires=NOW - 10.0)
    for _ in range(55):
        delivery(db, "s-many", certainty="STARTED", claim_generation=1)
    delivery(db, "s-many", certainty="TERMINAL", claim_generation=2, terminal_at=iso(NOW - 10.0), result=ok_result())
    out = frame(db)
    d = out["groups"]["activity"]["data"]
    assert len([r for r in d["asks"] if r["superseded"]]) == 50
    assert d["truncated"] is True and d["superseded_omitted"] == 5
    assert out["alert_sources"]["activity"] == "read"  # superseded rows never alert, so their cap does not weaken line 1


def test_scope_cap_of_one_thousand(db):
    generation = wsgen(db)
    with closing(sqlite3.connect(db)) as c:
        c.executemany("INSERT INTO bridge_claims (stream_id,peer_id,owner_id,generation,expires_at,lease_sec,workspace_generation) "
                      "VALUES (?,?,?,?,?,?,?)", [(f"c{i:04d}", "cx", "o", 1, NOW - 4000.0 - i, 60.0, generation) for i in range(1001)])
        c.executemany("INSERT INTO bridge_deliveries (delivery_id,seq,stream_id,peer_id,record_id,record_position,attempt,certainty,"
                      "claim_generation,result_json,result_digest,terminal_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      [(f"c{i:04d}-d", 10_000 + i, f"c{i:04d}", "cx", f"c{i:04d}-r", 1, 1, "NOT_STARTED", 1,
                        json.dumps(ok_result()), "dig", iso(NOW - 4000.0 - i)) for i in range(1001)])
        c.commit()
    out = frame(db)
    d = out["groups"]["activity"]["data"]
    assert len(d["asks"]) == 1000 and "c1000" not in {a["stream"] for a in d["asks"]}
    assert d["truncated"] is True and d["scopes_total"] == 1001
    assert out["alert_sources"]["activity"] == "truncated"
    assert out["groups"]["activity"]["coverage"]["truncated"] is True
    # Exactly 1,000 scopes is not truncated.
    with closing(sqlite3.connect(db)) as c:
        c.execute("UPDATE bridge_claims SET expires_at=? WHERE stream_id='c1000'", (NOW - 90_000.0,))
        c.commit()
    d = data(db)
    assert len(d["asks"]) == 1000 and d["truncated"] is False


def test_restored_workspace_generation_is_unsure_and_never_alerts(db):
    claim(db, "s-restored", workspace_generation="another-lineage")
    delivery(db, "s-restored", certainty="STARTED", caller_detail=caller())
    out = frame(db, liveness=lambda *a, **k: False)
    row = out["groups"]["activity"]["data"]["asks"][0]
    assert (row["state"], row["note"]) == ("unsure", "other generation")
    assert out["alerts"] == []


def test_unreadable_generation_file_gives_a_reason_not_a_run(db, monkeypatch):
    claim(db, "s-gfile")
    delivery(db, "s-gfile", certainty="STARTED")
    real = Path.read_text

    def read_text(self, *a, **kw):
        if self.name == "workspace.generation":
            raise PermissionError("denied")
        return real(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", read_text)
    out = frame(db)
    d = out["groups"]["activity"]["data"]
    assert out["groups"]["activity"]["status"] == "OK"
    assert d["reason"] == "workspace.generation unreadable"
    assert d["asks"][0]["state"] == "unsure" and d["asks"][0]["note"] == "generation unknown"


def test_without_bridge_tables_activity_is_empty_not_an_error(tmp_path):
    h = CoreHarness(tmp_path / "plain")
    out = frame(h.db_path)
    assert out["groups"]["activity"]["status"] == "OK"
    assert out["groups"]["activity"]["data"]["asks"] == []
    assert out["alert_sources"]["activity"] == "read"


# ---- end time ---------------------------------------------------------------------------------------

def test_end_time_falls_back_to_the_claim_expiry(db):
    claim(db, "s-exact", generation=1, expires=NOW - 300.0)
    delivery(db, "s-exact", certainty="STARTED", claim_generation=1, kinds=["runtime_error"])
    claim(db, "s-bound", generation=3, expires=NOW - 120.0)
    delivery(db, "s-bound", certainty="STARTED", claim_generation=2, kinds=["runtime_error"])
    out = frame(db)
    rows = {a["name"]: a for a in out["groups"]["activity"]["data"]["asks"]}
    assert (rows["s-exact"]["ended_at"], rows["s-exact"]["ended_exact"], rows["s-exact"]["age_s"]) == (NOW - 300.0, True, 300.0)
    assert (rows["s-bound"]["ended_at"], rows["s-bound"]["ended_exact"], rows["s-bound"]["age_s"]) == (NOW - 120.0, False, None)
    assert {a["subject"].split()[-1] for a in out["alerts"] if a["code"] == "a1"} == {"s-exact", "s-bound"}  # the bound still alerts


# ---- order, counts, leads --------------------------------------------------------------------------

def test_order_running_first_flagged_first_then_bad_recent_then_newest(db):
    claim(db, "r-short")
    delivery(db, "r-short", started_at=NOW - 10.0, caller_detail=caller())
    claim(db, "r-long")
    delivery(db, "r-long", started_at=NOW - 500.0, caller_detail=caller())
    claim(db, "r-gone")
    delivery(db, "r-gone", started_at=NOW - 5.0, caller_detail=caller(ask_pid=999))
    claim(db, "e-ok-new", expires=NOW - 5.0)
    delivery(db, "e-ok-new", certainty="TERMINAL", terminal_at=iso(NOW - 5.0), result=ok_result())
    claim(db, "e-bad", expires=NOW - 600.0)
    delivery(db, "e-bad", certainty="TERMINAL", terminal_at=iso(NOW - 600.0), result={"response": "", "error": None})
    claim(db, "e-bad-old", expires=NOW - 4000.0)
    delivery(db, "e-bad-old", certainty="TERMINAL", terminal_at=iso(NOW - 4000.0), result={"response": "", "error": None})
    claim(db, "e-ok-old", expires=NOW - 3000.0)
    delivery(db, "e-ok-old", certainty="TERMINAL", terminal_at=iso(NOW - 3000.0), result=ok_result())

    def alive(host, ns, pid, created, **kw):
        return pid != 999
    names = [a["name"] for a in data(db, liveness=alive)["asks"]]
    assert names == ["r-gone", "r-long", "r-short", "e-bad", "e-ok-new", "e-ok-old", "e-bad-old"]


def test_counts_and_leads(db):
    for n, peer in ((1, "cx"), (2, "cx"), (3, "cc")):
        claim(db, f"run{n}", peer)
        delivery(db, f"run{n}", peer, caller_detail=caller(session=f"lead-{n % 2}"))
    claim(db, "done", "cc", expires=NOW - 50.0)
    delivery(db, "done", "cc", certainty="TERMINAL", terminal_at=iso(NOW - 50.0), result=ok_result(),
             caller_detail=caller(session="lead-9", label="other", tool="?"))
    d = data(db)
    assert d["counts"] == {"running": 3, "overdue": 0, "by_peer": {"cc": 1, "cx": 2}, "leads": 2}
    leads = {lead["id"]: lead for lead in d["leads"]}
    lead1 = leads[hashlib.sha256(b"lead-1").hexdigest()[:8]]
    assert lead1 == {"label": "peerhub", "tool": "cc", "id": lead1["id"], "running": 2, "asks_24h": 2, "last_ask_at": NOW - 30.0,
                     "session": None}
    other = leads[hashlib.sha256(b"lead-9").hexdigest()[:8]]
    assert other["running"] == 0 and other["asks_24h"] == 1 and other["tool"] == "?"
    assert d["sample_size"] == 4 and d["truncated"] is False


def test_asks_24h_counts_streams_not_superseded_deliveries(db):
    claim(db, "s-lead", generation=2, expires=NOW - 10.0)
    for _ in range(3):
        delivery(db, "s-lead", certainty="STARTED", claim_generation=1, caller_detail=caller(session="lead-x"))
    delivery(db, "s-lead", certainty="TERMINAL", claim_generation=2, terminal_at=iso(NOW - 10.0), result=ok_result(),
             caller_detail=caller(session="lead-x"))
    (lead,) = data(db)["leads"]
    assert lead["asks_24h"] == 1


def test_the_newest_caller_row_decides_lead_and_liveness(db):
    claim(db, "s-two-callers")
    did = delivery(db, "s-two-callers", certainty="STARTED", caller_detail=caller(session="first", ask_pid=111))
    latest = caller(session="second", ask_pid=222)
    with closing(sqlite3.connect(db)) as c:  # The new invocation and its caller snapshot are recorded together.
        c.executemany("INSERT INTO bridge_evidence (delivery_id,stream_id,peer_id,kind,certainty,detail) VALUES (?,?,?,?,?,?)",
                      [(did, "s-two-callers", "cx", "caller", "NOT_STARTED", json.dumps(latest)),
                       (did, "s-two-callers", "cx", "about_to_invoke", "NOT_STARTED",
                        json.dumps(dict(invocation_no=2, claim_generation=1, caller=latest))),
                       (did, "s-two-callers", "cx", "started", "STARTED", json.dumps(dict(invocation_no=2)))])
        c.commit()
    pids = []
    row = asks(db, liveness=lambda host, ns, pid, created, **k: pids.append(pid) or True)["s-two-callers"]
    assert row["lead"]["id"] == hashlib.sha256(b"second").hexdigest()[:8] and pids == [222]


def test_lead_id_without_a_session_uses_host_pid_and_creation_time(db):
    claim(db, "s-nosess")
    detail = caller(session=None)
    delivery(db, "s-nosess", caller_detail=detail)
    lead = asks(db)["s-nosess"]["lead"]
    expected = hashlib.sha256(f"{detail['host']}:{detail['ask_pid']}:{detail['ask_created']}".encode()).hexdigest()[:8]
    assert lead["id"] == expected


def test_batch_name_and_no_raw_identity_leaves_the_read_model(db):
    claim(db, "s-batch")
    delivery(db, "s-batch", caller_detail=caller(batch="b1234", task="review"))
    out = frame(db)
    row = out["groups"]["activity"]["data"]["asks"][0]
    assert row["name"] == "b1234/review" and row["stream"] == "s-batch"
    dumped = json.dumps(out)
    for raw in ("sess-raw-1", "4242", "very/secret/path", monitor_activity._this_host()):  # pyright: ignore[reportPrivateUsage]
        assert raw not in dumped


# ---- caller rows and liveness -----------------------------------------------------------------------

def test_rows_without_a_caller_row(db):
    claim(db, "s-old")
    delivery(db, "s-old")
    row = asks(db)["s-old"]
    assert row["alive"] is None and row["alive_reason"] == "no caller row" and row["lead"] is None


def test_malformed_caller_detail_is_a_reason_not_an_error(db):
    claim(db, "s-badcaller")
    delivery(db, "s-badcaller", caller_detail="{oops")
    claim(db, "s-badcaller2")
    delivery(db, "s-badcaller2", caller_detail=caller(ask_pid="12"))
    out = frame(db)
    assert out["groups"]["activity"]["status"] == "OK"
    for row in out["groups"]["activity"]["data"]["asks"]:
        assert row["alive"] is None and row["alive_reason"] == "no caller row" and "caller unreadable" in row["reasons"]


@pytest.mark.parametrize("result,expected", [(True, True), (False, False), (None, None)])
def test_liveness_through_the_injected_callable(db, result, expected):
    claim(db, "s-live")
    delivery(db, "s-live", caller_detail=caller())
    seen = []

    def live(host, ns, pid, created, **kw):
        seen.append((host, ns, pid, created))
        return result
    row = asks(db, liveness=live, process_probe=lambda pid: "denied")["s-live"]
    assert row["alive"] is expected
    assert seen == [(caller()["host"], caller()["ns"], 4242, 1790812000.5)]
    assert row["alive_reason"] == (None if result is not None else "not accessible")


@pytest.mark.parametrize("detail,probe,reason", [
    ({"host": "elsewhere|linux"}, None, "other host"),
    ({"ns": "pid:[another]"}, None, "other namespace"),  # never a real link: the runner itself may be pid:[4026531836]
    ({"ns": None}, None, "other namespace"),
    ({}, 1790812000.4999, "pid reused or clock stepped"),
    ({}, "denied", "not accessible"),
])
def test_unknown_liveness_reasons(db, detail, probe, reason):
    claim(db, "s-unk")
    delivery(db, "s-unk", caller_detail=caller(**detail))
    row = asks(db, liveness=lambda *a, **k: None, process_probe=lambda pid: probe)["s-unk"]
    assert row["alive"] is None and row["alive_reason"] == reason


def test_huge_integers_in_evidence_are_unknown_values_not_errors(db):
    claim(db, "s-huge")
    delivery(db, "s-huge", certainty="STARTED", caller_detail=caller(ask_created=2**1024), started_at=2**1024)
    seen = []
    out = frame(db, liveness=lambda host, ns, pid, created, **k: seen.append(created))
    assert out["groups"]["activity"]["status"] == "OK"
    row = out["groups"]["activity"]["data"]["asks"][0]
    assert row["started_at"] is None and "start time unreadable" in row["reasons"]
    assert row["lead"] is not None and seen == [None] and row["alive_reason"] == "not accessible"


@pytest.mark.parametrize("detail", [{"session": "a\ud800b"}, {"session": None, "host": "h\ud800"}, {"label": "l\ud800"}])
def test_lone_surrogates_in_the_caller_row_never_raise(db, detail):
    claim(db, "s-sur")
    delivery(db, "s-sur", certainty="STARTED", caller_detail=caller(**detail))
    out = frame(db)
    assert out["groups"]["activity"]["status"] == "OK"
    row = out["groups"]["activity"]["data"]["asks"][0]
    assert len(row["lead"]["id"]) == 8
    json.dumps(out).encode("utf-8")


def test_a_failing_liveness_callable_is_unknown_not_an_error(db):
    claim(db, "s-raise")
    delivery(db, "s-raise", caller_detail=caller())

    def boom(*a, **k):
        raise OSError("psutil exploded")
    out = frame(db, liveness=boom, process_probe=lambda pid: "denied")
    assert out["groups"]["activity"]["status"] == "OK"
    assert out["groups"]["activity"]["data"]["asks"][0]["alive"] is None


def test_liveness_is_only_asked_for_run_and_overdue_rows(db):
    claim(db, "s-ended", expires=NOW - 10.0)
    delivery(db, "s-ended", certainty="TERMINAL", terminal_at=iso(NOW - 10.0), result=ok_result(), caller_detail=caller())
    calls = []
    asks(db, liveness=lambda *a, **k: calls.append(a) or True)
    assert calls == []


# ---- text rule -------------------------------------------------------------------------------------

def text_row(db, prompt=None, reply=None, **kw):
    claim(db, "s-text", expires=NOW - 10.0)
    delivery(db, "s-text", certainty="TERMINAL", terminal_at=iso(NOW - 10.0), prompt=prompt,
             result=ok_result(reply if reply is not None else "done"), **kw)
    return asks(db)["s-text"]


def test_text_escapes_are_dropped_and_cjk_is_kept(db):
    row = text_row(db, prompt="\x1b[31m검토\x1b[0m 요청: 계획\nsecond", reply="ok\n\x1b]0;title\x07최종 결론")
    assert row["ask_line"] == "[31m검토[0m 요청: 계획"
    assert row["reply_line"] == "]0;title최종 결론"


def test_first_line_that_is_only_a_dash_is_skipped(db):
    row = text_row(db, prompt="-\n---\n  \nReal request here\nmore", reply="answer\n-\n")
    assert row["ask_line"] == "Real request here" and row["reply_line"] == "answer"


def test_short_and_long_caps(db):
    row = text_row(db, prompt="x" * 500)
    assert len(row["ask_line"]) == 60 and row["ask_line"].endswith("…")
    assert len(row["ask_line_long"]) == 200 and row["ask_line_long"].endswith("…")


def test_text_over_the_limit_is_long_text_and_not_fetched(db):
    statements: list[str] = []
    row_prompt = "a" * (monitor_queries.TEXT_MAX_CHARS + 1)
    claim(db, "s-long", expires=NOW - 10.0)
    delivery(db, "s-long", certainty="TERMINAL", terminal_at=iso(NOW - 10.0), prompt=row_prompt,
             result=ok_result("b" * monitor_queries.TEXT_MAX_CHARS))
    out = frame(db, trace_callback=statements.append)
    row = out["groups"]["activity"]["data"]["asks"][0]
    assert row["ask_line"] == row["ask_line_long"] == "(long text)"
    assert row["reply_line"] == "b" * 59 + "…"  # exactly at the limit is read
    selects = [s for s in statements if s.lstrip().upper().startswith(("SELECT", "WITH"))]
    assert len(selects) == 1 and "FROM bridge_claims c" in selects[0]  # the one activity statement, nothing fetched later
    with closing(sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)) as c:
        c.row_factory = sqlite3.Row
        fetched = c.execute(monitor_queries._ACTIVITY_SQL, monitor_queries._activity_params(NOW, wsgen(db), True)).fetchone()  # pyright: ignore[reportPrivateUsage]
    assert fetched["ask_text"] is None and fetched["ask_len"] == monitor_queries.TEXT_MAX_CHARS + 1  # the long text never left SQLite
    assert len(fetched["reply_text"]) == monitor_queries.TEXT_MAX_CHARS


def test_a_nul_hides_the_length_from_sqlite_and_python_rechecks_it(db):
    row = text_row(db, prompt="a\x00" + "b" * monitor_queries.TEXT_MAX_CHARS, reply="ok\x00" + "c" * monitor_queries.TEXT_MAX_CHARS)
    assert row["ask_line"] == row["ask_line_long"] == "(long text)"
    assert row["reply_line"] == row["reply_line_long"] == "(long text)"


@pytest.mark.parametrize("where", ["body", "reply"])
def test_a_lone_surrogate_escape_in_stored_json_keeps_the_group_ok(db, where):
    claim(db, "s-lone", expires=NOW - 10.0)
    if where == "body":
        delivery(db, "s-lone", certainty="TERMINAL", terminal_at=iso(NOW - 10.0), body_json='"a\\ud800b"', result=ok_result())
    else:
        delivery(db, "s-lone", certainty="TERMINAL", terminal_at=iso(NOW - 10.0), result_json='{"response":"x\\ud800y","error":null}')
    out = frame(db)
    assert out["groups"]["activity"]["status"] == "OK", out["errors"]
    row = out["groups"]["activity"]["data"]["asks"][0]
    assert (row["ask_line"] if where == "body" else row["reply_line"]) is not None
    json.dumps(out).encode("utf-8")


def test_secret_on_the_shown_line_is_redacted(db):
    row = text_row(db, prompt="use key sk-abcdefghijklmnop now", reply="token=supersecret42value done")
    assert "abcdefghijklmnop" not in json.dumps(row) and "[REDACTED]" in row["ask_line"]
    assert "supersecret42value" not in json.dumps(row)


@pytest.mark.parametrize("broken", ["sk-abcd\u200befghijklmnop", "sk-abcd\x00efghijklmnop", "sk-abcd\u2060efgh\u00adijklmnop",
                                    "sk-abcd\x1b\x07efghijklmnop"])
def test_secret_broken_by_zero_width_or_control_characters_is_redacted(db, broken):
    row = text_row(db, prompt=f"key {broken} here")
    assert "efghijklmnop" not in json.dumps(row)
    assert row["ask_line"] == "key [REDACTED] here"


def test_environment_secret_is_redacted(db, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "hunter2-env-value")
    row = text_row(db, prompt="pass hunter2-env-value along")
    assert "hunter2" not in json.dumps(row)


def test_malformed_body_json_gives_an_unknown_cell_with_a_reason(db):
    row = text_row(db, body_json="{not json")
    assert row["ask_line"] is None and "request text unreadable" in row["reasons"]
    assert row["state"] == "ok"


def test_non_string_body_is_unknown(db):
    row = text_row(db, body_json=json.dumps({"x": 1}))
    assert row["ask_line"] is None and "request text unreadable" in row["reasons"]


def test_text_off(db, monkeypatch):
    monkeypatch.setenv("PEERHUB_MONITOR_TEXT", "off")
    row = text_row(db, prompt="hello", reply="world")
    assert [row[k] for k in ("ask_line", "reply_line", "ask_line_long", "reply_line_long")] == [None] * 4


def test_text_is_read_only_for_open_recent_or_twenty_newest_rows(db):
    for i in range(25):  # all ended outside the window
        claim(db, f"old{i:02d}", expires=NOW - 4000.0 - i)
        delivery(db, f"old{i:02d}", certainty="TERMINAL", terminal_at=iso(NOW - 4000.0 - i), result=ok_result())
    rows = asks(db)
    with_text = sorted(name for name, r in rows.items() if r["ask_line"] is not None)
    assert with_text == [f"old{i:02d}" for i in range(20)]
    assert all(rows[f"old{i:02d}"]["reply_line"] is None for i in range(20, 25))


def test_superseded_rows_do_not_take_the_text_ranks_of_ended_rows(db):
    claim(db, "s-livesup", generation=30)  # a live scope with a long tail of superseded deliveries
    for _ in range(25):
        delivery(db, "s-livesup", certainty="STARTED", claim_generation=1)
    delivery(db, "s-livesup", certainty="STARTED", claim_generation=30)
    for i in range(25):
        claim(db, f"old{i:02d}", expires=NOW - 4000.0 - i)
        delivery(db, f"old{i:02d}", certainty="TERMINAL", terminal_at=iso(NOW - 4000.0 - i), result=ok_result())
    rows = asks(db)
    assert rows["s-livesup"]["state"] == "run" and rows["s-livesup"]["ask_line"] == "Review the plan"
    with_text = sorted(name for name, r in rows.items() if name.startswith("old") and r["ask_line"] is not None)
    assert with_text == [f"old{i:02d}" for i in range(20)]


def test_format_character_table_covers_every_cf_and_cc_character():
    missing = [cp for cp in range(sys.maxunicode + 1)
               if unicodedata.category(chr(cp)) in ("Cf", "Cc") and chr(cp) not in "\n\r"
               and monitor_values._strip_controls(chr(cp)) != ("" if chr(cp) != "\t" else " ")]  # pyright: ignore[reportPrivateUsage]
    assert missing == []


# ---- read-only, statement count, scale --------------------------------------------------------------

def test_activity_is_read_only(db):
    claim(db, "s-ro")
    delivery(db, "s-ro", caller_detail=caller())
    before = hashlib.sha256(Path(db).read_bytes()).hexdigest()
    statements: list[str] = []
    frame(db, trace_callback=statements.append)
    assert hashlib.sha256(Path(db).read_bytes()).hexdigest() == before
    writes = [s for s in statements if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP"))]
    assert writes == []


def test_activity_is_one_select(db):
    claim(db, "s-one")
    delivery(db, "s-one")
    statements: list[str] = []
    out = frame(db, trace_callback=statements.append)
    assert out["query_families"] == 1


def _bridge_store(tmp_path):
    from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness
    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness)
    harness.delivery_cycle("b", "s", _ProviderTelemetryRuntime())
    return harness


@pytest.mark.parametrize("facts_in_capacity_rows", [True, False])
def test_select_count_for_the_default_groups_and_for_capacity_alone(tmp_path, facts_in_capacity_rows):
    """Default groups: peers, sessions, scope peers, scopes, capacity, activity = 6 when the capacity rows carry a facts
    report; 7 when they do not and the existing `peers` facts fallback (one more SELECT) runs. `capacity` alone: 1."""
    from peerhub.core.monitor_groups import DEFAULT_GROUPS
    from peerhub.extensions.observation import ObservationStore
    from tests.communication.fakes.observation import FakeObservationSource, measured
    harness = _bridge_store(tmp_path)
    store = ObservationStore(harness.db_path, clock=lambda: 1990.0)  # the quota tables exist, so capacity really runs
    if facts_in_capacity_rows:
        store.capture("b", "quota", FakeObservationSource(measured({"remaining_fraction": 0.5,
                                                                    "facts_report": {"status": "ok", "reasons": []}})))
    assert "activity" in DEFAULT_GROUPS
    statements: list[str] = []
    default = mrm.render_monitor_frame(harness.db_path, list(DEFAULT_GROUPS), read_at=2000.0, liveness=lambda *a, **k: None,
                                       trace_callback=statements.append)
    capacity = mrm.render_monitor_frame(harness.db_path, ["capacity"], read_at=2000.0)
    selects = [s.lstrip()[:40] for s in statements if s.lstrip().upper().startswith(("SELECT", "WITH"))]
    assert default["query_families"] == len(selects) == (6 if facts_in_capacity_rows else 7), selects
    assert (default["groups"]["peers"]["data"]["facts_report"] is not None) is facts_in_capacity_rows
    assert all(group["status"] == "OK" for group in default["groups"].values()), default["errors"]
    assert capacity["query_families"] == 1 and capacity["groups"]["capacity"]["status"] == "OK"


@pytest.fixture
def activity_scale_store(tmp_path):
    def store(name, history):
        path = CoreHarness(tmp_path / name).db_path
        with closing(sqlite3.connect(path)) as c:
            for stmt in bridge_schema._DDL:  # pyright: ignore[reportPrivateUsage]
                c.execute(stmt)
            c.execute(bridge_claims._DDL)  # pyright: ignore[reportPrivateUsage]
            for stmt in (*bridge_registry.DDL, *delivery_projections.DDL):
                c.execute(stmt)
            generation = wsgen(path)
            c.executemany("INSERT INTO bridge_claims (stream_id,peer_id,owner_id,generation,expires_at,lease_sec,workspace_generation) "
                          "VALUES (?,?,?,?,?,?,?)", [(f"other{i}", "cx", "o", 1, NOW - 100.0 - i, 60.0, generation) for i in range(150)]
                          + [("peer:cx:chat", "cx", "o", history, NOW - 10.0, 60.0, generation)])
            c.executemany("INSERT INTO bridge_deliveries (delivery_id,seq,stream_id,peer_id,record_id,record_position,attempt,certainty,"
                          "claim_generation,result_json,result_digest,terminal_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          [(f"o{i}", i + 1, f"other{i}", "cx", f"or{i}", 1, 1, "NOT_STARTED", 1, json.dumps(ok_result()), "d",
                            iso(NOW - 100.0 - i)) for i in range(150)]
                          + [(f"h{i}", 1000 + i, "peer:cx:chat", "cx", f"hr{i}", i, 1, "NOT_STARTED", i + 1, json.dumps(ok_result()),
                              "d", iso(NOW - 10.0)) for i in range(history)])
            c.commit()
        return path

    return store


def test_scale_one_scope_with_a_long_delivery_history(activity_scale_store):
    def steps(path):
        params = monitor_queries._activity_params(NOW, wsgen(path), True)  # pyright: ignore[reportPrivateUsage]
        sql = monitor_queries._ACTIVITY_SQL  # pyright: ignore[reportPrivateUsage]
        counter = [0]
        with closing(sqlite3.connect(f"{Path(path).as_uri()}?mode=ro", uri=True)) as conn:
            conn.set_progress_handler(lambda: counter.__setitem__(0, counter[0] + 1) or 0, 1)
            rows = conn.execute(sql, params).fetchall()
            conn.set_progress_handler(None, 1)
            plan = " | ".join(str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
        return counter[0], len(rows), plan

    small_db, big_db = activity_scale_store("small", 150), activity_scale_store("big", 9000)
    small, rows_small, _ = steps(small_db)
    big, rows_big, plan = steps(big_db)
    assert rows_small == rows_big == 151
    assert re.search(r"SEARCH d USING INDEX idx_bd_scope_seq \(stream_id=\? AND peer_id=\?\)", plan), plan
    assert re.search(r"SEARCH d USING INDEX idx_bd_scope_unresolved \(stream_id=\? AND peer_id=\?\)", plan), plan
    # Growing settled history by 60x leaves work bounded by selected scopes and unresolved deliveries.
    print(f"activity statement VM steps: history 150 -> {small}, history 9000 -> {big}")
    assert big < small * 1.1
    assert len(data(big_db)["asks"]) == 151


@pytest.mark.serial_timing
def test_activity_long_delivery_history_wall_clock(activity_scale_store):
    path = activity_scale_store("big", 9000)
    sql = monitor_queries._ACTIVITY_SQL  # pyright: ignore[reportPrivateUsage]
    params = monitor_queries._activity_params(NOW, wsgen(path), True)  # pyright: ignore[reportPrivateUsage]
    with closing(sqlite3.connect(f"{Path(path).as_uri()}?mode=ro", uri=True)) as conn:
        conn.execute(sql, params).fetchall()  # warm the same connection before measuring
        started = time.perf_counter()
        conn.execute(sql, params).fetchall()
        elapsed = time.perf_counter() - started
    assert elapsed < 1.0
