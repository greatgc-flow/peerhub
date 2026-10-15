from __future__ import annotations

import json
import sqlite3

import pytest

pytestmark = pytest.mark.unit


def test_usage_coverage_counts_terminal_delivery_without_receipt_as_missing():
    from peerhub.extensions.monitor_read_model import usage_coverage

    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE bridge_deliveries (
            delivery_id TEXT PRIMARY KEY, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
            certainty TEXT NOT NULL, session_snapshot_json TEXT
        );
        CREATE TABLE bridge_evidence (
            seq INTEGER PRIMARY KEY, delivery_id TEXT, kind TEXT NOT NULL, detail TEXT NOT NULL
        );
        INSERT INTO bridge_deliveries VALUES ('measured','s','cx','TERMINAL','{}');
        INSERT INTO bridge_deliveries VALUES ('invalid-receipt','s','cx','TERMINAL',NULL);
        INSERT INTO bridge_deliveries VALUES ('missing-legacy-session','s','cx','TERMINAL',NULL);
        INSERT INTO bridge_deliveries VALUES ('uncertain','s','cx','STARTED',NULL);
        INSERT INTO bridge_deliveries VALUES ('other-peer','s','cc','TERMINAL',NULL);
    """)
    conn.execute(
        "INSERT INTO bridge_evidence VALUES (1,'measured','session_telemetry',?)",
        (json.dumps({"usage": {"coverage": {"state": "MEASURED", "reason": None}}}),),
    )
    conn.execute("INSERT INTO bridge_evidence VALUES (2,'invalid-receipt','session_telemetry','{broken')")

    result = usage_coverage(conn, peer_id="cx", stream_id="s")

    assert result == {
        "eligible": 3,
        "measured": 1,
        "absent": 0,
        "unsupported": 0,
        "unknown": 1,
        "missing": 1,
    }


def test_usage_coverage_does_not_count_nonterminal_or_other_scope_deliveries():
    from peerhub.extensions.monitor_read_model import usage_coverage

    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE bridge_deliveries (
            delivery_id TEXT PRIMARY KEY, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
            certainty TEXT NOT NULL, session_snapshot_json TEXT
        );
        CREATE TABLE bridge_evidence (
            seq INTEGER PRIMARY KEY, delivery_id TEXT, kind TEXT NOT NULL, detail TEXT NOT NULL
        );
        INSERT INTO bridge_deliveries VALUES ('prestart','s','cx','NOT_STARTED',NULL);
        INSERT INTO bridge_deliveries VALUES ('uncertain','s','cx','STARTED',NULL);
        INSERT INTO bridge_deliveries VALUES ('other-stream','other','cx','TERMINAL',NULL);
    """)

    assert usage_coverage(conn, peer_id="cx", stream_id="s") == {
        "eligible": 0,
        "measured": 0,
        "absent": 0,
        "unsupported": 0,
        "unknown": 0,
        "missing": 0,
    }
