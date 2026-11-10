"""`peerhub diag running`: lease-active deliveries from existing tables; read-only, no provider calls."""
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.extensions import bridge, bridge_claims
from peerhub.extensions.diag_running import MAX_ROWS, format_running, running_report
from tests.communication.harness.core import CoreHarness

pytestmark = [pytest.mark.integration, pytest.mark.diag]

NOW = 1_790_812_800.0
BINDING = "cx.standard/test-model/low#0123456789ab"


@pytest.fixture
def db(tmp_path):
    h = CoreHarness(tmp_path / "ws")
    with closing(sqlite3.connect(h.db_path)) as c:
        for stmt in bridge._DDL:  # pyright: ignore[reportPrivateUsage]
            c.execute(stmt)
        c.execute(bridge_claims._DDL)  # pyright: ignore[reportPrivateUsage]
        # Created by the session-receipts layer in real stores: one session_call evidence row per delivery.
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_be_session_call_once ON bridge_evidence(delivery_id) WHERE kind='session_call'")
        c.commit()
    return h.db_path


def add(db, n, *, certainty="STARTED", acked=0, claim_expires=NOW + 60.0, claim_generation=1, delivery_generation=1,
        stream=None, peer="cx", request_id=None, binding=BINDING, metadata=None, start="valid", evidence_rows=None,
        workspace_generation=None, with_record=True):
    """One delivery with its record, claim and (optionally) session_call evidence."""
    stream = stream or f"s{n}"
    request_id = request_id or f"req-{n}"
    meta = json.dumps({"binding": binding} if metadata is None else metadata)
    generation = workspace_generation or Path(db).parent.joinpath("workspace.generation").read_text(encoding="utf-8")
    with closing(sqlite3.connect(db)) as c:
        if with_record:
            c.execute("INSERT INTO records (record_id,stream_id,position,author_peer_id,kind,metadata_json,idempotency_key,"
                      "payload_digest,created_at,appended_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (f"r{n}", stream, 1, "user", "prompt", meta, f"ask:{request_id}", "digest",
                       "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"))
        c.execute("INSERT OR REPLACE INTO bridge_claims (stream_id,peer_id,owner_id,generation,expires_at,lease_sec,workspace_generation) "
                  "VALUES (?,?,?,?,?,?,?)", (stream, peer, "owner", claim_generation, claim_expires, 60.0, generation))
        c.execute("INSERT INTO bridge_deliveries (delivery_id,seq,stream_id,peer_id,record_id,record_position,attempt,certainty,"
                  "claim_generation,acked) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (f"d{n}", n, stream, peer, f"r{n}", 1, 1, "NOT_STARTED", delivery_generation, acked))
        for step in {"NOT_STARTED": [], "MAY_HAVE_STARTED": ["MAY_HAVE_STARTED"], "STARTED": ["STARTED"],
                     "TERMINAL": ["STARTED", "TERMINAL"]}[certainty]:  # certainty is forward-only (DB trigger)
            c.execute("UPDATE bridge_deliveries SET certainty=? WHERE delivery_id=?", (step, f"d{n}"))
        rows = evidence_rows
        if rows is None and start == "valid":
            rows = [("session_call", "STARTED", json.dumps({"started_at": NOW - 30.0}))]
        elif rows is None and start == "malformed":
            rows = [("session_call", "STARTED", "not json")]
        for kind, cert, detail in rows or []:
            c.execute("INSERT INTO bridge_evidence (delivery_id,stream_id,peer_id,kind,certainty,detail) VALUES (?,?,?,?,?,?)",
                      (f"d{n}", stream, peer, kind, cert, detail))
        c.commit()


def report(db, **kw):
    return running_report(db, read_at=NOW, **kw)


def test_started_delivery_row_fields(db):
    add(db, 1)
    rep = report(db)
    assert rep["status"] == "OK" and rep["truncated"] is False
    assert rep["rows"] == [{
        "peer_id": "cx", "profile": "cx.standard", "binding": BINDING, "stream_id": "s1", "request_id": "req-1",
        "delivery_id": "d1", "certainty": "STARTED", "started_at": NOW - 30.0, "elapsed_s": 30.0,
        "lease_expires_in_s": 60.0}]


def test_expired_claim_is_excluded(db):
    add(db, 1, claim_expires=NOW - 1.0)
    add(db, 2, claim_expires=NOW)  # lease must be strictly in the future
    add(db, 3)
    assert [r["delivery_id"] for r in report(db)["rows"]] == ["d3"]


def test_claim_generation_mismatch_is_excluded(db):
    add(db, 1, claim_generation=2, delivery_generation=1)
    assert report(db)["rows"] == []


def test_terminal_and_acked_are_excluded(db):
    add(db, 1, certainty="TERMINAL")
    add(db, 2, certainty="MAY_HAVE_STARTED")
    add(db, 3, acked=1)
    add(db, 4)
    assert [r["delivery_id"] for r in report(db)["rows"]] == ["d4"]


def test_not_started_and_started_are_distinguished(db):
    add(db, 1, certainty="NOT_STARTED", start="none")
    add(db, 2, certainty="STARTED")
    rows = {r["delivery_id"]: r for r in report(db)["rows"]}
    assert rows["d1"]["certainty"] == "NOT_STARTED" and rows["d1"]["started_at"] is None and rows["d1"]["elapsed_s"] is None
    assert rows["d2"]["certainty"] == "STARTED" and rows["d2"]["elapsed_s"] == 30.0


@pytest.mark.parametrize("start", ["none", "malformed"])
def test_missing_or_invalid_start_evidence_yields_null_timing(db, start):
    add(db, 1, start=start)
    row = report(db)["rows"][0]
    assert row["certainty"] == "STARTED" and row["started_at"] is None and row["elapsed_s"] is None
    assert row["lease_expires_in_s"] == 60.0


@pytest.mark.parametrize("detail", ['{"started_at": "soon"}', '{"started_at": true}', '{"started_at": NaN}', '[1]', '{}'])
def test_non_numeric_started_at_is_not_valid_evidence(db, detail):
    add(db, 1, evidence_rows=[("session_call", "STARTED", detail)])
    row = report(db)["rows"][0]
    assert row["started_at"] is None and row["elapsed_s"] is None


def test_other_evidence_kinds_do_not_count_as_start(db):
    add(db, 1, evidence_rows=[("session_telemetry", "STARTED", json.dumps({"started_at": NOW - 5})),
                              ("session_call", "MAY_HAVE_STARTED", json.dumps({"started_at": NOW - 5}))])
    assert report(db)["rows"][0]["started_at"] is None


def test_several_evidence_kinds_per_delivery_yield_one_row(db):
    add(db, 1, evidence_rows=[("session_telemetry", "STARTED", json.dumps({"started_at": NOW - 99.0})),
                              ("session_call", "STARTED", json.dumps({"started_at": NOW - 10.0})),
                              ("terminal", "TERMINAL", "{}")])
    rows = report(db)["rows"]
    assert len(rows) == 1 and rows[0]["started_at"] == NOW - 10.0 and rows[0]["elapsed_s"] == 10.0


def test_the_real_schema_forbids_a_second_session_call_per_delivery(db):
    add(db, 1)
    with closing(sqlite3.connect(db)) as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO bridge_evidence (delivery_id,stream_id,peer_id,kind,certainty,detail) VALUES ('d1','s1','cx','session_call','STARTED','{}')")


def test_defensive_latest_started_wins_even_without_the_unique_index(db):
    """Not reachable in a real store (see above); proves the query never fans out if the index were ever missing."""
    with closing(sqlite3.connect(db)) as c:
        c.execute("DROP INDEX idx_be_session_call_once")
        c.commit()
    add(db, 1, evidence_rows=[("session_call", "STARTED", json.dumps({"started_at": NOW - 90.0})),
                              ("session_call", "STARTED", json.dumps({"started_at": NOW - 10.0}))])
    rows = report(db)["rows"]
    assert len(rows) == 1 and rows[0]["started_at"] == NOW - 10.0


def test_future_started_at_clamps_elapsed_to_zero(db):
    add(db, 1, evidence_rows=[("session_call", "STARTED", json.dumps({"started_at": NOW + 5.0}))])
    assert report(db)["rows"][0]["elapsed_s"] == 0.0


def test_binding_and_request_id_derivation(db):
    add(db, 1, metadata={"other": 1})
    add(db, 2, peer="cc", binding="cc.deep/x/high")
    rows = {r["delivery_id"]: r for r in report(db)["rows"]}
    assert rows["d1"]["binding"] is None and rows["d1"]["profile"] is None
    assert rows["d2"]["profile"] == "cc.deep" and rows["d2"]["binding"] == "cc.deep/x/high"
    assert rows["d2"]["request_id"] == "req-2"


@pytest.mark.parametrize("binding", ["test-model/low#0123456789ab", "default/default", "test-model/low", "a/b/c/d#0123456789ab",
                                     "gpt-5.1/high#0123456789ab", "nodot/m/e#0123456789ab", ".lead/m/e", "x./m/e"])
def test_profile_less_or_malformed_bindings_have_no_profile(db, binding):
    """A profile-less ask binds `model/effort#hash`: the model must never be shown as the profile."""
    add(db, 1, binding=binding)
    row = report(db)["rows"][0]
    assert row["profile"] is None and row["binding"] == binding


@pytest.mark.parametrize("peer, binding, profile", [
    ("cc-work", "cc.standard/opus/high#0123456789ab", "cc.standard"),   # an account-bound / custom peer uses a builtin provider's profile
    ("reviewer", "cx.deep-think/gpt-5/xhigh", "cx.deep-think"),
    ("claude", "cc.effort/default/default#abcdefabcdef", "cc.effort"),  # provider alias peer
    ("agy", "ag.standard/gemini-pro/default", "ag.standard"),
])
def test_profile_comes_from_the_binding_not_from_the_peer_id(db, peer, binding, profile):
    add(db, 1, peer=peer, binding=binding)
    assert report(db)["rows"][0]["profile"] == profile


def test_missing_prompt_record_gives_null_request_id_and_profile(db):
    add(db, 1, with_record=False)
    row = report(db)["rows"][0]
    assert row["request_id"] is None and row["profile"] is None and row["binding"] is None and row["delivery_id"] == "d1"


def test_claim_from_another_workspace_generation_is_not_lease_active(db):
    add(db, 1, workspace_generation="a-restored-lineage")  # a restore replaced the lineage: bridge_claims treats this claim as not live
    add(db, 2)
    assert [r["delivery_id"] for r in report(db)["rows"]] == ["d2"]


def test_truncation_at_twenty_one(db):
    for n in range(1, MAX_ROWS + 2):
        add(db, n)
    rep = report(db)
    assert MAX_ROWS == 20 and len(rep["rows"]) == 20 and rep["truncated"] is True
    assert [r["delivery_id"] for r in rep["rows"]] == [f"d{n}" for n in range(1, 21)]


def test_exactly_twenty_is_not_truncated(db):
    for n in range(1, MAX_ROWS + 1):
        add(db, n)
    rep = report(db)
    assert len(rep["rows"]) == 20 and rep["truncated"] is False


def test_report_has_no_progress_or_staleness_fields(db):
    add(db, 1)
    rep = report(db)
    assert set(rep["rows"][0]) == {"peer_id", "profile", "binding", "stream_id", "request_id", "delivery_id", "certainty",
                                   "started_at", "elapsed_s", "lease_expires_in_s"}
    assert set(rep) == {"schema_version", "status", "read_at", "rows", "truncated", "error", "basis"}


def test_missing_bridge_tables_is_empty_not_an_error(tmp_path):
    h = CoreHarness(tmp_path / "bare")
    rep = report(h.db_path)
    assert rep["status"] == "OK" and rep["rows"] == [] and rep["truncated"] is False


def test_missing_database_is_unavailable_and_not_created(tmp_path):
    path = tmp_path / "nope" / "core.db"
    rep = report(path)
    assert rep["status"] == "UNAVAILABLE" and rep["rows"] == []
    assert not path.exists() and not path.parent.exists()


def test_human_output_header_states_lease_not_liveness(db):
    add(db, 1)
    add(db, 2, certainty="NOT_STARTED", start="none")
    text = format_running(report(db))
    head = text.splitlines()[0].lower()
    assert "lease" in head and "not proof" in head
    assert "d1" in text and "d2" in text and "STARTED" in text and "NOT_STARTED" in text
    assert "30s" in text


def test_human_output_shows_the_recorded_start_time_or_an_unknown_marker(db):
    add(db, 1)  # started NOW - 30
    add(db, 2, start="none")
    add(db, 3, certainty="NOT_STARTED", start="none")
    rep = report(db)
    lines = format_running(rep).splitlines()
    header = next(line for line in lines if line.startswith("PEER"))
    assert "STARTED" in header.split()  # a column of its own, besides the CERTAINTY value
    cell = {line.split()[4]: line for line in lines if line.split() and line.split()[0] == "cx"}  # DELIVERY column -> row
    expected = datetime.fromtimestamp(NOW - 30.0, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    assert expected in cell["d1"] and "30s" in cell["d1"]
    for delivery in ("d2", "d3"):
        assert "?" in cell[delivery] and expected not in cell[delivery]
    by_id = {r["delivery_id"]: r for r in rep["rows"]}  # JSON parity: the same instant, as epoch seconds
    assert by_id["d1"]["started_at"] == NOW - 30.0 and by_id["d2"]["started_at"] is None and by_id["d3"]["started_at"] is None


def test_human_output_none_when_empty(db):
    text = format_running(report(db))
    assert text.splitlines()[-1] == "(none)"


def test_human_output_marks_truncation(db):
    for n in range(1, MAX_ROWS + 2):
        add(db, n)
    assert "more" in format_running(report(db)).splitlines()[-1]


# ---- query cost -----------------------------------------------------------------------------

def seed_expired_history(db, scopes, per_scope, tag):
    """`scopes` finished scopes (expired claim, acked deliveries), `per_scope` deliveries each; one live delivery besides."""
    generation = Path(db).parent.joinpath("workspace.generation").read_text(encoding="utf-8")
    with closing(sqlite3.connect(db)) as c:
        c.executemany("INSERT INTO bridge_claims (stream_id,peer_id,owner_id,generation,expires_at,lease_sec,workspace_generation) VALUES (?,?,?,?,?,?,?)",
                      [(f"{tag}{s}", "cx", "o", 1, NOW - 100.0, 60.0, generation) for s in range(scopes)])
        c.executemany("INSERT INTO bridge_deliveries (delivery_id,seq,stream_id,peer_id,record_id,record_position,attempt,certainty,claim_generation,acked) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)",
                      [(f"{tag}d{s}-{i}", s * per_scope + i + 100, f"{tag}{s}", "cx", f"{tag}r{s}-{i}", i, 1, "NOT_STARTED", 1, 1)
                       for s in range(scopes) for i in range(per_scope)])
        c.commit()


def run_traced(db):
    """Run the real reader and return (the SELECT it executed, SQLite VM instructions that SELECT took)."""
    from peerhub.extensions.diag import ReadonlyDiag
    statements, steps = [], [0]

    def factory(path):
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.set_trace_callback(statements.append)
        conn.set_progress_handler(lambda: steps.__setitem__(0, steps[0] + 1) or 0, 1)
        return conn

    assert ReadonlyDiag(db, connection_factory=factory).running(read_at=NOW, limit=20)["status"] == "OK"
    select = next(s for s in statements if "FROM bridge_claims c" in s)
    return select, steps[0]


def test_running_query_is_driven_from_claims_and_uses_delivery_indexes(db):
    add(db, 1)
    select, _ = run_traced(db)
    with closing(sqlite3.connect(db)) as c:
        plan = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + select)]
    assert not any(step.startswith("SCAN d") or step.startswith("SCAN bridge_deliveries") for step in plan), plan
    assert any("SEARCH d" in step and "USING" in step and "INDEX" in step for step in plan), plan


