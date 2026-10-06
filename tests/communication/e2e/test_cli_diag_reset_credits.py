"""`diag quota` reset-credit section: read-only, hours computed at read time, WARN below 72h. Rows are seeded with raw SQL; oracles are literals."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.extensions import diag_quota
from tests.communication.harness.observation import ObservationHarness

pytestmark = [pytest.mark.integration, pytest.mark.diag]

READ_AT = 1793299966.0 - 100 * 3600  # 100h before the credit expires
EXPIRES = 1793299966
RID = "RateLimitResetCredit_0123456789abcdef0123456789abcdef"
ID_HASH = hashlib.sha256(RID.encode()).hexdigest()[:12]


def iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def payload(*creds, count=None):
    cs = [{"id_ref": c.get("id", RID), "reset_type": "codexRateLimits", "status": c.get("status", "available"), "granted_at": EXPIRES - 2592000,
           "expires_at": c.get("exp", EXPIRES), "title": c.get("title", "Full reset (Weekly + 5 hr)")} for c in creds]
    av = [c["expires_at"] for c in cs if c["status"] == "available"]
    return {"evidence_ref": "probe:cx:reset-credits", "available_count": len(av) if count is None else count, "credits": cs,
            "nearest_expires_at": min(av) if av else None}


_N = iter(range(10**6))


def seed(db, rows, captured=READ_AT - 10):
    with closing(sqlite3.connect(db)) as c:
        if not c.execute("SELECT 1 FROM resource_pools WHERE resource_pool_id='credit:cx:reset'").fetchone():  # pools are append-only
            c.execute("INSERT INTO resource_pools (resource_pool_id, provider, kind, metadata_json) VALUES ('credit:cx:reset','codex','ACCOUNT','{}')")
        for i, (subj, state, pl) in enumerate(rows):
            c.execute("INSERT INTO observations (observation_id, subject_ref, resource_pool_ref, kind, source, state, payload_json, observed_at, captured_at, effective_at_us) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)", (f"rc{next(_N)}", subj, "credit:cx:reset", "reset_credit", "codex_app_server", state, json.dumps(pl),
                                                      iso(captured), iso(captured), int(captured * 1e6)))
        c.commit()


@pytest.fixture
def h(tmp_path):
    return ObservationHarness(tmp_path / "ws")


def report(h, read_at=READ_AT, **kw):
    return diag_quota.quota_report(h.db_path, read_at=read_at, **kw)


def test_one_credit_json_section(h):
    seed(h.db_path, [("cx", "MEASURED", payload({}))])
    (it,) = report(h)["reset_credits"]
    assert (it["subject_ref"], it["state"], it["available_count"]) == ("cx", "MEASURED", 1)
    assert it["nearest_expires_at"] == "2026-10-29T18:52:46Z" and it["hours_until_nearest_expiry"] == 100.0 and it["warn"] is False
    assert it["credits"] == [{"id_hash": ID_HASH, "reset_type": "codexRateLimits", "status": "available", "granted_at": EXPIRES - 2592000,
                              "expires_at": "2026-10-29T18:52:46Z", "hours_until_expiry": 100.0, "title": "Full reset (Weekly + 5 hr)", "warn": False}]


@pytest.mark.parametrize("hours_left,warn", [(72 * 3600 + 1, False), (72 * 3600, False), (72 * 3600 - 1, True), (0, True), (-3600, True)])
def test_warn_boundary_is_strictly_below_72h(h, hours_left, warn):
    seed(h.db_path, [("cx", "MEASURED", payload({}))], captured=EXPIRES - hours_left - 10)
    (it,) = report(h, read_at=EXPIRES - hours_left)["reset_credits"]
    assert it["warn"] is warn and it["credits"][0]["warn"] is warn and it["hours_until_nearest_expiry"] == hours_left / 3600


def test_hours_are_computed_at_read_time_not_capture_time(h):
    seed(h.db_path, [("cx", "MEASURED", payload({}))])
    a = report(h, read_at=READ_AT)["reset_credits"][0]
    b = report(h, read_at=READ_AT + 50 * 3600)["reset_credits"][0]
    assert a["hours_until_nearest_expiry"] == 100.0 and b["hours_until_nearest_expiry"] == 50.0 and b["warn"] is True
    assert b["state"] == "STALE"  # freshness TTL applies like other quota evidence; the hours stay a pure read-time function


def test_non_available_credit_never_warns_and_does_not_count_for_nearest(h):
    seed(h.db_path, [("cx", "MEASURED", payload({"status": "redeemed", "exp": EXPIRES - 3600, "id": "old"}, {}))])
    (it,) = report(h)["reset_credits"]
    assert it["warn"] is False and it["hours_until_nearest_expiry"] == 100.0
    assert [c["status"] for c in it["credits"]] == ["redeemed", "available"] and it["credits"][0]["warn"] is False


def test_zero_credits_shows_zero_not_unknown(h):
    seed(h.db_path, [("cx", "MEASURED", payload())])
    (it,) = report(h)["reset_credits"]
    assert it["state"] == "MEASURED" and it["available_count"] == 0 and it["nearest_expires_at"] is None
    assert it["hours_until_nearest_expiry"] is None and it["warn"] is False and it["credits"] == []


def test_unknown_and_error_show_no_count(h):
    seed(h.db_path, [("cx", "UNKNOWN", {"evidence_ref": "e", "reason": "reset_credits_absent"}),
                     ("cy", "ERROR", {"evidence_ref": "e", "reason": "bad", "condition": "reset_credits_malformed"})])
    items = {i["subject_ref"]: i for i in report(h)["reset_credits"]}
    assert items["cx"]["state"] == "UNKNOWN" and items["cx"]["available_count"] is None and items["cx"]["credits"] == []
    assert items["cy"]["state"] == "ERROR" and items["cy"]["available_count"] is None and items["cy"]["condition"] == "reset_credits_malformed"


def test_latest_row_wins_and_peer_filter(h):
    seed(h.db_path, [("cx", "MEASURED", payload({}, {"id": "b", "exp": EXPIRES + 10}))], captured=READ_AT - 500)
    seed(h.db_path, [("cx", "MEASURED", payload({})), ("cz", "MEASURED", payload({}))])
    rep = report(h)
    assert [(i["subject_ref"], i["available_count"]) for i in rep["reset_credits"]] == [("cx", 1), ("cz", 1)]
    assert [i["subject_ref"] for i in report(h, peer="cz")["reset_credits"]] == ["cz"]
    assert rep["pools"] == []  # reset credits are not quota pools


def test_no_evidence_gives_empty_list(h):
    assert report(h)["reset_credits"] == []


def test_table_and_cli_json_and_no_full_ids(h, tmp_path, capsys):
    seed(h.db_path, [("cx", "MEASURED", payload({}, {"id": "soon", "exp": int(READ_AT) + 3600}))], captured=int(READ_AT) - 10)
    table = diag_quota.format_quota_table(report(h))
    assert "reset credits" in table and "cx" in table and "available=2" in table and "WARN" in table
    assert "nearest=" in table and "2026-10-25T" in table and ID_HASH in table
    assert RID not in table and "soon" not in table
    import time
    h = ObservationHarness(tmp_path / "ws_live")  # the CLI reads at the real clock: seed relative to now
    now = time.time()
    seed(h.db_path, [("cx", "MEASURED", payload({"exp": int(now) + 7200}))], captured=now - 10)
    before = Path(h.db_path).read_bytes()
    assert main(["--db", str(h.db_path), "diag", "quota", "--json"]) == 0
    out = capsys.readouterr().out
    rep = json.loads(out)
    it = rep["reset_credits"][0]
    assert it["subject_ref"] == "cx" and it["state"] == "MEASURED" and it["warn"] is True and 1.9 < it["hours_until_nearest_expiry"] <= 2.0
    assert RID not in out and ID_HASH in out
    assert main(["--db", str(h.db_path), "diag", "quota"]) == 0
    assert "reset credits" in capsys.readouterr().out
    assert main(["--db", str(h.db_path), "diag"]) == 0
    assert "reset credits" in capsys.readouterr().out  # dashboard summary
    assert Path(h.db_path).read_bytes() == before  # Diag never writes

