"""Child-process entrypoints for multiprocessing(spawn) tests. Each worker puts exactly ONE dict on out_q."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

BARRIER_TIMEOUT = 40.0


def _err(e: BaseException) -> dict[str, str]:
    return {"type": type(e).__name__, "msg": str(e)}


def append_worker(db_path: str, barrier: Any, worker_id: int, n: int, out_q: Any) -> None:
    from peerhub.m1.store import CoreStore

    out: dict[str, Any] = {"worker": worker_id, "ok": [], "err": []}
    try:
        store = CoreStore(db_path)
        barrier.wait(BARRIER_TIMEOUT)
        for i in range(n):
            key = f"w{worker_id}-{i}"
            try:
                r = store.append_record(created_at="2026-10-01T00:00:00Z", stream_id="s", author_peer_id="a",
                                        kind="message", body={"w": worker_id, "i": i}, idempotency_key=key)
                out["ok"].append((r.idempotency_key, r.record_id, r.position))
            except Exception as e:  # recorded verbatim; the test decides which types are acceptable
                out["err"].append(_err(e))
    except BaseException as e:
        out["fatal"] = _err(e)
    out_q.put(out)


def same_request_worker(db_path: str, barrier: Any, request: dict, out_q: Any) -> None:
    from peerhub.m1.store import CoreStore

    out: dict[str, Any] = {}
    try:
        store = CoreStore(db_path)
        barrier.wait(BARRIER_TIMEOUT)
        try:
            r = store.append_record(**request)
            out["ok"] = (r.record_id, r.position)
        except Exception as e:
            out["err"] = _err(e)
    except BaseException as e:
        out["fatal"] = _err(e)
    out_q.put(out)


def cas_worker(db_path: str, barrier: Any, peer: str, stream: str, expected_rev: int, new_pos: int, out_q: Any) -> None:
    from peerhub.m1.store import CoreStore

    out: dict[str, Any] = {"new_pos": new_pos}
    try:
        store = CoreStore(db_path)
        barrier.wait(BARRIER_TIMEOUT)
        try:
            o = store.advance_offset_cas(peer, stream, new_pos, expected_rev)
            out["ok"] = (o.read_through_position, o.revision)
        except Exception as e:
            out["err"] = _err(e)
    except BaseException as e:
        out["fatal"] = _err(e)
    out_q.put(out)


def claim_worker(db_path: str, ws_root: str, barrier: Any, owner: str, peer: str, stream: str, out_q: Any) -> None:
    from peerhub.extensions.bridge_claims import ClaimStore, ClaimToken
    from peerhub.m1.store import CoreStore
    from peerhub.m1.workspace import Workspace

    out: dict[str, Any] = {"owner": owner}
    try:
        ws = Workspace(Path(ws_root))
        claims = ClaimStore(db_path, generation=ws.generation, clock=lambda: 5000.0)
        store = CoreStore(db_path)
        barrier.wait(BARRIER_TIMEOUT)
        try:
            tok = claims.acquire(peer, stream, owner, lease_sec=60.0)
            out["token_generation"] = tok.generation
        except Exception as e:
            out["acquire_err"] = _err(e)
            tok = ClaimToken(ws.generation(), 1, owner, peer, stream)  # forged claim of generation 1
        try:
            o = store.advance_offset_cas(peer, stream, 1, 1, guard=claims.guard(tok))
            out["ack"] = (o.read_through_position, o.revision)
        except Exception as e:
            out["ack_err"] = _err(e)
        try:
            r = store.append_record(created_at="2026-10-01T00:00:00Z", stream_id=stream, author_peer_id=peer,
                                    kind="message", body=f"resp-{owner}", idempotency_key=f"resp-{owner}",
                                    guard=claims.guard(tok))
            out["append"] = r.position
        except Exception as e:
            out["append_err"] = _err(e)
    except BaseException as e:
        out["fatal"] = _err(e)
    out_q.put(out)


def verify_worker(db_path: str, stream: str, out_q: Any) -> None:
    """Fresh process: reopen the store, run integrity_check, dump logical records + state digest."""
    from peerhub.m1.store import CoreStore
    from tests.m1.harness.core import compute_state_digest

    out: dict[str, Any] = {}
    try:
        store = CoreStore(db_path)
        recs = store.read_records(stream, 0, 10**6)
        out["records"] = [(r.record_id, r.position, r.idempotency_key, r.payload_digest) for r in recs]
        with sqlite3.connect(db_path) as c:
            out["integrity"] = c.execute("PRAGMA integrity_check").fetchone()[0]
            out["user_version"] = c.execute("PRAGMA user_version").fetchone()[0]
        out["digest"] = compute_state_digest(db_path)
    except BaseException as e:
        out["fatal"] = _err(e)
    out_q.put(out)


def migrate_crash_worker(db_path: str, out_q: Any) -> None:
    """Runs the fixture N->N+1 migration and hard-kills the process at migration.before_commit."""
    from peerhub.m1.migrations import run_migrations
    from tests.m1.harness.migration_fixtures import FIXTURE_MIGRATIONS

    def fault(point: str) -> None:
        if point == "migration.before_commit":
            os._exit(17)

    run_migrations(db_path, migrations=FIXTURE_MIGRATIONS, fault=fault)
    out_q.put({"fatal": "fault did not fire"})
