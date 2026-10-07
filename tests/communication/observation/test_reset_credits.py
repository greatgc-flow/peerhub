"""Codex `rateLimitResetCredits` capture: probe parsing (fake JSON-RPC) and persistence as kind `reset_credit`.
Oracles are literals (wire values copied from a real response shape), not recomputed from the code under test."""
import json
import sqlite3
import subprocess

import pytest

from peerhub.core.store import CoreStore
from peerhub.extensions.observation_model import KNOWN_KINDS, EvidenceState
from peerhub.extensions.quota_capture import refresh_quota
from peerhub.extensions.quota_probes import poll_codex_usage
from peerhub.extensions.quota_types import ResetCreditObserved, UsageObserved

GRANTED, EXPIRES = 1790707966, 1793299966  # 2026-09-29T18:52:46Z .. 2026-10-29T18:52:46Z
RID = "RateLimitResetCredit_0123456789abcdef0123456789abcdef"


class Ids:
    def new_id(self, prefix):
        return f"{prefix}-1"


def credit(**kw):
    c = {"id": RID, "resetType": "codexRateLimits", "status": "available", "grantedAt": GRANTED, "expiresAt": EXPIRES,
         "title": "Full reset (Weekly + 5 hr)", "description": "free text"}
    c.update(kw)
    return c


def envelope(**extra):
    env = {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 40, "windowDurationMins": 300, "resetsAt": 1787200158}, "secondary": None},
           "rateLimitsByLimitId": {}}
    env.update(extra)
    return env


def run_probe(monkeypatch, env):
    lines = ['{"id": 0, "result": {"codexHome": "/x"}}\n', json.dumps({"id": 1, "result": env}) + "\n", ""]

    class Out:
        def readline(self):
            return lines.pop(0) if lines else ""

    class In:
        def write(self, _): pass
        def flush(self): pass

    class Proc:
        stdin, stdout, pid = In(), Out(), 9999
        def poll(self): return None

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: Proc())
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_binary", lambda _p, _s=None: "dummy.exe")
    res = poll_codex_usage(Ids(), "cx", "default", deadline_sec=1.0)
    return [r for r in res if isinstance(r, UsageObserved)], [r for r in res if isinstance(r, ResetCreditObserved)]


def test_kind_is_registered():
    assert "reset_credit" in KNOWN_KINDS


def test_one_credit_measured(monkeypatch):
    usage, rc = run_probe(monkeypatch, envelope(rateLimitResetCredits={"availableCount": 1, "credits": [credit()]}))
    assert len(usage) == 1 and usage[0].evidence.state == EvidenceState.MEASURED  # quota path unchanged (positive control)
    assert len(rc) == 1 and rc[0].state == EvidenceState.MEASURED
    assert rc[0].payload["available_count"] == 1 and rc[0].payload["nearest_expires_at"] == EXPIRES
    assert rc[0].payload["credits"] == [{"id_ref": RID, "reset_type": "codexRateLimits", "status": "available",
                                         "granted_at": GRANTED, "expires_at": EXPIRES, "title": "Full reset (Weekly + 5 hr)"}]
    assert "description" not in json.dumps(rc[0].payload)


def test_zero_credits_is_measured_zero(monkeypatch):
    _, rc = run_probe(monkeypatch, envelope(rateLimitResetCredits={"availableCount": 0, "credits": []}))
    assert rc[0].state == EvidenceState.MEASURED
    assert rc[0].payload["available_count"] == 0 and rc[0].payload["credits"] == [] and rc[0].payload["nearest_expires_at"] is None


@pytest.mark.parametrize("value", ["absent", None])
def test_missing_field_is_unknown_never_zero(monkeypatch, value):
    env = envelope() if value == "absent" else envelope(rateLimitResetCredits=None)
    _, rc = run_probe(monkeypatch, env)
    assert rc[0].state == EvidenceState.UNKNOWN
    assert "available_count" not in rc[0].payload and "credits" not in rc[0].payload


def test_multiple_credits_sorted_and_nearest_is_earliest_available(monkeypatch):
    cs = [credit(id="c-late", expiresAt=EXPIRES + 500), credit(id="c-used", status="redeemed", expiresAt=EXPIRES - 9000),
          credit(id="c-early", expiresAt=EXPIRES - 100)]
    _, rc = run_probe(monkeypatch, envelope(rateLimitResetCredits={"availableCount": 2, "credits": cs}))
    assert [c["id_ref"] for c in rc[0].payload["credits"]] == ["c-used", "c-early", "c-late"]  # by expires_at asc
    assert rc[0].payload["nearest_expires_at"] == EXPIRES - 100  # redeemed credit ignored
    assert rc[0].payload["available_count"] == 2


