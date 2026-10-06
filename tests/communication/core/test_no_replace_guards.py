"""Pragma-independent INSERT OR REPLACE guards on every immutable table (final hardening).

A raw sqlite3 connection (recursive_triggers explicitly OFF), as a script or the sqlite shell would use, must not be able to
overwrite an immutable row via REPLACE (whose implicit DELETE fires delete triggers only under recursive_triggers=ON).
"""
import json
import sqlite3
from contextlib import closing

import pytest

from peerhub.extensions.observation import ObservationStore
from peerhub.core.migrations import MIGRATIONS, run_migrations


def raw(db):
    c = sqlite3.connect(str(db), isolation_level=None)
    c.execute("PRAGMA recursive_triggers = OFF")
    assert c.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
    return c


def digest(db):
    with closing(raw(db)) as c:
        names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: c.execute(f'SELECT * FROM "{n}" ORDER BY rowid').fetchall() for n in names}


def ins(c, table, row, verb="INSERT"):
    cols = ",".join(row)
    return c.execute(f"{verb} INTO {table} ({cols}) VALUES ({','.join('?' * len(row))})", list(row.values()))


REC = dict(record_id="r1", stream_id="s", position=1, author_peer_id="a", kind="message", body_json='"x"', idempotency_key="k",
           payload_digest="d", created_at="t", appended_at="t")
CONTROL_REC = dict(REC, record_id="rc", position=2, author_peer_id="ops", idempotency_key="k2", kind="control.reconcile",
                   body_json=json.dumps({"decision": "RETRY", "delivery_id": "d1"}))
DELIVERY = dict(delivery_id="d1", seq=1, stream_id="s", peer_id="p", record_id="r1", record_position=1, attempt=1,
                certainty="NOT_STARTED", claim_generation=1)
EV = dict(seq=1, delivery_id="d", stream_id="s", peer_id="p", kind="k", detail="{}")
SE = dict(seq=1, stream_id="s", peer_id="p", event="e", from_state="a", to_state="b", generation=1, detail="{}")
CTL = dict(record_id="c", stream_id="s", peer_id="p", kind="k", position=1, effect="e")
INV = dict(delivery_id="d1", invocation_no=1, stream_id="s", peer_id="p", claim_generation=1)
RES = dict(delivery_id="d1", invocation_no=1, reason="prespawn_failure", claim_generation=1)
FIN = dict(stream_id="s", peer_id="p", claim_generation=1, result_json="{}")

# table -> (seed rows, row, colliding variants, non-colliding positive-control row or None)
CASES = {
    "records": ([], REC, [dict(REC, stream_id="s2"), dict(REC, record_id="r2", idempotency_key="k9"), dict(REC, record_id="r2", position=7)],
                dict(REC, record_id="r9", position=9, idempotency_key="k9")),
    "bridge_evidence": ([], EV, [dict(EV, kind="z")], dict(EV, seq=2)),
    "bridge_session_events": ([], SE, [dict(SE, event="X")], dict(SE, seq=2)),
    "bridge_deliveries": ([], DELIVERY, [dict(DELIVERY, attempt=2), dict(DELIVERY, delivery_id="d2", seq=2)],
                          dict(DELIVERY, delivery_id="d9", seq=9, attempt=9)),
    "bridge_reconciliations": ([("records", REC), ("records", CONTROL_REC), ("bridge_deliveries", DELIVERY)],
                               dict(delivery_id="d1", reconcile_record_id="rc", decision="RETRY"),
                               [dict(delivery_id="d1", reconcile_record_id="rc", decision="X")], None),
    "bridge_controls": ([], CTL, [dict(CTL, position=9, effect="other")], dict(CTL, peer_id="p2")),
    "bridge_finalizations": ([], FIN, [dict(FIN, result_json='{"x":1}')], dict(FIN, claim_generation=2)),
    "bridge_invocations": ([], INV, [dict(INV, claim_generation=9)], dict(INV, invocation_no=2)),
    "bridge_invocation_resolutions": ([("bridge_deliveries", DELIVERY), ("bridge_deliveries", dict(DELIVERY, delivery_id="z", seq=3, attempt=3)),
                                       ("bridge_invocations", INV), ("bridge_invocations", dict(INV, invocation_no=2)),
                                       ("bridge_invocations", dict(INV, delivery_id="z"))] + [
                                       ("bridge_evidence", dict(EV, seq=10 + i, delivery_id=d, kind=k, detail=json.dumps({"invocation_no": n})))
                                       for i, (d, n, k) in enumerate([("d1", 1, "prespawn_failure"), ("d1", 2, "prespawn_failure"),
                                                                      ("z", 1, "prespawn_failure"), ("d1", 1, "control_halt")])],
                                      RES, [dict(RES, reason="control_halt")], dict(RES, invocation_no=2)),
}


