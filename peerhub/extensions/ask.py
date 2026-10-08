"""One-peer ask over the durable Session Bridge, independent of v0 dispatch."""
from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from peerhub.extensions.adapters import CliRuntimeTarget
from peerhub.extensions.peer_kinds import ALIASES  # noqa: F401  (re-exported for existing importers)
from peerhub.extensions.bridge import Bridge, CycleResult, RuntimeTarget
from peerhub.extensions.bridge_claims import ClaimScopeError, ClaimStore, StaleClaimError
from peerhub.core.models import Peer, Stream, utc_now_iso
from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
from peerhub.core.store import AlreadyExistsError, CoreStore, StorageCorruptError, storage_errors
from peerhub.core.workspace import Workspace



def ask(db_path: str | Path, peer_id: str, prompt: str, *, stream_id: str | None = None,
        request_id: str | None = None, author: str = "user", workspace: str | Path | None = None,
        model: str | None = None, effort: str | None = None, timeout_s: float = 60,
        profile: str | None = None, silence_timeout_s: float | None = None, writable: bool = False,
        max_bytes: int = 1_000_000, runtime: RuntimeTarget | None = None,
        on_output: Callable[[str], None] | None = None) -> dict[str, Any]:
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    if not math.isfinite(timeout_s) or timeout_s <= 0 or max_bytes <= 0:
        raise ValueError("timeout and output limit must be positive")
    if silence_timeout_s is not None and (not math.isfinite(silence_timeout_s) or silence_timeout_s <= 0):
        raise ValueError("silence timeout must be finite and positive")
    if runtime is not None and (profile is not None or silence_timeout_s is not None or writable):
        raise ValueError("profile/silence timeout/writable must be configured on an explicitly supplied runtime")
    if author == peer_id:
        raise ValueError("ask author and target peer must differ")
    # Resolve adapter configuration before any bootstrap/migration or prompt append.
    adapter_ref = None
    db = Path(db_path).resolve()
    if db.is_file() and db.stat().st_size:
        with storage_errors(), closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SUPPORTED_SCHEMA_VERSION:
                raise SchemaVersionError(future_schema_message(version, SUPPORTED_SCHEMA_VERSION))
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if "peers" in tables:
                row = conn.execute("SELECT adapter_ref FROM peers WHERE peer_id=?", (peer_id,)).fetchone()
                adapter_ref = row[0] if row else None
            elif tables:
                raise StorageCorruptError("database does not contain the communication Core schema; import legacy data explicitly")
    kind = ALIASES.get(adapter_ref or peer_id)
    if runtime is None:
        if kind is None:
            raise ValueError("register this peer with --adapter cx, cc or ag before asking it")
        runtime = CliRuntimeTarget(kind, workspace or Path.cwd(), model=model, effort=effort,
                                   profile=profile, silence_timeout_s=silence_timeout_s, writable=writable,
                                   timeout_s=timeout_s, max_bytes=max_bytes, on_output=on_output)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = CoreStore(db_path)
    peer = store.get_peer(peer_id)
    if peer is None:
        store.register_peer(Peer(peer_id=peer_id, adapter_ref=kind or runtime.runtime_kind))
    if store.get_peer(author) is None:
        store.register_peer(Peer(peer_id=author))
    sid = stream_id or f"peer:{peer_id}:chat"
    if store.get_stream(sid) is None:
        try:
            store.create_stream(Stream(stream_id=sid, members=[author, peer_id]))
        except AlreadyExistsError:  # another caller created the same conversation
            pass
    lineage = Workspace(Path(db_path).resolve().parent)
    claims = ClaimStore(db_path, generation=lineage.generation)
    # Runtime events may be silent until completion; keep the claim alive for the bounded run.
    lease_sec = timeout_s + 60
    bridge = Bridge(store, claims, owner_id=f"ask-{uuid.uuid4().hex}", lease_sec=lease_sec)
    token = claims.acquire(peer_id, sid, bridge.owner_id, lease_sec)
    started_at = time.monotonic()
    try:
        rid = request_id or uuid.uuid4().hex
        key = f"ask:{rid}"
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            previous = conn.execute("SELECT created_at FROM records WHERE stream_id=? AND author_peer_id=? AND idempotency_key=?",
                                    (sid, author, key)).fetchone()
        def prompt_guard(conn: sqlite3.Connection, **scope: Any) -> None:
            if scope["stream_id"] != token.stream_id:
                raise ClaimScopeError("prompt and delivery claim must share a Stream")
            claims.check(conn, token)
        rec = store.append_record(stream_id=sid, author_peer_id=author, kind="prompt", body=prompt,
                                  targets=[peer_id], idempotency_key=key,
                                  metadata={"runtime_kind": runtime.runtime_kind, "binding": runtime.binding()},
                                  created_at=previous[0] if previous else utc_now_iso(), guard=prompt_guard)
        ev = bridge.execution_evidence(rec.record_id, peer_id)
        terminal = next((d for d in reversed(ev["deliveries"]) if d["certainty"] == "TERMINAL" and d["acked"]), None)
        if terminal:
            result = asdict(CycleResult("recovered_terminal", rec.record_id, terminal["delivery_id"], "TERMINAL",
                                       terminal["response_record_id"], terminal["session_generation"]))
        else:
            result = asdict(bridge.run_cycle(token, runtime))
        response_id = result.get("response_record_id")
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT body_json FROM records WHERE record_id=?", (response_id,)).fetchone()
        result.update(request_id=rid, stream_id=sid, peer_id=peer_id,
                      requested_record_id=rec.record_id, response=json.loads(row[0]) if row else None)
        # A conversation may have earlier pending work. Report its progress without claiming this prompt completed.
        if result.get("record_id") != rec.record_id:
            result["processed_status"] = result["status"]
            result["status"] = "pending_earlier_record"
        if not terminal:
            # Operation timing is activity evidence, never manufactured provider usage/quota.
            from peerhub.extensions.observation import ObservationStore
            from peerhub.extensions.observation_model import EvidenceState, Observation
            now = utc_now_iso()
            try:
                ObservationStore(db_path).persist(Observation(
                    observation_id=uuid.uuid4().hex, subject_ref=peer_id, kind="activity", source="session_bridge.ask",
                    observed_at=now, captured_at=now, state=EvidenceState.MEASURED,
                    payload={"operation_elapsed_seconds": time.monotonic() - started_at,
                             "status": result["status"], "certainty": result.get("certainty"), "record_id": rec.record_id}))
            except Exception as exc:
                result["observation_error"] = type(exc).__name__  # committed response truth remains authoritative
        return result
    finally:
        try:
            claims.release(token)
        except StaleClaimError:
            pass  # expiry/takeover cannot release a new owner's claim
