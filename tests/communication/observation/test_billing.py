"""Offline API billing intake, replay, and read views."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from peerhub.cli.app import main
from peerhub.extensions import billing, bridge_schema, observation
from peerhub.extensions.adapters import api
from peerhub.extensions.adapters.process import ProcessResult
from peerhub.extensions.ask import ask
from peerhub.extensions.bridge import Bridge
from peerhub.extensions.observation_model import ObservationExistsError

KEY_A = "fixture-key-secret-before-rotation"
KEY_B = "fixture-key-secret-after-rotation"
COLLECTOR_KEY = "fixture-billing-collector-secret"
STAMP = 1_790_000_000.0


@pytest.fixture
def billing_conn():
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(observation._DDL)
        for trigger in observation._TRIGGERS:
            conn.execute(trigger)
        for ddl in bridge_schema._DDL:
            if ddl.strip().startswith("CREATE TABLE IF NOT EXISTS bridge_evidence "):
                conn.execute(ddl)
        yield conn


def bind(conn, delivery, scope, *, provider="oa", invocation=1):
    conn.execute("INSERT INTO bridge_evidence(delivery_id, stream_id, peer_id, kind, detail) VALUES (?,?,?,?,?)",
                 (delivery, "s", provider, "about_to_invoke", json.dumps(
                     dict(invocation_no=invocation, billing_provider=provider, billing_account_scope=scope))))


def test_uncertain_and_missing_usage_stay_unreconciled_after_rotation(billing_conn, monkeypatch):
    conn = billing_conn
    account_a = billing.BillingScope.from_verified_identity("oa", KEY_A, identity_kind="key")
    account_b = billing.BillingScope.from_verified_identity("oa", KEY_B, identity_kind="key")
    bind(conn, "attempt-a", account_a.fingerprint)
    billing.record_delivery(conn, "attempt-a", STAMP, uncertain=True)
    monkeypatch.setenv("OPENAI_API_KEY", KEY_B)
    billing.record_delivery(conn, "attempt-a", STAMP + 10, uncertain=True)
    bind(conn, "attempt-b", account_b.fingerprint)
    billing.record_delivery(conn, "attempt-b", STAMP + 10)
    readings = billing.peer_billing(conn, "oa", "oa", STAMP + 20)["observations"]
    assert [(r["reason"], r["account_scope"], r["age_seconds"]) for r in readings] == [
        ("usage_missing", account_b.fingerprint, 10),
        ("execution_uncertain", account_a.fingerprint, 20),
    ]
    assert all(r["status"] == "unreconciled" and r["state"] == "UNKNOWN" and r["unit"] == "token" for r in readings)
    assert all("amount_micros" not in r for r in readings)
    stored = "\n".join(conn.iterdump())
    assert all(secret not in stored + json.dumps(readings) for secret in (KEY_A, KEY_B))


def test_ca_unverified_account_keeps_reported_tokens(billing_conn):
    conn = billing_conn
    bind(conn, "ca-attempt", None, provider="ca")
    usage = {"reported": {"input_tokens": 12, "output_tokens": 3}, "coverage": {"state": "MEASURED"}}
    billing.record_delivery(conn, "ca-attempt", STAMP, usage=usage)
    reading, = billing.peer_billing(conn, "ca", "ca", STAMP + 86_400)["observations"]
    assert reading["account_scope"] is None and reading["reason"] == "account_scope_unverified"
    assert reading["state"] == "STALE" and reading["stored_state"] == "MEASURED"
    assert reading["source"] == "ca.message.usage" and (reading["input_tokens"], reading["output_tokens"]) == (12, 3)


def fake_oa(monkeypatch, responder):
    def open_request(request, *, timeout):
        status, body = responder(request.headers["Authorization"].removeprefix("Bearer "))
        return nullcontext(SimpleNamespace(status=status, read=lambda _n: json.dumps(body).encode()))

    monkeypatch.setattr(api, "http_opener", lambda: SimpleNamespace(open=open_request))


def oa_reply(text, usage=None):
    return 200, {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": text}]}], "usage": usage}


@pytest.mark.parametrize("error", [False, True])
def test_http_scope_uses_the_transmitted_key_and_sanitizes_outputs(monkeypatch, error, caplog):
    seen = []

    def respond(key):
        seen.append(key)
        if error:
            return 503, {"error": {"message": f"rejected {key}"}}
        return oa_reply(f"echo {key}")

    fake_oa(monkeypatch, respond)
    monkeypatch.setenv("OPENAI_API_KEY", KEY_A)
    target = api.OaRuntimeTarget(Path.cwd(), model="gpt-test")
    scope, events = target.prepare_billing_delivery(SimpleNamespace(body="hello"), [])
    monkeypatch.setenv("OPENAI_API_KEY", KEY_B)
    if error:
        with pytest.raises(api.RuntimeTargetError) as raised:
            list(events)
        output = str(raised.value)
    else:
        output = json.dumps(list(events))
    assert seen == [KEY_A]
    assert scope == billing.BillingScope.from_verified_identity("oa", KEY_A, identity_kind="key")
    assert "[REDACTED]" in output
    assert all(secret not in output + repr(scope) + caplog.text for secret in (KEY_A, KEY_B))


def test_ca_scrubs_collector_credentials_from_child_and_result(monkeypatch, caplog):
    monkeypatch.setenv("PEERHUB_BILLING_COLLECTOR_TOKEN", COLLECTOR_KEY)
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY", KEY_A)
    monkeypatch.setenv("OPENAI_API_KEY", KEY_B)

    def process(argv, *, env, stdin, **kwargs):
        assert all(secret not in repr((argv, env, stdin)) for secret in (KEY_A, KEY_B, COLLECTOR_KEY))
        output = json.dumps({"type": "message", "stop_reason": "end_turn", "content": [
            {"type": "text", "text": f"echo {COLLECTOR_KEY} {KEY_A} {KEY_B}"}]})
        result = ProcessResult(123, 0, output.encode(), b"", False, False, 0.1)
        return SimpleNamespace(start=lambda: None, wait=lambda: result)

    monkeypatch.setattr(api, "BoundedProcess", process)
    target = api.CaRuntimeTarget(Path.cwd(), command=["fake-ant"], model="claude-test")
    scope, events = target.prepare_billing_delivery(SimpleNamespace(body="hello"), [])
    output = json.dumps(list(events))
    assert scope is None and "[REDACTED]" in output
    assert all(secret not in output + caplog.text for secret in (KEY_A, KEY_B, COLLECTOR_KEY))


def test_ask_billing_replay_and_read_views(tmp_path, monkeypatch, capsys, caplog):
    db = tmp_path / "core.db"
    called = []

    def respond(key):
        called.append(key)
        if len(called) == 3:
            return 503, {"error": {"message": key}}
        if len(called) == 4:
            return 429, {"error": {"code": "insufficient_quota", "message": key}}
        usage = {"input_tokens": 8, "output_tokens": 2} if key == KEY_A else None
        return oa_reply(f"reply {key}", usage)

    fake_oa(monkeypatch, respond)
    monkeypatch.setenv("OPENAI_API_KEY", KEY_A)
    runtime = api.OaRuntimeTarget(tmp_path, model="gpt-test")
    first = ask(db, "oa", "question", request_id="first", runtime=runtime)
    monkeypatch.setenv("OPENAI_API_KEY", KEY_B)
    replay = ask(db, "oa", "question", request_id="first", runtime=runtime)
    second = ask(db, "oa", "question", request_id="second", runtime=runtime)
    uncertain = ask(db, "oa", "question", request_id="third", runtime=runtime)
    blocked = ask(db, "oa", "question", request_id="third", runtime=runtime)
    refused = ask(db, "oa", "question", request_id="fourth", runtime=runtime)
    results = [first, replay, second, uncertain, blocked, refused]
    assert [result["status"] for result in results] == [
        "delivered", "recovered_terminal", "delivered", "uncertain", "blocked_uncertain", "not_executed"]
    assert called == [KEY_A, KEY_B, KEY_B, KEY_B]

    a = billing.BillingScope.from_verified_identity("oa", KEY_A, identity_kind="key")
    b = billing.BillingScope.from_verified_identity("oa", KEY_B, identity_kind="key")
    money = dict(evidence_id=COLLECTOR_KEY, amount_micros=1_250_000, currency="USD", observed_at=STAMP)
    obs = billing.record_amount(db, a, "balance", source="provider_console", **money)
    assert billing.record_amount(db, a, "balance", source="provider_console", **money) == obs
    with pytest.raises(ObservationExistsError) as raised:
        billing.record_amount(db, b, "balance", source="provider_console", **money)
    billing.record_amount(db, a, "estimate", source="collector_estimate", **money)
    billing.record_amount(db, b, "charge", source="provider_statement", **money)

    assert main(["--db", str(db), "diag", "--json"]) == 0
    diag_text = capsys.readouterr().out
    diag = json.loads(diag_text)["sections"]["peers"]["data"]["peers"]
    monitor_args = ["--db", str(db), "monitor", "--snapshot", "--no-probe", "--group", "peers"]
    assert main([*monitor_args, "--json"]) == 0
    monitor_text = capsys.readouterr().out
    monitor = json.loads(monitor_text)["groups"]["peers"]["data"]["peers"]
    facts = next(p for p in diag if p["peer_id"] == "oa")["billing"]["observations"]
    assert sorted(row["observation_id"] for row in facts) == sorted(
        row["observation_id"] for row in next(p for p in monitor if p["peer_id"] == "oa")["billing"]["observations"])
    assert {row["kind"] for row in facts} == {"billing_usage", "billing_balance", "billing_estimate", "billing_charge"}
    assert len(facts) == 6
    assert {row["reason"]: row["account_scope"] for row in facts if row["kind"] == "billing_usage"} == {
        None: a.fingerprint, "usage_missing": b.fingerprint, "execution_uncertain": b.fingerprint,
    }
    for view in ("--compact", "--expand"):
        assert main([*monitor_args, "--view", "plain", view]) == 0
        readable = capsys.readouterr().out
        assert "billing unreconciled" in readable
        monitor_text += readable
    with closing(sqlite3.connect(db)) as conn:
        dump = "\n".join(conn.iterdump())
    combined = diag_text + monitor_text + dump + caplog.text + str(raised.value) + json.dumps(results)
    assert all(secret not in combined for secret in (KEY_A, KEY_B, COLLECTOR_KEY))


def test_interrupted_invocation_keeps_original_scope_on_recovery(tmp_path, monkeypatch):
    db = tmp_path / "core.db"
    calls = []

    def respond(key):
        calls.append(key)
        return oa_reply("unexpected")

    fake_oa(monkeypatch, respond)
    monkeypatch.setenv("OPENAI_API_KEY", KEY_A)
    runtime = api.OaRuntimeTarget(tmp_path, model="gpt-test")

    def stop_before_runtime(self, point):
        if point == "bridge.before_runtime_invoke":
            raise RuntimeError("interrupted after durable invocation marker")

    with monkeypatch.context() as faults:
        faults.setattr(Bridge, "_fire", stop_before_runtime)
        with pytest.raises(RuntimeError, match="durable invocation marker"):
            ask(db, "oa", "question", request_id="first", runtime=runtime)
    monkeypatch.setenv("OPENAI_API_KEY", KEY_B)
    for _ in range(2):
        result = ask(db, "oa", "question", request_id="first", runtime=runtime)
        assert result["status"] == "blocked_uncertain"
    assert calls == []

    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        fact, = billing.peer_billing(conn, "oa", "oa", STAMP)["observations"]
    assert fact["reason"] == "execution_uncertain"
    assert fact["account_scope"] == billing.BillingScope.from_verified_identity("oa", KEY_A, identity_kind="key").fingerprint
