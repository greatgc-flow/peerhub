"""Cached readiness: evidence attribution, conservative states, and read-only CLI."""
import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing

import pytest

from peerhub.cli.app import build_parser, main
from peerhub.extensions.diag import ReadonlyDiag
from peerhub.extensions.diag_readiness import configured_profiles, format_readiness, readiness_report
from tests.communication.harness.observation import ObservationHarness, T0


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "global"))
    h = ObservationHarness(tmp_path / "ws" / ".peerhub")
    workspace = h.db_path.parent.parent
    config = workspace / ".peerhub" / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "models.toml").write_text('''schema_version = 1
[profiles."cx.standard"]
model = "test-model"
reasoning_effort = "low"
pools = ["P"]
pools_source = "fixture measurement"
[profiles."cx.effort"]
model = "test-model"
reasoning_effort = "medium"
pools = ["P"]
pools_source = "fixture measurement"
''', encoding="utf-8")
    with closing(sqlite3.connect(h.db_path)) as c:
        for name, in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name IN ('observations_no_update','observations_no_delete')").fetchall():
            c.execute(f'DROP TRIGGER "{name}"')
        c.executescript('''
        CREATE TABLE bridge_deliveries (delivery_id TEXT, seq INTEGER, stream_id TEXT,
          peer_id TEXT, record_id TEXT, attempt INTEGER, certainty TEXT, result_json TEXT, terminal_at TEXT);
        CREATE TABLE bridge_evidence (seq INTEGER PRIMARY KEY, delivery_id TEXT, kind TEXT, detail TEXT);
        INSERT INTO resource_pools VALUES ('quota:cx:P','cx','QUOTA','{}');
        ''')
        c.execute("INSERT INTO observations (observation_id,subject_ref,resource_pool_ref,kind,source,state,payload_json,observed_at,captured_at,effective_at_us) VALUES ('o','cx','quota:cx:P','quota','fixture','MEASURED',?,'2026-10-01T00:00:00Z','2026-10-01T00:00:00Z',?)",
                  (json.dumps({"remaining_fraction": .5, "resets_at": T0 + 3600}), int(T0 * 1e6)))
        c.commit()
    return h, workspace


def delivery(store, *, profile="cx.standard", certainty="TERMINAL", result='{"response":""}',
             kind="terminal", detail="{}", binding=None):
    h, workspace = store
    expected = next(p["binding"] for p in configured_profiles(workspace) if p["id"] == profile)
    with closing(sqlite3.connect(h.db_path)) as c:
        seq = c.execute("SELECT COUNT(*)+1 FROM bridge_deliveries").fetchone()[0]
        c.execute("INSERT INTO records (record_id,stream_id,position,author_peer_id,kind,metadata_json,idempotency_key,payload_digest,created_at,appended_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (f"r{seq}", f"s{seq}", 1, "cx", "prompt", json.dumps({"binding": binding or expected}),
                   str(seq), "digest", "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"))
        c.execute("INSERT INTO bridge_deliveries VALUES (?,?,?,?,?,?,?,?,?)",
                  (f"d{seq}", seq, f"s{seq}", "cx", f"r{seq}", 1, certainty, result,
                   "2026-10-01T00:00:00Z" if certainty == "TERMINAL" else None))
        c.execute("INSERT INTO bridge_evidence VALUES (?,?,?,?)", (seq, f"d{seq}", kind, detail))
        c.commit()


def report(store, **kw):
    return readiness_report(store[0].db_path, reader=ReadonlyDiag(store[0].db_path, **kw), read_at=T0 + 12)


def profile(rep, name="cx.standard"):
    return next(p for p in rep["profiles"] if p["id"] == name)


def test_no_delivery_never_ready_and_terminal_empty_response(store):
    assert profile(report(store))["state"] == "UNVERIFIED"
    delivery(store)
    p = profile(report(store))
    assert p["state"] == "READY" and p["last"] == {
        "outcome": "terminal", "age": 12, "error_class": "UNKNOWN", "evidence": "delivery:d1"}
    assert "actual_model" not in p


