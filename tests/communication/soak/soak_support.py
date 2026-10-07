"""Wave 9 soak support: EVIDENCE-ONLY capacity measurement + hard correctness oracles (SOAK-001/002).

Capacity numbers (latency, sizes) are recorded, never asserted against a threshold (no SLO is ratified, Q-W9-1).
Correctness is judged by an independent oracle (raw sqlite3 + hashlib + the deterministic generator), never by CoreStore reads.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from peerhub.extensions.bridge_claims import ClaimHeldError, ClaimStore
from peerhub.extensions.diag import ReadonlyDiag
from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore, IdempotencyConflictError, OffsetBeyondHeadError
from peerhub.core.workspace import Workspace

OPT_IN_ENV = "PEERHUB_SOAK"
SCALE_ENV = "PEERHUB_SOAK_SCALE"
EVIDENCE_DIR_ENV = "PEERHUB_SOAK_EVIDENCE_DIR"
SCALED_TIMEOUT_S = int(os.environ.get("PEERHUB_SOAK_TIMEOUT_S", "10800"))  # scaled runs exceed the 60 s default test timeout
SCHEMA = "peerhub.core.soak.evidence/1"
SEED = 20261004
CREATED = "2026-10-01T00:00:00Z"

SCALES = {
    "smoke": {"records": 300, "authors": 3, "restarts": 2, "peers": 60, "streams": 12, "members": 5, "stream_records": 3,
              "claim_threads": 4, "claim_keys": 5},
    "moderate": {"records": 20000, "authors": 4, "restarts": 4, "peers": 2000, "streams": 400, "members": 8, "stream_records": 5,
                 "claim_threads": 8, "claim_keys": 50},
    "full": {"records": 200000, "authors": 8, "restarts": 8, "peers": 20000, "streams": 2000, "members": 16, "stream_records": 10,
             "claim_threads": 16, "claim_keys": 200},
}


def opt_in_reason() -> str | None:
    if os.environ.get(OPT_IN_ENV) == "1":
        return None
    return f"SOAK-OPT-IN[env={OPT_IN_ENV};required=1;marker=soak]: scaled soak runs are disabled unless {OPT_IN_ENV}=1 and -m soak"


def scaled_name() -> str:
    name = os.environ.get(SCALE_ENV, "moderate")
    if name not in SCALES:
        raise ValueError(f"unknown {SCALE_ENV}={name!r}; expected one of {sorted(SCALES)}")
    return name


# ----------------------------------------------------------------------------- measurement helpers
def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def stats(samples: list[float]) -> dict:
    if not samples:
        return {"count": 0, "min_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None, "mean_ms": None}
    s = sorted(samples)

    def q(p: float) -> float:
        return round(s[min(len(s) - 1, int(p * len(s)))], 4)

    return {"count": len(s), "min_ms": round(s[0], 4), "p50_ms": q(0.50), "p95_ms": q(0.95), "p99_ms": q(0.99),
            "max_ms": round(s[-1], 4), "mean_ms": round(sum(s) / len(s), 4)}


def file_sizes(db) -> dict:
    db = Path(db)

    def size(p: Path) -> int:
        return p.stat().st_size if p.exists() else 0

    return {"db_bytes": size(db), "wal_bytes": size(Path(str(db) + "-wal")), "shm_bytes": size(Path(str(db) + "-shm"))}


def write_evidence(doc: dict, name: str, scale: str) -> Path | None:
    """Writes `<dir>/<name>.<scale>.json` (sorted keys, stable layout) when PEERHUB_SOAK_EVIDENCE_DIR is set."""
    d = os.environ.get(EVIDENCE_DIR_ENV)
    if not d:
        return None
    out = Path(d)
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{name}.{scale}.json"
    p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


# ----------------------------------------------------------------------------- deterministic generators
def body_for(i: int, seed: int = SEED) -> dict:
    return {"n": i, "pad": hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()[:24]}


def author_for(i: int, authors: list[str]) -> str:
    return authors[i % len(authors)]


def key_for(i: int) -> str:
    return f"k{i}"


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def expected_chain(n: int, authors: list[str], seed: int = SEED) -> str:
    """Independent expectation: sha256 over the ordered (position, author, key, canonical body) tuples the generator emits."""
    h = hashlib.sha256()
    for i in range(n):
        h.update(f"{i + 1}|{author_for(i, authors)}|{key_for(i)}|{_canon(body_for(i, seed))}\n".encode())
    return h.hexdigest()


# ----------------------------------------------------------------------------- independent oracles (raw SQL only)
def oracle_stream(db, stream_id: str, n: int, authors: list[str], seed: int = SEED) -> list[str]:
    """Violations of: no lost/duplicate Records, positions strictly monotonic (== 1..n in append order), contents/order == generator,
    Offsets <= head, integrity_check ok. Empty list == correct."""
    v: list[str] = []
    with closing(sqlite3.connect(str(db))) as c:
        rows = c.execute("SELECT position, author_peer_id, idempotency_key, body_json FROM records WHERE stream_id=? ORDER BY rowid",
                         (stream_id,)).fetchall()
        if len(rows) != n:
            v.append(f"record count {len(rows)} != expected {n}")
        positions = [r[0] for r in rows]
        if any(b <= a for a, b in zip(positions, positions[1:])):
            v.append("positions not strictly increasing in append order")
        if positions != list(range(1, len(rows) + 1)):
            v.append("positions are not the contiguous sequence 1..N")
        keys = [(r[1], r[2]) for r in rows]
        if len(set(keys)) != len(keys):
            v.append("duplicate (author, idempotency_key)")
        h = hashlib.sha256()
        for r in sorted(rows, key=lambda r: r[0]):
            h.update(f"{r[0]}|{r[1]}|{r[2]}|{_canon(json.loads(r[3]))}\n".encode())
        if h.hexdigest() != expected_chain(n, authors, seed):
            v.append("content/order chain differs from the deterministic generator")
        head = max(positions) if positions else 0
        for peer, pos in c.execute("SELECT peer_id, read_through_position FROM offsets WHERE stream_id=?", (stream_id,)):
            if pos > head:
                v.append(f"offset of {peer} ({pos}) beyond head {head}")
        ic = c.execute("PRAGMA integrity_check").fetchall()
        if ic != [("ok",)]:
            v.append(f"integrity_check: {ic[:3]}")
    return v


def oracle_cardinality(db, peers: int, streams: int, members: int, stream_records: int, offsets: dict) -> list[str]:
    v: list[str] = []
    with closing(sqlite3.connect(str(db))) as c:
        if c.execute("SELECT COUNT(*) FROM peers").fetchone()[0] != peers:
            v.append("peer count mismatch")
        if c.execute("SELECT COUNT(DISTINCT peer_id) FROM peers").fetchone()[0] != peers:
            v.append("duplicate peers")
        if c.execute("SELECT COUNT(*) FROM streams").fetchone()[0] != streams:
            v.append("stream count mismatch")
        if c.execute("SELECT COUNT(*) FROM stream_members").fetchone()[0] != streams * members:
            v.append("membership count mismatch")
        if c.execute("SELECT COUNT(*) FROM records").fetchone()[0] != streams * stream_records:
            v.append("record count mismatch")
        bad = c.execute("SELECT stream_id FROM records GROUP BY stream_id "
                        "HAVING MIN(position) != 1 OR MAX(position) != COUNT(*) OR COUNT(*) != ?", (stream_records,)).fetchall()
        if bad:
            v.append(f"streams with non-contiguous/incomplete positions: {len(bad)}")
        if c.execute("SELECT COUNT(*) FROM offsets o WHERE o.read_through_position > "
                     "(SELECT COALESCE(MAX(position),0) FROM records r WHERE r.stream_id=o.stream_id)").fetchone()[0]:
            v.append("offset beyond head")
        got = {(p, s): pos for p, s, pos in c.execute("SELECT peer_id, stream_id, read_through_position FROM offsets")}
        if got != offsets:
            v.append("persisted offsets differ from the expected mapping")
        if c.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            v.append("integrity_check not ok")
        if c.execute("PRAGMA foreign_key_check").fetchall():
            v.append("foreign_key_check reports rows")
    return v


# ----------------------------------------------------------------------------- SOAK-001
def run_large_stream(work, scale: str, seed: int = SEED, cfg: dict | None = None) -> dict:
    cfg = cfg or SCALES[scale]
    n, restarts = cfg["records"], cfg["restarts"]
    authors = [f"author-{i}" for i in range(cfg["authors"])]
    db = Workspace(Path(work)).db_path
    rng = random.Random(seed)
    store = CoreStore(db)
    for a in authors:
        store.register_peer(Peer(peer_id=a, created_at=CREATED))
    store.create_stream(Stream(stream_id="big", members=authors, created_at=CREATED))

    def req(i: int, body=None) -> dict:
        return {"created_at": CREATED, "stream_id": "big", "author_peer_id": author_for(i, authors), "kind": "message",
                "body": body_for(i, seed) if body is None else body, "idempotency_key": key_for(i)}

    append_ms, retry_ms, restart_ms = [], [], []
    retry_ok = conflict_ok = wal_peak = 0
    boundaries = [n * (k + 1) // (restarts + 1) for k in range(restarts)] + [n]
    i = 0
    for b in boundaries:
        while i < b:
            t0 = time.perf_counter()
            rec = store.append_record(**req(i))
            append_ms.append(_ms(t0))
            assert rec.position == i + 1
            if i % 50 == 0:
                wal_peak = max(wal_peak, file_sizes(db)["wal_bytes"])
            if rng.random() < 0.05:  # mixed retries: same payload and key (lost response) -> the same Record
                t0 = time.perf_counter()
                again = store.append_record(**req(i))
                retry_ms.append(_ms(t0))
                assert (again.record_id, again.position) == (rec.record_id, rec.position)
                retry_ok += 1
            i += 1
        if b != n:
            t0 = time.perf_counter()
            store = CoreStore(db)  # restart: fresh store (preflight + migrations no-op)
            restart_ms.append(_ms(t0))
            for j in rng.sample(range(b), min(20, b)):  # retry sampled earlier keys after the restart
                assert store.append_record(**req(j)).position == j + 1
                retry_ok += 1
            try:
                store.append_record(**req(rng.randrange(b), body={"different": True}))
            except IdempotencyConflictError:
                conflict_ok += 1
    sizes_after_append = file_sizes(db)

    store = CoreStore(db)  # final restart, then scan the whole Stream
    read_ms, scanned, after = [], 0, 0
    t_scan = time.perf_counter()
    while True:
        t0 = time.perf_counter()
        page = store.read_records("big", after_position=after, limit=1000)
        read_ms.append(_ms(t0))
        if not page:
            break
        scanned += len(page)
        assert [r.position for r in page] == list(range(after + 1, after + len(page) + 1))
        after = page[-1].position
    scan_total_ms = _ms(t_scan)
    point_ms = []
    for j in rng.sample(range(n), min(200, n)):
        t0 = time.perf_counter()
        got = store.read_records("big", after_position=j, limit=1)
        point_ms.append(_ms(t0))
        assert got[0].position == j + 1

    head = store.stream_head("big")
    off_ms, offsets_written = [], {}
    for k, a in enumerate(authors):
        target = head * (k + 1) // len(authors)
        t0 = time.perf_counter()
        store.advance_offset_cas(a, "big", target, 1)
        off_ms.append(_ms(t0))
        offsets_written[a] = target
    beyond_rejected = False
    try:
        store.advance_offset_cas(authors[0], "big", head + 1, 2)
    except OffsetBeyondHeadError:
        beyond_rejected = True

    diag = diag_under_writer(db, "big", author_for(n, authors), n, seed)  # appends exactly one more Record (index n)
    violations = oracle_stream(db, "big", n + 1, authors, seed)
    sizes_end = file_sizes(db)
    return {
        "schema": SCHEMA, "test_id": "SOAK-001", "scale": scale, "seed": seed, "config": dict(cfg),
        "evidence_only": True, "capacity_thresholds": None,
        "correctness": {
            "violations": violations, "records_expected": n + 1, "records_scanned_before_diag_write": scanned,
            "retries_same_payload_returned_same_record": retry_ok, "retries_conflicting_payload_rejected": conflict_ok,
            "offset_beyond_head_rejected": beyond_rejected, "offsets_written": offsets_written, "head_before_diag_write": head,
            "diag": diag["correctness"],
        },
        "measurements": {
            "append": stats(append_ms), "idempotent_retry": stats(retry_ms), "restart_open": stats(restart_ms),
            "read_page_1000": stats(read_ms), "read_point_limit1": stats(point_ms), "full_scan_total_ms": round(scan_total_ms, 2),
            "offset_advance": stats(off_ms), "diag_snapshot_ms": diag["snapshot_ms"],
            "wal_peak_bytes_sampled": wal_peak, "sizes_after_append": sizes_after_append, "sizes_end": sizes_end,
            "db_bytes_per_record": round(sizes_end["db_bytes"] / (n + 1), 2),
        },
    }


def _core_sections(report) -> dict:
    """Status of the Core sections Diag must render from a bare Core DB (observation/pool tables belong to extensions: PARTIAL is expected)."""
    return {k: report.sections[k].status for k in ("peers", "streams")}


def diag_under_writer(db, stream_id: str, author: str, index: int, seed: int) -> dict:
    """Diag renders ONE read-only snapshot while a real writer thread commits a Record mid-render (bounded waits)."""
    reached, written, errs = threading.Event(), threading.Event(), []
    with closing(sqlite3.connect(str(db))) as c:
        before = c.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        head_before = c.execute("SELECT MAX(position) FROM records WHERE stream_id=?", (stream_id,)).fetchone()[0]

    def writer():
        try:
            reached.wait(60)
            CoreStore(db).append_record(created_at=CREATED, stream_id=stream_id, author_peer_id=author, kind="message",
                                        body=body_for(index, seed), idempotency_key=key_for(index))
        except BaseException as e:  # surfaced through the evidence, never swallowed silently
            errs.append(repr(e))
        finally:
            written.set()

    def hook(section):
        if section == "peers":
            reached.set()
            assert written.wait(60), "writer blocked by the Diag snapshot"

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    t0 = time.perf_counter()
    report = ReadonlyDiag(db, section_hook=hook).render()
    ms = _ms(t0)
    t.join(60)
    with closing(sqlite3.connect(str(db))) as c:
        after = c.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    st = {s["stream_id"]: s for s in report.sections["streams"].data["streams"]}[stream_id]
    return {"snapshot_ms": round(ms, 2), "correctness": {
        "writer_errors": errs, "status": report.status, "core_sections": _core_sections(report), "snapshot_records_total": report.snapshot["records_total"],
        "snapshot_head": st["head_position"], "records_before_write": before, "records_after_write": after,
        "snapshot_is_pre_write": report.snapshot["records_total"] == before and st["head_position"] == head_before,
        "writer_committed": after == before + 1}}


# ----------------------------------------------------------------------------- SOAK-002
def run_cardinality(work, scale: str, seed: int = SEED, cfg: dict | None = None) -> dict:
    cfg = cfg or SCALES[scale]
    P, M, K, R = cfg["peers"], cfg["streams"], cfg["members"], cfg["stream_records"]
    rng = random.Random(seed)
    ws = Workspace(Path(work))
    db = ws.db_path
    store = CoreStore(db)
    peers = [f"peer-{i:06d}" for i in range(P)]
    reg_ms, mk_ms, app_ms, off_ms = [], [], [], []
    for p in peers:  # the same adapter kind for every Peer: no vendor-specific ceiling
        t0 = time.perf_counter()
        store.register_peer(Peer(peer_id=p, adapter_ref="cc", created_at=CREATED))
        reg_ms.append(_ms(t0))
    members_of: dict[str, list[str]] = {}
    for s in range(M):
        sid = f"stream-{s:05d}"
        members_of[sid] = [peers[(s * K + j) % P] for j in range(K)]
        t0 = time.perf_counter()
        store.create_stream(Stream(stream_id=sid, members=members_of[sid], created_at=CREATED))
        mk_ms.append(_ms(t0))
    wal_peak = 0
    for sid, mem in members_of.items():
        for r in range(R):
            t0 = time.perf_counter()
            store.append_record(created_at=CREATED, stream_id=sid, author_peer_id=mem[r % K], kind="message",
                                body={"r": r}, idempotency_key=f"c{r}")
            app_ms.append(_ms(t0))
            if len(app_ms) % 50 == 0:
                wal_peak = max(wal_peak, file_sizes(db)["wal_bytes"])
    offsets: dict = {}
    for sid, mem in members_of.items():
        for peer in mem:
            pos = rng.randint(0, R)
            if pos:
                t0 = time.perf_counter()
                store.advance_offset_cas(peer, sid, pos, 1)
                off_ms.append(_ms(t0))
                offsets[(peer, sid)] = pos
    store = CoreStore(db)  # restart, then read the mappings back
    rd_peer, rd_stream, rd_off = [], [], []
    for p in rng.sample(peers, min(300, P)):
        t0 = time.perf_counter()
        got = store.get_peer(p)
        rd_peer.append(_ms(t0))
        assert got is not None and got.adapter_ref == "cc"
    for sid in rng.sample(sorted(members_of), min(300, M)):
        t0 = time.perf_counter()
        got = store.get_stream(sid)
        rd_stream.append(_ms(t0))
        assert got is not None and sorted(got.members) == sorted(members_of[sid])
    for key in rng.sample(sorted(offsets), min(300, len(offsets))):
        t0 = time.perf_counter()
        got = store.get_offset(*key)
        rd_off.append(_ms(t0))
        assert got.read_through_position == offsets[key]

    t0 = time.perf_counter()
    report = ReadonlyDiag(db).render()
    diag_ms = _ms(t0)
    contention = claim_contention(ws, members_of, cfg["claim_keys"], cfg["claim_threads"], rng)
    violations = oracle_cardinality(db, P, M, K, R, offsets)
    return {
        "schema": SCHEMA, "test_id": "SOAK-002", "scale": scale, "seed": seed, "config": dict(cfg),
        "evidence_only": True, "capacity_thresholds": None,
        "correctness": {
            "violations": violations, "peers": P, "streams": M, "offsets_persisted": len(offsets),
            "peers_with_same_adapter_kind": P, "diag_status": report.status, "diag_core_sections": _core_sections(report), "diag_records_total": report.snapshot["records_total"],
            "claim_contention": contention["correctness"],
        },
        "measurements": {
            "register_peer": stats(reg_ms), "create_stream": stats(mk_ms), "append": stats(app_ms), "offset_advance": stats(off_ms),
            "read_peer": stats(rd_peer), "read_stream": stats(rd_stream), "read_offset": stats(rd_off),
            "diag_snapshot_ms": round(diag_ms, 2), "claim_contention": contention["measurements"], "wal_peak_bytes_sampled": wal_peak, "sizes_end": file_sizes(db),
        },
    }


def claim_contention(ws: Workspace, members_of: dict, keys: int, threads: int, rng: random.Random) -> dict:
    """For `keys` (peer, stream) scopes, `threads` real threads race acquire(); the oracle is the raw bridge_claims table."""
    db = ws.db_path
    claims = ClaimStore(db, generation=ws.generation)  # real clock; a long lease keeps the winner live during the race
    scopes = rng.sample([(mem[0], sid) for sid, mem in sorted(members_of.items())], min(keys, len(members_of)))
    lat, wins, held, other = [], {}, 0, []
    lock = threading.Lock()
    for peer, sid in scopes:
        barrier = threading.Barrier(threads)
        res: list = []

        def racer(k, peer=peer, sid=sid, barrier=barrier, res=res):
            barrier.wait(60)
            t0 = time.perf_counter()
            try:
                out = ("won", claims.acquire(peer, sid, f"owner-{k}", 600.0).owner_id)
            except ClaimHeldError:
                out = ("held", None)
            except BaseException as e:
                out = ("error", repr(e))
            with lock:
                lat.append(_ms(t0))
                res.append(out)

        ts = [threading.Thread(target=racer, args=(k,), daemon=True) for k in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(120)
        wins[(peer, sid)] = [r[1] for r in res if r[0] == "won"]
        held += sum(1 for r in res if r[0] == "held")
        other += [r[1] for r in res if r[0] == "error"]
    viol = []
    with closing(sqlite3.connect(str(db))) as c:
        for (peer, sid), w in wins.items():
            rows = c.execute("SELECT owner_id FROM bridge_claims WHERE peer_id=? AND stream_id=?", (peer, sid)).fetchall()
            if len(w) != 1:
                viol.append(f"{peer}/{sid}: {len(w)} winners")
            if len(rows) != 1 or (w and rows[0][0] != w[0]):
                viol.append(f"{peer}/{sid}: persisted owner differs from the winner")
    return {"correctness": {"scopes": len(scopes), "threads": threads, "violations": viol, "unexpected_errors": other,
                            "held_rejections": held, "expected_held_rejections": len(scopes) * (threads - 1)},
            "measurements": {"acquire": stats(lat)}}
