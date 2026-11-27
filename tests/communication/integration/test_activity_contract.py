"""Engram Rev06 consumer oracle using deterministic synthetic source rows."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from peerhub.extensions.activity.reader import export_page
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.bridge_helpers import bseed

FIXTURE = Path(__file__).resolve().parents[3] / "docs/implementation/activity/engram-v1.json"


def synthetic_exchange(root, patch):
    h = BridgeHarness(root)
    bseed(h, n=0)
    # Fixed IDs, times and enrollment eliminate host-dependent bytes in this transport vector.
    patch.setattr("peerhub.extensions.activity.cursor.identity", lambda _: ("fixture-store", "fixture-generation"))
    with closing(sqlite3.connect(h.db_path)) as c, c:
        c.execute("INSERT INTO records(record_id,stream_id,position,author_peer_id,kind,targets_json,metadata_json,"
                  "idempotency_key,payload_digest,created_at,appended_at) VALUES ('rec-fixture','s',1,'a','prompt',"
                  "'[\"b\"]','{\"binding\":\"demo\",\"runtime_kind\":\"oa\"}','ask:fixture','fixture','2026-11-28T00:00:00Z',"
                  "'2026-11-28T00:00:00Z')")
        c.execute("INSERT INTO bridge_deliveries(delivery_id,seq,stream_id,peer_id,record_id,record_position,attempt,"
                  "certainty,claim_generation,session_handle,session_generation) VALUES ('d1',1,'s','b','rec-fixture',"
                  "1,1,'NOT_STARTED',1,'sess_fixture',1)")
        c.execute("INSERT INTO bridge_evidence(stream_id,peer_id,delivery_id,kind,certainty,detail) VALUES "
                  "('s','b','d1','attempt_created','NOT_STARTED','{}')")
        c.execute("UPDATE bridge_deliveries SET certainty='STARTED' WHERE delivery_id='d1'")
        c.execute("UPDATE bridge_deliveries SET certainty='TERMINAL' WHERE delivery_id='d1'")
        usage = {"schema_version": 1, "scope": "delivery", "source": "oa.response.usage",
                 "reported": {"input_tokens": 8, "output_tokens": 0},
                 "normalized": {"provider_input_tokens": 8, "output_tokens": 0},
                 "coverage": {"state": "MEASURED", "reason": None}, "attribution": "delivery", "field_evidence": {}}
        detail = {"handle": "sess_fixture", "generation": 1, "schema_version": 3, "provider_kind": "oa",
                  "observed_at": 1795824000, "usage": usage}
        c.execute("INSERT INTO bridge_evidence(stream_id,peer_id,delivery_id,kind,certainty,detail) VALUES "
                  "('s','b','d1','session_telemetry','TERMINAL',?)", (json.dumps(detail),))
    first = export_page(h.db_path, stream="s", limit=1)
    with closing(sqlite3.connect(h.db_path)) as c, c:
        c.execute("INSERT INTO bridge_ask_outcomes(record_id,outcome,marked_at) VALUES "
                  "('rec-fixture','accepted','2026-11-28T00:01:00Z')")
    drain = export_page(h.db_path, stream="s", token=first["next_cursor"])
    late = export_page(h.db_path, stream="s", token=drain["next_cursor"])
    return {"contract": "peerhub.activity/1.0", "first": first, "drain": drain, "late": late}


def test_engram_golden_replay_and_conflict_quarantine(tmp_path, monkeypatch):
    exchange = synthetic_exchange(tmp_path / "ws", monkeypatch)
    assert exchange == json.loads(FIXTURE.read_text(encoding="utf-8"))
    # Independent consumer. The source installation ID is assigned on enrollment and survives restores.
    accepted = {}
    conflicts = []
    def ingest(page):
        for event in page["items"]:
            key = ("enrolled-peerhub", event["source_event_id"], event["source_revision"])
            raw = {k: v for k, v in event.items() if k != "source_digest"}
            digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            assert digest == event["source_digest"]
            if accepted.setdefault(key, digest) != digest:
                conflicts.append(key)
    for name in ("first", "drain", "drain", "late", "late"):
        ingest(exchange[name])
    assert len(accepted) == 4 and not conflicts
    conflict = dict(exchange["late"]["items"][0], outcome="rejected")
    raw = {k: v for k, v in conflict.items() if k != "source_digest"}
    conflict["source_digest"] = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    ingest({"items": [conflict]})
    assert conflicts == [("enrolled-peerhub", "outcome:1", "1")]
    assert accepted[conflicts[0]] == exchange["late"]["items"][0]["source_digest"]