def test_shared_pool_exhaustion_and_unknown_delivery_cause(store):
    delivery(store, certainty="STARTED", result=None, kind="runtime_error")
    with closing(sqlite3.connect(store[0].db_path)) as c:
        c.execute('UPDATE observations SET payload_json=\'{"remaining_fraction":0}\'')
        c.commit()
    rep = report(store)
    assert list(rep["pools"]).count("quota:cx:P") == 1
    assert [profile(rep, n)["state"] for n in ("cx.standard", "cx.effort")] == ["EXHAUSTED"] * 2
    assert profile(rep)["last"]["error_class"] == "UNKNOWN"
    assert rep["pools"]["quota:cx:P"]["reset_in"] is None
    assert profile(rep, "cx.effort")["action"] == "inspect_facts"


@pytest.mark.parametrize("cause,expected", [("quota_exhausted", "rate_limit"), ("auth_failure", "auth_failure"),
                                           ("invalid_model", "invalid_model"), ("other", "UNKNOWN")])
def test_explicit_cause_has_evidence(store, cause, expected):
    delivery(store, certainty="STARTED", result=None, kind="refusal_detected", detail=json.dumps({"reason": cause}))
    p = profile(report(store))
    assert p["state"] == "UNVERIFIED" and p["last"]["error_class"] == expected
    assert p["last"]["evidence"] == "delivery:d1"
    assert p["action"] == "ask_refusal"


@pytest.mark.parametrize("certainty,kind,result,outcome", [
    ("STARTED", "runtime_timeout", None, "uncertain"),
    ("MAY_HAVE_STARTED", "runtime_error", None, "uncertain"),
    ("NOT_STARTED", "runtime_error", None, "failed"),
])
def test_newer_error_hides_success(store, certainty, kind, result, outcome):
    delivery(store)
    delivery(store, certainty=certainty, kind=kind, result=result)
    p = profile(report(store))
    assert p["state"] == "UNVERIFIED" and p["last"]["outcome"] == outcome
    assert p["last"]["evidence"] == "delivery:d2"


def test_binding_changed_and_later_success(store):
    delivery(store, binding="cx.standard/old-model/low#old")
    assert profile(report(store))["last"]["outcome"] == "binding_changed"
    assert profile(report(store))["state"] == "UNVERIFIED"
    delivery(store)
    assert profile(report(store))["state"] == "READY"


@pytest.mark.parametrize("model,effort,outcome,state", [
    ("test-model", "medium", "terminal", "READY"),
    ("old-model", "medium", "binding_changed", "UNVERIFIED"),
    ("test-model", "high", "binding_changed", "UNVERIFIED"),
])
def test_native_recorded_binding_on_copied_store(store, tmp_path, model, effort, outcome, state):
    # Native Records retain this binding string; the immutable delivery snapshot
    # contains a handle and telemetry, not the configured binding fields.
    delivery(store, profile="cx.effort", binding=f"cx.effort/{model}/{effort}#610f3a58af36",
             result=json.dumps({"response": "", "empty_response": True,
                                "task_effects": "unverified", "session": {
                                    "handle": "session-original", "generation": 1, "mode": "fresh",
                                    "prior_handle": None, "reason": "no_prior", "telemetry": {}}}))
    with closing(sqlite3.connect(store[0].db_path)) as c:
        c.execute("ALTER TABLE bridge_deliveries ADD COLUMN session_snapshot_json TEXT")
        c.execute("UPDATE bridge_deliveries SET session_snapshot_json=?", (json.dumps({
            "handle": "session-original", "generation": 1, "mode": "fresh",
            "prior_handle": None, "reason": "no_prior", "telemetry": {}}),))
        c.commit()
        copied = tmp_path / "copied.db"
        with closing(sqlite3.connect(copied)) as dst:
            c.backup(dst)
    # Put the same effective profile in the inspection workspace, which differs
    # from the original provider cwd and from the copy's directory.
    snapshot = ReadonlyDiag(copied).readiness(["cx.effort"], read_at=T0 + 12)
    from peerhub.extensions.diag_readiness import compose
    rep = compose(configured_profiles(store[1]), snapshot["observations"], snapshot["deliveries"],
                  snapshot["read_at"], complete=snapshot["complete"])
    p = profile(rep, "cx.effort")
    assert p["last"]["outcome"] == outcome and p["state"] == state


