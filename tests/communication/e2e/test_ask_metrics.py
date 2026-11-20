"""M2 ask metrics exercised with SQLite and scripted provider evidence."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pytest

from peerhub.cli.app import main
from peerhub.extensions import session_receipts
from peerhub.extensions.ask import ask
from tests.communication.fakes import FakeRuntimeTarget


def test_reported_zero_is_different_from_absent_usage():
    args = {"provider_kind": "cc", "usage": {"coverage": {"state": "MEASURED"}, "normalized": {
        "uncached_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}}}
    assert session_receipts.ask_tokens(args) == {
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_tokens": None, "prompt_tokens": None,
    }
    assert all(value is None for value in session_receipts.ask_tokens({}).values())


@pytest.mark.parametrize(("second_start", "second_reset", "last_left", "expected"), [
    (None, 100, 0.6, 20),
    (None, 100, 0.8, 0),
    (22, 100, 0.6, None),  # competitor overlaps only the interior of the interval
    (None, 200, 0.6, None),
])
def test_pool_delta_requires_exclusive_same_window_readings(second_start, second_reset, last_left, expected):
    before = session_receipts.QuotaReading("first", "quota:cx:X-7D", "cx", "source", 20, 20, 0.8, 1, 100)
    after = session_receipts.QuotaReading("second", "quota:cx:X-7D", "cx", "source", 30, 30, last_left, 1, second_reset)
    competitors = [] if second_start is None else [(second_start, 25)]
    delta = session_receipts.comparable_pool_delta(before, after, competitors)
    assert delta == (None if expected is None else pytest.approx(expected))


def test_pool_cost_uses_all_accepted_asks_in_cohort():
    from peerhub.cli.profile_stats import aggregate_profile

    rows = [
        {"wall_seconds": 0, "input_tokens": 0, "pool_delta_pp": 6, "outcome": "rejected"},
        {"wall_seconds": 10, "input_tokens": 100, "pool_delta_pp": 0, "outcome": "accepted"},
        {"wall_seconds": None, "input_tokens": None, "pool_delta_pp": None, "outcome": "accepted"},
    ]
    result = aggregate_profile(rows)
    assert result["n"] == 3
    assert result["p50_wall_seconds"] == 5
    assert result["coverage_pct"]["input_tokens"] == pytest.approx(200 / 3)
    assert result["tokens_per_ask"]["input_tokens"] == 50
    assert result["pool_pct_per_accepted_answer"] == 3
    assert aggregate_profile(rows[:1])["pool_pct_per_accepted_answer"] is None


@pytest.fixture
def old_bridge():
    from peerhub.core.migrations import _baseline
    from peerhub.extensions import bridge, bridge_registry, observation

    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        _baseline(conn)
        conn.executescript(observation._DDL)
        for stmt in bridge._DDL:
            conn.execute(stmt)
        bridge_registry.initialize(conn, bridge.Bridge._upgrade_session_context, lambda _: None)
        conn.execute("INSERT INTO records (record_id, stream_id, position, author_peer_id, kind, targets_json, "
                     "metadata_json,idempotency_key,payload_digest,created_at,appended_at) "
                     "VALUES ('ask-id','s',1,'user','prompt','[\"cx\"]',?, 'ask:request','digest','1970-01-01T00:00:19Z','1970-01-01T00:00:19Z')",
                     (json.dumps({"runtime_kind": "cx", "binding": "cx.standard/gpt-6-luna/low"}),))
        yield conn


def test_old_bridge_read_and_additive_outcome_migration(old_bridge):
    from peerhub.cli.profile_stats import _ask_rows

    conn = old_bridge
    before = conn.execute("SELECT * FROM records").fetchone()
    assert next(iter(_ask_rows(conn, None)))["outcome"] == "unknown"
    for outcome in ("accepted", "retried", "rejected"):
        session_receipts.install_ask_metrics(conn)  # initialization is safe to repeat
        conn.execute("INSERT INTO bridge_ask_outcomes(record_id,outcome,marked_at) VALUES ('ask-id',?,'today')", (outcome,))
        assert next(iter(_ask_rows(conn, None)))["outcome"] == outcome
    assert conn.execute("SELECT * FROM records").fetchone() == before
    assert conn.execute("SELECT COUNT(*) FROM bridge_ask_outcomes").fetchone()[0] == 3
    with pytest.raises(sqlite3.IntegrityError, match="append only"):
        conn.execute("UPDATE bridge_ask_outcomes SET outcome='accepted'")


@pytest.mark.parametrize(("overlap", "reset", "expected"), [(False, 100, 20), (True, 100, None), (False, 200, None)])
def test_quota_writer_checks_other_invocations_and_reset_evidence(old_bridge, overlap, reset, expected):
    conn = old_bridge
    session_receipts.install_ask_metrics(conn)
    conn.execute("INSERT INTO bridge_session_history(session_handle,stream_id,peer_id,runtime_kind,external_session_id,"
                 "session_generation,adapter_fingerprint,binding,resumable,state,last_seen,created_at) "
                 "VALUES ('session','s','cx','cx','ext',1,'adapter:cx:v1','binding',1,'ACTIVE',10,10)")
    conn.execute("INSERT INTO bridge_deliveries(delivery_id,seq,stream_id,peer_id,record_id,record_position,attempt,certainty,"
                 "claim_generation,session_handle) VALUES ('d',1,'s','cx','ask-id',1,1,'NOT_STARTED',1,'session')")
    pool = "quota:cx:X-7D"

    def evidence(delivery, kind, detail):
        conn.execute("INSERT INTO bridge_evidence(delivery_id,stream_id,peer_id,kind,detail) VALUES (?,'s','cx',?,?)",
                     (delivery, kind, json.dumps(detail)))

    evidence("d", "about_to_invoke", {"invocation_no": 1, "quota_invoked_at": 15, "quota_pools": [pool]})
    evidence("d", "session_call", {"started_at": 19})
    if overlap:
        conn.execute("INSERT INTO bridge_deliveries(delivery_id,seq,stream_id,peer_id,record_id,record_position,attempt,certainty,"
                     "claim_generation,terminal_at) VALUES ('other',2,'other','cx','other-prompt',1,1,'NOT_STARTED',1,"
                     "'1970-01-01T00:00:25Z')")
        conn.execute("INSERT INTO bridge_invocations(delivery_id,invocation_no,stream_id,peer_id,claim_generation) "
                     "VALUES ('other',1,'other','cx',1)")
        evidence("other", "about_to_invoke", {"invocation_no": 1, "quota_invoked_at": 22, "quota_pools": [pool]})

    for observed, left, window in ((20, 0.8, 100), (30, 0.6, reset)):
        iso = datetime.fromtimestamp(observed, timezone.utc).isoformat()
        conn.execute("INSERT INTO observations(observation_id,subject_ref,resource_pool_ref,kind,source,state,payload_json,"
                     "observed_at,captured_at,effective_at_us) VALUES (?,'cx',?,'quota','test','MEASURED',?,?,?,?)",
                     (str(observed), pool, json.dumps(dict(remaining_fraction=left, window_started_at=1, resets_at=window)),
                      iso, iso, observed * 1_000_000))
    session_receipts.record_ask_pool_delta(conn, conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id='d'").fetchone(), 35)
    result = conn.execute("SELECT delta_pp,before_id,after_id FROM bridge_ask_pool_deltas").fetchone()
    if expected is None:
        assert result is None
    else:
        assert tuple(result) == (pytest.approx(expected), "20", "30")


class ScriptedCodex(FakeRuntimeTarget):
    runtime_kind = "cx"

    def __init__(self):
        super().__init__(fingerprint="adapter:cx:v1", binding="cx.standard/gpt-6-luna/low")

    def session_binding(self):
        return {"profile": "cx.standard", "model": "gpt-6-luna", "effort": "low", "writable": False}


def test_outcome_corrections_migrate_old_store_without_changing_ask(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "no-global"))
    db = tmp_path / "core.db"
    runtime = ScriptedCodex().script_deliver(("started", "first"), ("terminal", {"response": "ok"}))
    first = ask(db, "cx", "question", request_id="recorded", runtime=runtime)
    ask_id = first["requested_record_id"]
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE bridge_ask_outcomes")
        conn.execute("DROP TABLE bridge_ask_pool_deltas")
        old_version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert main(["--db", str(db), "profile", "stats", "--json"]) == 0
    row = next(row for row in json.loads(capsys.readouterr().out)["profiles"] if row["profile"] == "cx.standard")
    assert row["n"] == 1 and row["outcomes"] == {"unknown": 1, "accepted": 0, "rejected": 0, "retried": 0}
    for outcome in ("accepted", "retried", "rejected"):
        assert main(["--db", str(db), "outcome", ask_id, outcome]) == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == old_version
        assert conn.execute("SELECT outcome FROM bridge_ask_outcomes ORDER BY seq").fetchall() == [
            ("accepted",), ("retried",), ("rejected",)]
    assert ask(db, "cx", "question", request_id="recorded", runtime=ScriptedCodex())["response"] == "ok"
    capsys.readouterr()
    assert main(["--db", str(db), "profile", "stats", "--json"]) == 0
    row = next(row for row in json.loads(capsys.readouterr().out)["profiles"] if row["profile"] == "cx.standard")
    assert row["n"] == 1 and row["outcomes"]["rejected"] == 1


def test_stream_budget_warning_precedes_only_a_resumed_invocation(tmp_path, monkeypatch):
    from peerhub.extensions.adapters.telemetry import parse_provider_usage

    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "no-global"))
    db = tmp_path / "core.db"
    runtime = ScriptedCodex()
    usage = parse_provider_usage("cx", '{"type":"turn.completed","usage":{"input_tokens":128000,"cached_input_tokens":0}}')
    runtime.script_deliver(("started", "first"), ("terminal", {
        "response": "ok", "vendor_session": {"id": "native-first"}, "provider_telemetry": usage,
    }))
    events = []
    options = dict(db_path=db, peer_id="cx", stream_id="work", workspace=tmp_path, runtime=runtime,
                   on_stream_budget=lambda: events.append("warning"))
    assert ask(prompt="first", **options)["session"]["mode"] == "fresh"
    assert events == []
    runtime.script_deliver(("call", lambda: events.append("invoked")), ("started", "second"), ("terminal", {"response": "ok"}))
    assert ask(prompt="second", **options)["session"]["mode"] == "resumed"
    assert events == ["warning", "invoked"]
    assert ask(prompt="third", fresh=True, **options)["session"]["mode"] == "fresh"
    assert events == ["warning", "invoked"]


def test_profile_check_uses_catalog_evidence_and_measurement_age(monkeypatch, capsys):
    from peerhub.cli import profile_stats

    monkeypatch.setattr(profile_stats, "installed_models", lambda provider, workspace: {"gpt-6-luna"} if provider == "cx" else None)
    monkeypatch.setattr(profile_stats, "read_profile_stats", lambda db, workspace, profiles: [
        {"profile": p["profile"], "last_measured_at": "2001-01-01T00:00:00Z", "stale": False} for p in profiles
    ])
    assert main(["profile", "check", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert "cx.effort" in result["missing_models"]
    assert "cx.standard" not in result["missing_models"]
    assert "cc.standard" in result["measurement_overdue"]


def test_stats_marks_changed_bindings_and_reads_latest_benchmark(monkeypatch):
    from io import StringIO
    from pathlib import Path
    from peerhub.cli import profile_stats
    from peerhub.extensions.adapters.profiles import list_profiles

    workspace = Path.cwd()
    profile = next(p for p in list_profiles(workspace, include_meta=True) if p["profile"] == "cx.standard")
    monkeypatch.setattr(profile_stats, "ask_measurements", lambda db: iter([]))
    monkeypatch.setattr(Path, "glob", lambda self, suffix: [self / "results.jsonl"])
    monkeypatch.setattr(Path, "open", lambda self, **opts: StringIO(
        '{"profile":"cx.standard","model":"gpt-old","effort":"low","finished_at":"2026-11-18T00:00:00Z"}\n'
        '{"profile":"cx.standard","model":"gpt-6-luna","effort":"low","finished_at":"2026-11-17T00:00:00Z"}\n'
        '{'
    ))
    row, = profile_stats.read_profile_stats("unused.db", workspace, [profile])
    assert row["stale"] is True and row["n"] == 0
    assert row["last_benchmark_run"] == "2026-11-18T00:00:00Z"


def test_receipt_and_query_file_warning(monkeypatch, capsys):
    from pathlib import Path
    from peerhub.cli import profile_stats
    from peerhub.extensions import ask as ask_module

    query = b"a" * (64 * 1024 + 1)
    monkeypatch.setattr(Path, "read_bytes", lambda self: query)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("PEERHUB_NO_RECEIPT", raising=False)

    def fake_ask(db, peer, prompt, **options):
        assert prompt == query.decode()
        return {"status": "delivered", "response": "ok", "requested_record_id": "ask-id"}

    monkeypatch.setattr(ask_module, "ask", fake_ask)
    monkeypatch.setattr(profile_stats, "ask_measurements", lambda db, ask_id: iter([{
        "profile": "cx.standard", "wall_seconds": 0, "input_tokens": 0,
    }]))
    argv = ["--db", "unused.db", "ask", "cx", "--query-file", "query.txt"]
    assert main(argv) == 0
    stderr = capsys.readouterr().err.splitlines()
    assert len(stderr) == 2 and "paths or a diff" in stderr[0]
    assert stderr[1].startswith("ask-id - cx.standard - 0") and "tokens 0/-/-" in stderr[1]
    assert main(argv + ["--json"]) == 0
    assert len(capsys.readouterr().err.splitlines()) == 1
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert main(argv) == 0
    assert len(capsys.readouterr().err.splitlines()) == 1
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("PEERHUB_NO_RECEIPT", "1")
    assert main(argv) == 0
    assert len(capsys.readouterr().err.splitlines()) == 1


@pytest.mark.parametrize(("prompt_tokens", "turns", "warn"), [
    (128_000, 0, True), (127_999, 99, False), (0, 99, False),
    (None, 11, False), (None, 12, True),
])
def test_stream_budget_uses_reported_prompt_or_resumed_turns(prompt_tokens, turns, warn):
    assert session_receipts.stream_budget_exceeded(prompt_tokens, turns, (128_000, 12)) is warn