@pytest.fixture
def db(bridge_h):
    return bridge_h.db_path


@pytest.mark.parametrize("table", list(CASES))
@pytest.mark.parametrize("verb", ["INSERT OR REPLACE", "REPLACE"])
def test_replace_guard_without_recursive_triggers(db, table, verb):
    seeds, row, collisions, fresh = CASES[table]
    with closing(raw(db)) as c:
        for t, r in seeds:
            ins(c, t, r)
        ins(c, table, row)
    for bad in collisions:
        before = digest(db)
        with closing(raw(db)) as c, pytest.raises(sqlite3.IntegrityError):
            ins(c, table, bad, verb)
        assert digest(db) == before
    if fresh is not None:  # positive control: non-colliding inserts still work
        with closing(raw(db)) as c:
            ins(c, table, fresh)
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 2


def test_observation_tables_still_guarded(tmp_path):
    db = tmp_path / "o.db"
    ObservationStore(db)
    pool = dict(resource_pool_id="p", provider="x", kind="k")
    with closing(raw(db)) as c:
        ins(c, "resource_pools", pool)
        before = digest(db)
        with pytest.raises(sqlite3.IntegrityError):
            ins(c, "resource_pools", dict(pool, kind="z"), "INSERT OR REPLACE")
    assert digest(db) == before


def test_mutable_tables_legitimate_update_and_replace_still_work(db):
    with closing(raw(db)) as c:
        ins(c, "peers", dict(peer_id="a", created_at="t"))
        ins(c, "streams", dict(stream_id="s", state="OPEN", created_at="t"))
        ins(c, "offsets", dict(peer_id="a", stream_id="s"))
        c.execute("UPDATE offsets SET read_through_position = 3, revision = 2 WHERE peer_id='a'")
        c.execute("UPDATE streams SET revision = 2 WHERE stream_id='s'")
        ins(c, "offsets", dict(peer_id="a", stream_id="s", read_through_position=5), "INSERT OR REPLACE")
        assert c.execute("SELECT read_through_position FROM offsets").fetchall() == [(5,)]


def _triggers(db):
    with closing(raw(db)) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}


def _version(db):
    with closing(raw(db)) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def test_records_guard_is_migration_v2_and_old_store_upgrades(tmp_path):
    db = tmp_path / "old.db"
    assert run_migrations(db, MIGRATIONS[:1]) == [1]  # a store created at the previous version
    assert "records_no_replace" not in _triggers(db)
    with closing(raw(db)) as c:
        ins(c, "peers", dict(peer_id="a", created_at="t"))
        ins(c, "streams", dict(stream_id="s", state="OPEN", created_at="t"))
        ins(c, "records", REC)
    assert run_migrations(db) == [2, 3]
    assert run_migrations(db) == []  # idempotent
    assert _version(db) == 3  # independent literal
    assert {"records_no_replace", "records_no_rowid_replace"} <= _triggers(db)
    before = digest(db)
    with closing(raw(db)) as c, pytest.raises(sqlite3.IntegrityError):
        ins(c, "records", dict(REC, kind="evil"), "INSERT OR REPLACE")
    assert digest(db) == before
    assert set(before) == {"peers", "streams", "stream_members", "records", "offsets"}


def test_migration_v2_is_atomic(tmp_path):
    db = tmp_path / "atomic.db"
    run_migrations(db, MIGRATIONS[:1])

    def boom(point):
        if point == "migration.before_commit":
            raise RuntimeError("crash")

    with pytest.raises(RuntimeError):
        run_migrations(db, fault=boom)
    assert _version(db) == 1 and "records_no_replace" not in _triggers(db)
    assert run_migrations(db) == [2, 3]