@pytest.mark.parametrize("age,expected", [(299, "MEASURED"), (300, "STALE"), (900, "STALE")])
def test_freshness_matches_monitor_at_same_read_at(store, age, expected):
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    now = T0 + age
    ready = readiness_report(store[0].db_path, read_at=now)
    monitor = render_monitor_frame(str(store[0].db_path), ["capacity"], read_at=now)
    monitor_pool = next(o for o in monitor["groups"]["capacity"]["data"]["observations"]
                        if o["pool_ref"] == "quota:cx:P")
    assert ready["pools"]["quota:cx:P"]["state"] == monitor_pool["state"] == expected


@pytest.mark.parametrize("mutation", ["DELETE FROM observations", "UPDATE observations SET effective_at_us=1,observed_at='1970-01-01T00:00:00Z',captured_at='1970-01-01T00:00:00Z'",
                                    "UPDATE observations SET payload_json='broken'", "DROP TABLE bridge_evidence"])
def test_missing_stale_corrupt_optional_evidence_never_ready(store, mutation):
    delivery(store)
    with closing(sqlite3.connect(store[0].db_path)) as c:
        c.execute(mutation)
        c.commit()
    assert profile(report(store))["state"] == "UNKNOWN"


def test_exact_model_mapping_overrides_profile_pools(store):
    config = store[1] / ".peerhub/config/models.toml"
    with config.open("a", encoding="utf-8", newline="\n") as f:
        f.write('\n[exact_model_pools."test-model"]\npools=["Q"]\npools_source="measured"\n')
    p = profile(report(store))
    assert p["pools"] == ["quota:cx:Q"] and p["state"] == "UNKNOWN"


def test_override_does_not_inherit_verified_membership(store):
    config = store[1] / ".peerhub/config/models.toml"
    config.write_text('schema_version=1\n[profiles."cx.standard"]\nmodel="new-model"\n', encoding="utf-8")
    assert profile(report(store))["mapping"] == "unverified"


def test_ag_success_cannot_waive_writable_consent(store):
    config = store[1] / ".peerhub/config/models.toml"
    with config.open("a", encoding="utf-8", newline="\n") as f:
        f.write('\n[profiles."ag.standard"]\npools=["quota:cx:P"]\npools_source="measured"\nmodel="test-model"\n')
    delivery(store, profile="ag.standard")
    p = profile(report(store), "ag.standard")
    assert p["state"] == "UNVERIFIED" and p["policy"] == "requires_writable_consent"


def test_readonly_determinism_and_batch_query_bound(store, monkeypatch):
    delivery(store)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("provider spawn"))
    db = store[0].db_path
    before = hashlib.sha256(db.read_bytes()).digest()
    statements = []
    def factory(path):
        c = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.set_trace_callback(statements.append)
        return c
    first = report(store, connection_factory=factory)
    assert first == report(store)
    assert before == hashlib.sha256(db.read_bytes()).digest()
    assert len(statements) <= 36  # Includes the existing per-table root integrity probes.
    assert sum("FROM bridge_deliveries d" in s for s in statements) == 1
    assert "coverage" not in first


def test_cli_actions_parser_overflow_and_no_probe(store, capsys, monkeypatch):
    monkeypatch.setattr("peerhub.extensions.diag.time.time", lambda: T0 + 12)
    delivery(store, certainty="STARTED", result=None, kind="runtime_timeout")
    assert main(["--db", str(store[0].db_path), "diag", "readiness", "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    for p in rep["profiles"]:
        if p["next"]:
            build_parser().parse_args(p["next"]["argv"][1:])
    assert profile(rep)["next"]["argv"][-2:] == ["--stream", "s1"]
    text = format_readiness(rep)
    assert "PROFILE POOLS STATE LAST NEXT" in text
    # Ordinary nine-profile output stays compact; JSON has no size promise.
    assert len(text.split()) < 200
    rep["profiles"].extend([rep["profiles"][0]] * 3)
    assert "+3 profiles, see --json" in format_readiness(rep)
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(store[0].db_path), "diag", "readiness", "--probe"])
    assert exc.value.code == 2