def running_query_steps(db):
    return run_traced(db)[1]


def test_delivery_history_in_finished_scopes_does_not_add_query_work(tmp_path):
    small = CoreHarness(tmp_path / "small").db_path
    big = CoreHarness(tmp_path / "big").db_path
    for path in (small, big):
        with closing(sqlite3.connect(path)) as c:
            for stmt in bridge._DDL:  # pyright: ignore[reportPrivateUsage]
                c.execute(stmt)
            c.execute(bridge_claims._DDL)  # pyright: ignore[reportPrivateUsage]
            c.commit()
    seed_expired_history(small, scopes=150, per_scope=1, tag="h")
    seed_expired_history(big, scopes=150, per_scope=60, tag="h")  # 9000 finished deliveries instead of 150
    add(small, 1)
    add(big, 1)
    assert [r["delivery_id"] for r in report(big)["rows"]] == ["d1"]
    assert running_query_steps(big) <= running_query_steps(small) * 1.3, "history of finished scopes must not be read"


# ---- read-only guarantees -------------------------------------------------------------------

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_running_report_is_read_only(db, monkeypatch):
    add(db, 1)
    before = sha(db)
    calls = []
    monkeypatch.setattr("peerhub.extensions.observation.ObservationStore.capture", lambda *a, **k: calls.append("capture"))
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: calls.append("spawn"))
    report(db)
    assert sha(db) == before and calls == []
    with closing(sqlite3.connect(db)) as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] != "memory"