def test_v2_store_upgrades_to_v3_atomically(tmp_path):
    db = tmp_path / "v2.db"
    assert run_migrations(db, MIGRATIONS[:2]) == [1, 2]
    assert "records_no_rowid_replace" not in _triggers(db)

    def boom(point):
        if point == "migration.before_commit":
            raise RuntimeError("crash")

    with pytest.raises(RuntimeError):
        run_migrations(db, fault=boom)
    assert _version(db) == 2 and "records_no_rowid_replace" not in _triggers(db)
    assert run_migrations(db) == [3] and run_migrations(db) == [] and _version(db) == 3
    with closing(raw(db)) as c:
        ins(c, "peers", dict(peer_id="a", created_at="t"))
        ins(c, "streams", dict(stream_id="s", state="OPEN", created_at="t"))
        ins(c, "records", REC)
        with pytest.raises(sqlite3.IntegrityError):
            ins(c, "records", dict(REC, rowid=1, record_id="z", position=5, idempotency_key="z"), "INSERT OR REPLACE")


FRESH_KEYS = {
    "records": dict(record_id="z", position=5, idempotency_key="z"),
    "bridge_deliveries": dict(delivery_id="z", seq=5, attempt=5),
    "bridge_reconciliations": None,
    "bridge_controls": dict(record_id="z"),
    "bridge_finalizations": dict(claim_generation=5),
    "bridge_invocations": dict(delivery_id="z"),
    "bridge_invocation_resolutions": dict(delivery_id="z"),
}


@pytest.mark.parametrize("table", list(FRESH_KEYS))
@pytest.mark.parametrize("verb", ["INSERT OR REPLACE", "REPLACE"])
def test_rowid_replace_guard(db, table, verb):
    seeds, row, _, _ = CASES[table]
    fresh = FRESH_KEYS[table]
    if fresh is None:  # reconciliations: a second valid delivery/control pair is needed
        seeds = seeds + [("records", dict(CONTROL_REC, record_id="rc2", position=3, idempotency_key="k3",
                                          body_json=json.dumps({"decision": "RETRY", "delivery_id": "d2"}))),
                         ("bridge_deliveries", dict(DELIVERY, delivery_id="d2", seq=2, attempt=2))]
        bad = dict(row, rowid=1, delivery_id="d2", reconcile_record_id="rc2")
    else:
        bad = dict(row, rowid=1, **fresh)
    with closing(raw(db)) as c:
        for t, r in seeds:
            ins(c, t, r)
        ins(c, table, row)
    before = digest(db)
    with closing(raw(db)) as c, pytest.raises(sqlite3.IntegrityError):
        ins(c, table, bad, verb)
    assert digest(db) == before
    with closing(raw(db)) as c:  # positive control: explicit unused rowid is fine
        ins(c, table, dict(bad, rowid=77))


def test_rowid_replace_guard_observation_tables(tmp_path):
    db = tmp_path / "o.db"
    ObservationStore(db)
    pool = dict(resource_pool_id="p", provider="x", kind="k")
    obs = dict(observation_id="o1", subject_ref="s", kind="k", source="x", state="OBSERVED", payload_json="{}",
               observed_at="t", captured_at="t", effective_at_us=1)
    with closing(raw(db)) as c:
        ins(c, "resource_pools", pool)
        ins(c, "observations", obs)
        cols = [r[1] for r in c.execute("PRAGMA table_info(observations)")]
    before = digest(db)
    with closing(raw(db)) as c:
        with pytest.raises(sqlite3.IntegrityError):
            ins(c, "resource_pools", dict(pool, rowid=1, resource_pool_id="p2"), "INSERT OR REPLACE")
        with pytest.raises(sqlite3.IntegrityError):
            ins(c, "observations", dict(obs, capture_seq=1, observation_id="o2"), "INSERT OR REPLACE")
    assert digest(db) == before
    with closing(raw(db)) as c:
        ins(c, "observations", dict(obs, capture_seq=9, observation_id="o3"))