def test_missing_store_never_created(tmp_path, capsys):
    db = tmp_path / "missing/core.db"
    assert main(["--db", str(db), "diag", "readiness", "--json"]) == 5
    assert json.loads(capsys.readouterr().out)["status"] == "UNAVAILABLE"
    assert not db.parent.exists()


@pytest.mark.parametrize("result,detail,expected,state", [
    ('{"error":{"message":"rate_limit_error"}}', "{}", "rate_limit", "UNVERIFIED"),
    ('{"response":"rate_limit_error"}', "{}", "UNKNOWN", "READY"),
    ('{"error":{"message":"unrecognized failure"}}', "{}", "UNKNOWN", "UNVERIFIED"),
    ('{"response":"ok"}', "broken", "UNKNOWN", "UNKNOWN"),
    ('broken', "{}", "UNKNOWN", "UNKNOWN"),
])
def test_error_fields_only_and_corrupt_evidence(store, result, detail, expected, state):
    delivery(store, result=result, detail=detail)
    p = profile(report(store))
    assert p["state"] == state and p["last"]["error_class"] == expected


def test_profile_outcome_not_hidden_by_monitor_sample_cap(store):
    delivery(store, certainty="STARTED", result=None, kind="runtime_timeout")
    for _ in range(55):
        delivery(store, profile="cx.effort")
    rep = report(store)
    assert profile(rep)["last"]["outcome"] == "uncertain"
    assert profile(rep, "cx.effort")["state"] == "READY"


def test_runtime_cause_does_not_turn_uncertainty_into_refusal(store):
    delivery(store, certainty="STARTED", result=None, kind="runtime_error",
             detail='{"error":"rate_limit_error"}')
    p = profile(report(store))
    assert p["last"]["error_class"] == "rate_limit"
    assert p["last"]["outcome"] == "uncertain" and p["action"] == "ask_uncertain"


def test_real_bridge_terminal_binding(store):
    from peerhub.extensions.ask import ask
    from tests.communication.fakes import FakeRuntimeTarget
    h, workspace = store
    with closing(sqlite3.connect(h.db_path)) as c:
        c.executescript("DROP TABLE bridge_deliveries; DROP TABLE bridge_evidence;")
    binding = next(p["binding"] for p in configured_profiles(workspace) if p["id"] == "cx.standard")
    runtime = FakeRuntimeTarget(binding=binding)
    runtime.script_deliver(("started", "exec-1"), ("terminal", {"response": ""}))
    assert ask(h.db_path, "cx", "test", request_id="test", runtime=runtime)["status"] == "delivered"
    # Use the stored terminal time so evidence age is meaningful even outside fixture time.
    assert profile(readiness_report(h.db_path))["last"]["outcome"] == "terminal"


def test_schema_storage_exits_and_builtin_ag_pool_separation(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "global"))
    profiles = configured_profiles(tmp_path)
    assert next(p for p in profiles if p["id"] == "ag.standard")["pools"] == ["quota:ag:G-5H", "quota:ag:G-7D"]
    assert next(p for p in profiles if p["id"] == "ag.deepthink")["pools"] == ["quota:ag:3P-5H", "quota:ag:3P-7D"]
    db = tmp_path / "future.db"
    with closing(sqlite3.connect(db)) as c:
        c.execute("PRAGMA user_version=999")
    before = db.read_bytes()
    assert main(["--db", str(db), "diag", "readiness", "--json"]) == 6
    assert json.loads(capsys.readouterr().out)["status"] == "SCHEMA_VERSION"
    assert db.read_bytes() == before
    db.write_bytes(b"not sqlite")
    assert main(["--db", str(db), "diag", "readiness", "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["status"] == "FAILED"
