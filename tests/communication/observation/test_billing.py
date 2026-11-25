"""Offline API billing intake, replay, and read views."""
from __future__ import annotations

import json
import hashlib
import os
import sqlite3
from contextlib import closing, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from peerhub.cli.app import main
from peerhub.core.models import Peer
from peerhub.core.store import CoreStore
from peerhub.extensions import billing, bridge_schema, observation
from peerhub.extensions.adapters import api
from peerhub.extensions.adapters.process import ProcessResult
from peerhub.extensions.ask import ask
from peerhub.extensions.bridge import Bridge
from peerhub.extensions.observation_model import EvidenceState, Observation, ObservationExistsError, epoch_to_iso

KEY_A = "fixture-key-secret-before-rotation"
KEY_B = "fixture-key-secret-after-rotation"
COLLECTOR_KEY = "fixture-billing-collector-secret"
STAMP = 1_790_000_000.0
STORE_SECRET = b"fixture-per-store-hmac-key-00001"


@pytest.fixture
def billing_conn():
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(observation._DDL)
        for trigger in observation._TRIGGERS:
            conn.execute(trigger)
        for ddl in bridge_schema._DDL:
            if ddl.strip().startswith(("CREATE TABLE IF NOT EXISTS bridge_evidence ",
                                       "CREATE TABLE IF NOT EXISTS bridge_deliveries ",
                                       "CREATE TRIGGER IF NOT EXISTS bridge_evidence_no_update ")):
                conn.execute(ddl)
        yield conn


def bind(conn, delivery, scope, *, provider="oa", peer=None, at=STAMP, uncertain=False, usage=None):
    peer = peer or provider
    certainty = "STARTED" if uncertain else "TERMINAL"
    conn.execute("INSERT INTO bridge_deliveries(delivery_id,seq,stream_id,peer_id,record_id,record_position,"
                 "attempt,certainty,claim_generation,result_json) VALUES (?,(SELECT COUNT(*)+1 FROM bridge_deliveries),"
                 "'s',?,?,1,1,?,1,?)", (delivery, peer, delivery, certainty, None if uncertain else "{}"))
    evidence(conn, delivery, peer, "about_to_invoke", dict(invocation_no=1, quota_invoked_at=at,
             billing_provider=provider, billing_account_scope=scope))
    evidence(conn, delivery, peer, "runtime_error" if uncertain else "session_telemetry", dict(observed_at=at, usage=usage))


def evidence(conn, delivery, peer, kind, detail):
    conn.execute("INSERT INTO bridge_evidence(delivery_id, stream_id, peer_id, kind, detail) VALUES (?,?,?,?,?)",
                 (delivery, "s", peer, kind, json.dumps(detail)))


def scope_for(identity, provider="oa"):
    return billing._fingerprint(STORE_SECRET, provider, identity, "key")


def test_uncertain_and_missing_usage_stay_unreconciled_after_rotation(billing_conn, monkeypatch):
    conn = billing_conn
    account_a, account_b = scope_for(KEY_A), scope_for(KEY_B)
    bind(conn, "attempt-a", account_a, uncertain=True)
    monkeypatch.setenv("OPENAI_API_KEY", KEY_B)
    evidence(conn, "attempt-a", "oa", "processes_dead", {"observed_at_s": STAMP + 5})
    bind(conn, "attempt-b", account_b, at=STAMP + 10)
    readings = billing.peer_billing(conn, "oa", "oa", STAMP + 20)["observations"]
    assert [(r["reason"], r["account_scope"], r["age_seconds"]) for r in readings] == [
        ("usage_missing", account_b, 10),
        ("execution_uncertain", account_a, 20),
    ]
    assert all(r["status"] == "unreconciled" and r["state"] == "UNKNOWN" and r["unit"] == "token" for r in readings)
    assert all("amount_micros" not in r for r in readings)
    stored = "\n".join(conn.iterdump())
    assert all(secret not in stored + json.dumps(readings) for secret in (KEY_A, KEY_B))


def test_ca_unverified_account_keeps_reported_tokens(billing_conn):
    conn = billing_conn
    usage = {"reported": {"input_tokens": 12, "output_tokens": 3}, "coverage": {"state": "MEASURED"}}
    bind(conn, "ca-attempt", None, provider="ca", usage=usage)
    reading, = billing.peer_billing(conn, "ca", "ca", STAMP + 86_400)["observations"]
    assert reading["account_scope"] is None and reading["reason"] == "account_scope_unverified"
    assert reading["state"] == "STALE" and reading["stored_state"] == "MEASURED"
    assert reading["source"] == "ca.message.usage" and (reading["input_tokens"], reading["output_tokens"]) == (12, 3)