# ---- CLI -----------------------------------------------------------------------------------

def run(capsys, db, *args):
    code = main(["--db", str(db), "diag", "running", *args])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def seed_live(db):
    """Rows relative to the real clock for the CLI (which reads time.time())."""
    now = time.time()
    add(db, 1, claim_expires=now + 120.0,
        evidence_rows=[("session_call", "STARTED", json.dumps({"started_at": now - 40.0}))])


def test_cli_json(db, capsys):
    seed_live(db)
    code, out, _ = run(capsys, db, "--json")
    rep = json.loads(out)
    assert code == 0 and rep["status"] == "OK" and rep["truncated"] is False
    row = rep["rows"][0]
    assert row["delivery_id"] == "d1" and row["request_id"] == "req-1" and row["certainty"] == "STARTED"
    assert 39 < row["elapsed_s"] < 60 and 100 < row["lease_expires_in_s"] <= 120


def test_cli_human_table(db, capsys):
    seed_live(db)
    code, out, _ = run(capsys, db)
    lines = out.splitlines()
    assert code == 0 and "lease" in lines[0].lower() and "not proof" in lines[0].lower()
    assert "d1" in out and "req-1" in out and "cx.standard" in out


def test_cli_human_none(db, capsys):
    code, out, _ = run(capsys, db)
    assert code == 0 and out.strip().splitlines()[-1] == "(none)"