BAD = [
    "notadict", {"availableCount": "1", "credits": []}, {"availableCount": True, "credits": []}, {"availableCount": -1, "credits": []},
    {"availableCount": 1.5, "credits": []}, {"availableCount": float("nan"), "credits": []}, {"availableCount": 1},
    {"availableCount": 1, "credits": "x"}, {"availableCount": 1, "credits": [5]},
    {"availableCount": 1, "credits": [credit(expiresAt=GRANTED - 1)]},
    {"availableCount": 1, "credits": [credit(grantedAt=-5)]}, {"availableCount": 1, "credits": [credit(expiresAt="9")]},
    {"availableCount": 1, "credits": [credit(expiresAt=float("inf"))]}, {"availableCount": 1, "credits": [credit(id="")]},
    {"availableCount": 1, "credits": [credit(status=3)]}, {"availableCount": 1, "credits": [credit(title=None)]},
    {"availableCount": 1, "credits": [credit(), credit(expiresAt=float("nan"))]},  # one bad credit poisons all: no partial garbage
]


@pytest.mark.parametrize("bad", BAD, ids=range(len(BAD)))
def test_malformed_is_error_without_partial_payload(monkeypatch, bad):
    usage, rc = run_probe(monkeypatch, envelope(rateLimitResetCredits=bad))
    assert usage and usage[0].evidence.state == EvidenceState.MEASURED  # quota evidence is independent
    assert rc[0].state == EvidenceState.ERROR
    assert set(rc[0].payload) == {"reason"} and isinstance(rc[0].payload["reason"], str)


def test_credits_still_captured_when_rate_limit_windows_fail(monkeypatch):
    env = {"rateLimits": None, "rateLimitsByLimitId": None, "rateLimitResetCredits": {"availableCount": 1, "credits": [credit()]}}
    usage, rc = run_probe(monkeypatch, env)
    assert usage[0].evidence.state == EvidenceState.ERROR and rc[0].state == EvidenceState.MEASURED


# ----------------------------------------------------------------------------- persistence
def rows(db):
    with sqlite3.connect(db) as c:
        return c.execute("SELECT subject_ref, resource_pool_ref, kind, state, payload_json, source FROM observations WHERE kind='reset_credit'").fetchall()


def test_refresh_persists_reset_credit_with_pool(tmp_path, monkeypatch):
    db = tmp_path / "core.db"
    CoreStore(db)
    payload = {"available_count": 1, "credits": [{"id_ref": RID, "reset_type": "codexRateLimits", "status": "available",
                                                  "granted_at": GRANTED, "expires_at": EXPIRES, "title": "T"}], "nearest_expires_at": EXPIRES}
    rc = ResetCreditObserved(observation_id="o", instance_id="cx", profile_id="default", state=EvidenceState.MEASURED,
                             source_tag="codex_app_server", observed_at=100, captured_at=100, evidence_ref="probe:cx:reset-credits", payload=payload)
    res = refresh_quota(db, ["cx"], pollers={"cx": lambda **kw: [rc]})
    (subj, pool, kind, state, pj, src), = rows(db)
    assert (subj, pool, kind, state, src) == ("cx", "credit:cx:reset", "reset_credit", "MEASURED", "codex_app_server")
    assert json.loads(pj) == {"evidence_ref": "probe:cx:reset-credits", **payload}
    assert res["status"] == "OK"
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT kind FROM resource_pools WHERE resource_pool_id='credit:cx:reset'").fetchone() == ("ACCOUNT",)


@pytest.mark.parametrize("state,payload,cond", [(EvidenceState.UNKNOWN, {"reason": "reset_credits_absent"}, None),
                                                (EvidenceState.ERROR, {"reason": "malformed"}, "reset_credits_malformed")])
def test_refresh_unknown_and_error_carry_no_counts(tmp_path, state, payload, cond):
    db = tmp_path / "core.db"
    CoreStore(db)
    rc = ResetCreditObserved("o", "cx", "default", state, "codex_app_server", 100, 100, "probe:cx:reset-credits", payload)
    res = refresh_quota(db, ["cx"], pollers={"cx": lambda **kw: [rc]})
    (_, pool, _, st, pj, _), = rows(db)
    p = json.loads(pj)
    assert st == state.value and pool == "credit:cx:reset" and "available_count" not in p and p.get("condition") == cond
    assert res["status"] == ("PARTIAL" if state == EvidenceState.ERROR else "OK")