def money_fact(evidence_id, scope, amount=10):
    return Observation(observation_id=evidence_id, subject_ref="oa", kind="billing_balance", source="provider_console",
                       observed_at=epoch_to_iso(STAMP), state=EvidenceState.MEASURED,
                       payload=dict(provider="oa", account_scope=scope, amount_micros=amount, currency="USD"))


def test_billing_capacity_preserves_replay_and_unresolved_evidence(billing_conn, monkeypatch):
    conn = billing_conn
    monkeypatch.setattr(billing, "_MONEY_LIMIT", 2)
    scope = scope_for(KEY_A)
    bind(conn, "old-uncertain", scope, peer="team-openai", uncertain=True,
         usage={"coverage": {"state": "MEASURED"}, "reported": {"input_tokens": 8, "output_tokens": 3}})
    for n in range(9):
        bind(conn, f"attempt-{n}", scope, peer="team-openai", at=STAMP + n + 1)
    original = money_fact("balance-a", scope)
    billing._append_money(conn, original)
    billing._append_money(conn, money_fact("balance-b", scope))
    billing._append_money(conn, original)
    with pytest.raises(ObservationExistsError):
        billing._append_money(conn, money_fact("balance-a", scope_for(KEY_B)))
    with pytest.raises(billing.BillingCapacityError):
        billing._append_money(conn, money_fact("balance-c", scope))

    panel = billing.peer_billing(conn, "team-openai", "oa", STAMP + 30)
    assert panel["truncated"]
    assert [r["kind"] for r in panel["observations"]].count("billing_usage") == 5
    assert [r["kind"] for r in panel["observations"]].count("billing_balance") == 2
    assert [row[0] for row in conn.execute("SELECT kind FROM observations ORDER BY kind")] == ["billing_balance"] * 2
    raw, = conn.execute("SELECT detail FROM bridge_evidence WHERE delivery_id='old-uncertain' AND kind='runtime_error'").fetchone()
    assert json.loads(raw)["usage"]["reported"] == {"input_tokens": 8, "output_tokens": 3}


def test_upgrade_rekeys_legacy_observations_and_bridge_markers(billing_conn):
    conn = billing_conn
    legacy = "key:" + hashlib.sha256(("peerhub.billing.v1\0oa\0key\0" + KEY_A).encode()).hexdigest()
    observation.ObservationStore.append(conn, money_fact("legacy-balance", legacy))
    bind(conn, "legacy-ask", legacy, uncertain=True)
    assert all(fact["account_scope"] is None for fact in billing.peer_billing(conn, "oa", "oa", STAMP)["observations"])
    billing._upgrade_fingerprints(conn, STORE_SECRET)
    billing._upgrade_fingerprints(conn, STORE_SECRET)
    assert all(fact["account_scope"] == scope_for(KEY_A) for fact in billing.peer_billing(conn, "oa", "oa", STAMP)["observations"])
    billing._append_money(conn, money_fact("legacy-balance", scope_for(KEY_A)))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE observations SET source='changed'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE bridge_evidence SET detail='{}'")
    assert legacy not in "\n".join(conn.iterdump())


def test_hmac_scope_depends_on_the_store_and_cannot_be_reused_across_stores(monkeypatch):
    monkeypatch.setattr(billing, "_scope_key", lambda db_path: STORE_SECRET if db_path == "a" else STORE_SECRET[::-1])
    a = billing.BillingScope.from_verified_identity("a", "oa", KEY_A, identity_kind="key")
    b = billing.BillingScope.from_verified_identity("b", "oa", KEY_A, identity_kind="key")
    assert billing.key_signer("a", "oa")(KEY_A) == a.fingerprint
    assert a.fingerprint != b.fingerprint
    assert scope_for(KEY_A, "ga") != a.fingerprint
    assert scope_for(KEY_B) != a.fingerprint
    with pytest.raises(ValueError, match="different store") as raised:
        billing.record_amount("b", a, "balance", source="provider_console", evidence_id=COLLECTOR_KEY,
                              amount_micros=1, currency="USD", observed_at=STAMP)
    combined = repr(a) + repr(b) + repr(raised.value)
    assert all(secret not in combined for secret in (KEY_A, COLLECTOR_KEY, STORE_SECRET.decode(), STORE_SECRET.hex()))