def test_cli_json_empty(db, capsys):
    code, out, _ = run(capsys, db, "--json")
    assert code == 0 and json.loads(out)["rows"] == []


def test_cli_is_read_only(db, capsys):
    seed_live(db)
    before = sha(db)
    run(capsys, db)
    run(capsys, db, "--json")
    assert sha(db) == before


def test_cli_missing_store_exits_5_and_creates_nothing(tmp_path, capsys):
    path = tmp_path / "missing" / "core.db"
    code, _, _ = run(capsys, path)
    assert code == 5 and not path.parent.exists()


def test_cli_storage_fault_exits_4_with_the_error_once_on_stderr(tmp_path, capsys):
    path = tmp_path / "ws" / "core.db"
    path.parent.mkdir()
    path.write_bytes(b"this is not a sqlite database" * 20)
    code, out, err = run(capsys, path)
    assert code == 4
    assert err.count("[diag_unavailable]") == 1 and "next:" in err
    assert "Lease-active deliveries" in out and "(none)" not in out  # no false "nothing running" on a failed read
    assert "DatabaseError" not in out  # the error text is on stderr only
    code, out, err = run(capsys, path, "--json")
    assert code == 4 and json.loads(out)["status"] == "FAILED" and json.loads(out)["error"]


def test_cli_future_schema_exits_6(db, capsys):
    with closing(sqlite3.connect(db)) as c:
        c.execute("PRAGMA user_version = 99999")
        c.commit()
    code, out, err = run(capsys, db)
    assert code == 6 and err.count("[diag_unavailable]") == 1 and "SchemaVersionError" not in out and "(none)" not in out
    code, out, _ = run(capsys, db, "--json")
    assert code == 6 and json.loads(out)["status"] == "SCHEMA_VERSION"


def test_unreadable_generation_file_is_a_failed_report_not_a_traceback(db, capsys, monkeypatch):
    add(db, 1)
    real = Path.read_text

    def read_text(self, *a, **kw):
        if self.name == "workspace.generation":
            raise PermissionError("denied")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", read_text)
    rep = report(db)
    assert rep["status"] == "FAILED" and rep["rows"] == [] and "PermissionError" in rep["error"]
    code, out, err = run(capsys, db)
    assert code == 4 and err.count("[diag_unavailable]") == 1 and "(none)" not in out


def test_without_a_generation_file_every_generation_matches(db):
    add(db, 1, workspace_generation="anything")
    db.parent.joinpath("workspace.generation").unlink()
    assert [r["delivery_id"] for r in report(db)["rows"]] == ["d1"]


def test_cli_help_says_lease_not_liveness(capsys):
    with pytest.raises(SystemExit):
        main(["diag", "running", "--help"])
    out = " ".join(capsys.readouterr().out.lower().split())  # argparse wraps lines
    assert "lease" in out and "not proof" in out and "read-only" in out