def fake_oa(monkeypatch, responder):
    def open_request(request, *, timeout):
        status, body = responder(request.headers["Authorization"].removeprefix("Bearer "))
        return nullcontext(SimpleNamespace(status=status, read=lambda _n: json.dumps(body).encode()))

    monkeypatch.setattr(api, "http_opener", lambda: SimpleNamespace(open=open_request))


def oa_reply(text, usage=None):
    return 200, {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": text}]}], "usage": usage}


@pytest.mark.parametrize(("target_type", "model", "key_env"), [
    (api.OaRuntimeTarget, "gpt-test", "OPENAI_API_KEY"),
    (api.GaRuntimeTarget, "gemini-test", "GEMINI_API_KEY"),
])
@pytest.mark.parametrize("shape", ["sliced", "escaped", "structured"])
def test_http_failures_do_not_expose_secret_fragments(monkeypatch, target_type, model, key_env, shape):
    escaped = "".join(f"\\u{ord(ch):04x}" for ch in KEY_A)
    if shape == "sliced":
        raw = b" " * (api.EXCERPT_CHARS * 4 - 12) + KEY_A.encode()
    elif shape == "escaped":
        raw = ('{"broken": "' + escaped + '",oops').encode()
    else:
        raw = ('{"error":{"message":"' + escaped + '"}}').encode()
    status = 503
    monkeypatch.setenv(key_env, KEY_A)
    monkeypatch.setattr(api, "http_opener", lambda: SimpleNamespace(open=lambda *a, **k: nullcontext(
        SimpleNamespace(status=status, read=lambda _n: raw))))
    runtime = target_type(Path.cwd(), model=model)
    with pytest.raises(api.RuntimeTargetError) as raised:
        list(runtime.deliver("session", SimpleNamespace(body="check"), []))
    output = str(raised.value) + repr(raised.value) + repr(runtime._call("check"))
    assert "HTTP 503" in output
    assert KEY_A[:12] not in output and escaped[:30] not in output


@pytest.mark.parametrize(("target_type", "model", "usage"), [
    (api.OaRuntimeTarget, "gpt-test", {"input_tokens": 3, "output_tokens": False, "total_tokens": 2**63, "raw": KEY_A}),
    (api.CaRuntimeTarget, "claude-test", {"input_tokens": 3, "output_tokens": KEY_A, "total_tokens": 5, "raw": KEY_A}),
])
def test_outcome_usage_repr_contains_only_validated_counters(target_type, model, usage):
    runtime = target_type(Path.cwd(), model=model)
    outcome = runtime._reply(KEY_A, usage, complete=True, truncated=False, why="")
    assert outcome.usage == {"input_tokens": 3}
    assert KEY_A not in repr(outcome)


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
    scope, events = target.prepare_billing_delivery(SimpleNamespace(body="hello"), [], scope_for)
    monkeypatch.setenv("OPENAI_API_KEY", KEY_B)
    if error:
        with pytest.raises(api.RuntimeTargetError) as raised:
            list(events)
        output = str(raised.value)
    else:
        output = json.dumps(list(events))
    assert seen == [KEY_A]
    assert scope == scope_for(KEY_A)
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
    scope, events = target.prepare_billing_delivery(SimpleNamespace(body="hello"), [], None)
    output = json.dumps(list(events))
    assert scope is None and "[REDACTED]" in output
    assert all(secret not in output + caplog.text for secret in (KEY_A, KEY_B, COLLECTOR_KEY))


def test_ask_billing_replay_and_read_views(tmp_path, monkeypatch, capsys, caplog):
    db = tmp_path / "core.db"
    peer = "team-openai"
    CoreStore(db).register_peer(Peer(peer_id=peer, adapter_ref="oa"))
    called = []

    def respond(key):
        called.append(key)
        if len(called) == 3:
            return 200, {"status": "completed", "output": {"invalid": key},
                         "usage": {"input_tokens": 7, "output_tokens": 2, "extra": key}}
        if len(called) == 4:
            return 429, {"error": {"code": "insufficient_quota", "message": key}}
        usage = {"input_tokens": 8, "output_tokens": 2} if key == KEY_A else None
        return oa_reply(f"reply {key}", usage)

    fake_oa(monkeypatch, respond)
    monkeypatch.setenv("OPENAI_API_KEY", KEY_A)
    runtime = api.OaRuntimeTarget(tmp_path, model="gpt-test")
    first = ask(db, peer, "question", request_id="first", runtime=runtime)
    monkeypatch.setenv("OPENAI_API_KEY", KEY_B)
    replay = ask(db, peer, "question", request_id="first", runtime=runtime)
    second = ask(db, peer, "question", request_id="second", runtime=runtime)
    uncertain = ask(db, peer, "question", request_id="third", runtime=runtime)
    blocked = ask(db, peer, "question", request_id="third", runtime=runtime)
    refused = ask(db, peer, "question", request_id="fourth", runtime=runtime)
    results = [first, replay, second, uncertain, blocked, refused]
    assert [result["status"] for result in results] == [
        "delivered", "recovered_terminal", "delivered", "uncertain", "blocked_uncertain", "not_executed"]
    assert called == [KEY_A, KEY_B, KEY_B, KEY_B]

    a = billing.BillingScope.from_verified_identity(db, "oa", KEY_A, identity_kind="key")
    b = billing.BillingScope.from_verified_identity(db, "oa", KEY_B, identity_kind="key")
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
    facts = next(p for p in diag if p["peer_id"] == peer)["billing"]["observations"]
    assert sorted(row["observation_id"] for row in facts) == sorted(
        row["observation_id"] for row in next(p for p in monitor if p["peer_id"] == peer)["billing"]["observations"])
    assert {row["kind"] for row in facts} == {"billing_usage", "billing_balance", "billing_estimate", "billing_charge"}
    assert len(facts) == 6
    assert {row["reason"]: row["account_scope"] for row in facts if row["kind"] == "billing_usage"} == {
        None: a.fingerprint, "usage_missing": b.fingerprint, "execution_uncertain": b.fingerprint,
    }
    unknown, = [row for row in facts if row.get("reason") == "execution_uncertain"]
    assert (unknown["source"], unknown["input_tokens"], unknown["output_tokens"]) == ("oa.response.usage", 7, 2)
    for view in ("--compact", "--expand"):
        assert main([*monitor_args, "--view", "plain", view]) == 0
        readable = capsys.readouterr().out
        assert "billing unreconciled" in readable
        monitor_text += readable
    with closing(sqlite3.connect(db)) as conn:
        dump = "\n".join(conn.iterdump())
    combined = diag_text + monitor_text + dump + caplog.text + str(raised.value) + json.dumps(results)
    assert all(secret not in combined for secret in (KEY_A, KEY_B, COLLECTOR_KEY))


def test_scope_key_is_persistent_private_and_per_store(tmp_path):
    first_db, second_db = tmp_path / "first.db", tmp_path / "second.db"
    for db in (first_db, second_db):
        CoreStore(db)
    first = billing.BillingScope.from_verified_identity(first_db, "oa", KEY_A, identity_kind="key")
    second = billing.BillingScope.from_verified_identity(second_db, "oa", KEY_A, identity_kind="key")
    assert first == billing.BillingScope.from_verified_identity(first_db, "oa", KEY_A, identity_kind="key")
    assert first.fingerprint != second.fingerprint
    key_file = tmp_path / ".first.db.billing" / "key"
    secret = key_file.read_bytes()
    assert len(secret) == 32
    if os.name != "nt":
        assert key_file.stat().st_mode & 0o077 == key_file.parent.stat().st_mode & 0o077 == 0
    with pytest.raises(ValueError, match="different store") as raised:
        billing.record_amount(second_db, first, "balance", source="provider_console",
                              evidence_id="statement", amount_micros=10, currency="USD", observed_at=STAMP)
    with closing(sqlite3.connect(first_db)) as conn:
        dump = "\n".join(conn.iterdump())
    assert all(value not in dump + repr(first) + repr(raised.value) for value in (secret.hex(), KEY_A))
    key_file.unlink()
    assert billing.key_signer(first_db, "oa")(KEY_A) is None
    assert not key_file.exists()


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
    assert fact["account_scope"] == billing.BillingScope.from_verified_identity(db, "oa", KEY_A, identity_kind="key").fingerprint
