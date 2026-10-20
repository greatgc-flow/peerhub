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
from typing import Any, Callable, cast

from peerhub.extensions.adapters import CliRuntimeTarget
from peerhub.extensions.peer_kinds import ALIASES  # noqa: F401  (re-exported for existing importers)
from peerhub.extensions.bridge import Bridge, CycleResult, RuntimeTarget, normalize_session_selector
from peerhub.extensions.bridge_claims import ClaimScopeError, ClaimStore, StaleClaimError
from peerhub.core.models import Peer, Stream, utc_now_iso
from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
from peerhub.core.store import AlreadyExistsError, CoreStore, IdempotencyConflictError, StorageCorruptError, storage_errors
from peerhub.core.workspace import Workspace



class LegacySelectorUnsupported(ValueError):
    """Historical ask identity has no final selector intent; no replay shim."""
    code = "legacy_selector_unsupported"


class StreamPeerMismatchError(ValueError):
    """An established ask stream cannot be retargeted to a different peer (#125)."""
    code = "stream_peer_mismatch"


def _read_delivery_result(conn: sqlite3.Connection, delivery: sqlite3.Row) -> dict[str, Any]:
    stored = json.loads(delivery["result_json"])
    refused = stored.get("task_outcome") == "NOT_EXECUTED"
    status = "failed_not_started" if delivery["certainty"] == "NOT_STARTED" else "not_executed" if refused else "recovered_terminal"
    result = asdict(CycleResult(status, delivery["record_id"], delivery["delivery_id"],
                               delivery["certainty"], delivery["response_record_id"], delivery["session_generation"],
                               session=stored.get("session")))
    # The private terminal payload may contain vendor/runner identity. Only the
    # final public envelope and allowlisted delivery fields cross the API boundary.
    result.update({key: stored[key] for key in ("response", "session", "error", "task_outcome", "reason", "refusal_reason",
                  "reset_time", "resets_at", "message", "output_truncated", "display_truncated",
                  "empty_response", "task_effects", "denied_actions") if key in stored})
    for _kind, detail in conn.execute("SELECT kind,detail FROM bridge_evidence WHERE delivery_id=? AND kind IN ('terminal','refusal_detected') ORDER BY seq", (delivery["delivery_id"],)):
        data = json.loads(detail)
        for key in ("effective_mode", "fallback_reason", "injected_record_ids", "output_truncated", "display_truncated", "reason", "refusal_reason", "reset_time", "resets_at", "message", "empty_response", "task_effects", "denied_actions"):
            if key in data:
                result[key] = data[key]
    row = conn.execute("SELECT body_json,metadata_json FROM records WHERE record_id=?", (delivery["response_record_id"],)).fetchone()
    if row is not None:
        result["response"] = json.loads(row[0])
        metadata = json.loads(row[1])
        # Provider usage stays distinct from session context occupancy; metadata carries its scope.
        result.pop("usage", None)
        for key in ("usage", "usage_scope", "usage_attribution", "display_truncated", "output_truncated", "empty_response", "task_effects", "denied_actions"):
            if key in metadata:
                result[key] = metadata[key]
    if delivery["certainty"] == "TERMINAL" and isinstance(result.get("response"), str) and not result["response"].strip():
        result["empty_response"] = True
        result["task_effects"] = "unverified"
    return result


def ask(db_path: str | Path, peer_id: str, prompt: str, *, stream_id: str | None = None,
        request_id: str | None = None, author: str = "user", workspace: str | Path | None = None,
        model: str | None = None, effort: str | None = None, timeout_s: float = 60,
        profile: str | None = None, silence_timeout_s: float | None = None, writable: bool = False,
        max_bytes: int = 1_000_000, runtime: RuntimeTarget | None = None,
        fresh: bool = False, session: str | None = None,
        on_output: Callable[[str], None] | None = None) -> dict[str, Any]:
    selector = normalize_session_selector(fresh=fresh, session=session)
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
                raise StorageCorruptError("database does not contain the communication Core schema; v0 support is archived on branch legacy/v0-main-final")
    kind = ALIASES.get(adapter_ref or peer_id)
    target: RuntimeTarget
    if runtime is None:
        if kind is None:
            raise ValueError("register this peer with --adapter cx, cc or ag before asking it")
        target = CliRuntimeTarget(kind, workspace or Path.cwd(), model=model, effort=effort,
                                  profile=profile, silence_timeout_s=silence_timeout_s, writable=writable,
                                  timeout_s=timeout_s, max_bytes=max_bytes, on_output=on_output)
    else:
        target = runtime
    sid = stream_id or f"peer:{peer_id}:chat"
    rid = request_id or uuid.uuid4().hex
    key = f"ask:{rid}"
    prompt_metadata = {"runtime_kind": target.runtime_kind, "binding": target.binding(), "session_selector": selector}
    generation_path = db.parent / "workspace.generation"
    identity_metadata = {"adapter_fingerprint": target.fingerprint(),
                         "workspace_generation": generation_path.read_text(encoding="utf-8") if generation_path.is_file() else None}
    # Validate immutable scoped request identity before inspecting cached execution,
    # and return the original durable result without constructing Bridge or a claim.
    if db.is_file() and db.stat().st_size:
        with storage_errors(), closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            previous = conn.execute("SELECT * FROM records WHERE stream_id=? AND author_peer_id=? AND idempotency_key=?", (sid, author, key)).fetchone()
            if previous is not None:
                previous_metadata = json.loads(previous["metadata_json"])
                base_metadata = {k: v for k, v in previous_metadata.items() if k not in ("session_selector", *identity_metadata)}
                expected_base = {k: v for k, v in prompt_metadata.items() if k != "session_selector"}
                if previous["kind"] != "prompt" or json.loads(previous["body_json"]) != prompt or json.loads(previous["targets_json"]) != [peer_id] or base_metadata != expected_base:
                    raise IdempotencyConflictError(f"request {rid!r} conflicts with its stored identity")
                stored_selector = previous_metadata.get("session_selector")
                if stored_selector is None:
                    raise LegacySelectorUnsupported(f"Request {rid!r} lacks supported session selector metadata.")
                if stored_selector != selector:
                    raise IdempotencyConflictError(f"request {rid!r} conflicts with its stored session selector")
                if any(name not in previous_metadata for name in identity_metadata):
                    raise LegacySelectorUnsupported(f"Request {rid!r} lacks complete session selector identity metadata.")
                if any(previous_metadata[name] != value for name, value in identity_metadata.items()):
                    raise IdempotencyConflictError(f"request {rid!r} conflicts with its stored fingerprint or workspace lineage")
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='bridge_deliveries'").fetchone():
                    settled = conn.execute("SELECT * FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND record_id=? AND acked=1 AND result_json IS NOT NULL ORDER BY attempt DESC LIMIT 1",
                                           (sid, peer_id, previous["record_id"])).fetchone()
                    if settled is not None:
                        replay = _read_delivery_result(conn, settled)
                        replay.update(request_id=rid, stream_id=sid, peer_id=peer_id, requested_record_id=previous["record_id"])
                        return replay
    validate_policy: Callable[[], None] | None = getattr(target, "validate_execution_policy", None)
    if callable(validate_policy):
        validate_policy()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    lineage = Workspace(Path(db_path).resolve().parent)
    store = CoreStore(db_path)
    peer = store.get_peer(peer_id)
    if peer is None:
        store.register_peer(Peer(peer_id=peer_id, adapter_ref=kind or target.runtime_kind))
    if store.get_peer(author) is None:
        store.register_peer(Peer(peer_id=author))
    if store.get_stream(sid) is None:
        try:
            store.create_stream(Stream(stream_id=sid, members=[author, peer_id]))
        except AlreadyExistsError:  # another caller created the same conversation
            pass
    prompt_metadata.update(adapter_fingerprint=target.fingerprint(), workspace_generation=lineage.generation())
    claims = ClaimStore(db_path, generation=lineage.generation)
    # Runtime events may be silent until completion; keep the claim alive for the bounded run.
    lease_sec = timeout_s + 60
    bridge = Bridge(store, claims, owner_id=f"ask-{uuid.uuid4().hex}", lease_sec=lease_sec)
    token = claims.acquire(peer_id, sid, bridge.owner_id, lease_sec)
    started_at = time.monotonic()
    try:
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            previous = conn.execute("SELECT created_at FROM records WHERE stream_id=? AND author_peer_id=? AND idempotency_key=?",
                                    (sid, author, key)).fetchone()
        def prompt_guard(conn: sqlite3.Connection, **scope: Any) -> None:
            if scope["stream_id"] != token.stream_id:
                raise ClaimScopeError("prompt and delivery claim must share a Stream")
            claims.check(conn, token)
            for row_targets_json, row_meta_json, row_key in conn.execute(
                "SELECT targets_json, metadata_json, idempotency_key FROM records WHERE stream_id=? AND kind='prompt'",
                (sid,),
            ):
                if not (isinstance(row_key, str) and row_key.startswith("ask:")):
                    continue
                try:
                    row_meta: Any = json.loads(row_meta_json or "{}")
                    row_targets: Any = json.loads(row_targets_json or "[]")
                except (ValueError, TypeError):
                    continue
                if not isinstance(row_meta, dict) or "runtime_kind" not in row_meta or "binding" not in row_meta:
                    continue
                if not isinstance(row_targets, list) or not row_targets:
                    continue
                targets_list = cast(list[Any], row_targets)
                if len(targets_list) != 1:
                    raise StreamPeerMismatchError(
                        f"stream {sid!r} has mixed historical prompt targets {targets_list!r}; "
                        f"cannot dispatch to '{peer_id}' on the same ask stream (use dedicated single-peer streams)"
                    )
                if targets_list[0] != peer_id:
                    bound_peer = str(targets_list[0])
                    raise StreamPeerMismatchError(
                        f"stream {sid!r} is already bound to ask target peer {bound_peer!r}; "
                        f"cannot dispatch to '{peer_id}' on the same ask stream (use a distinct --stream)"
                    )
        if previous is None:
            bridge.validate_selector_before_append(token, target, selector)
        rec = store.append_record(stream_id=sid, author_peer_id=author, kind="prompt", body=prompt,
                                  targets=[peer_id], idempotency_key=key,
                                  metadata=prompt_metadata,
                                  created_at=previous[0] if previous else utc_now_iso(), guard=prompt_guard)
        ev = bridge.execution_evidence(rec.record_id, peer_id)
        terminal = next((d for d in reversed(ev["deliveries"]) if d["certainty"] == "TERMINAL" and d["acked"]), None)
        refused = next((d for d in reversed(ev["deliveries"]) if d["certainty"] == "STARTED" and d["acked"]
                        and bridge.delivery_details(d["delivery_id"]).get("task_outcome") == "NOT_EXECUTED"), None)
        if terminal:
            result = asdict(CycleResult("recovered_terminal", rec.record_id, terminal["delivery_id"], "TERMINAL",
                                       terminal["response_record_id"], terminal["session_generation"]))
        elif refused:
            result = asdict(CycleResult("not_executed", rec.record_id, refused["delivery_id"], "STARTED",
                                       session_generation=refused["session_generation"],
                                       detail=bridge.delivery_details(refused["delivery_id"])))
        else:
            result = asdict(bridge.run_cycle(token, target))
        if result.get("delivery_id") is not None:
            with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                delivered = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (result["delivery_id"],)).fetchone()
                if delivered is not None and delivered["acked"] and delivered["result_json"] and delivered["certainty"] in ("NOT_STARTED", "STARTED", "TERMINAL"):
                    result.update(_read_delivery_result(conn, delivered))
                    if not terminal and not refused and delivered["certainty"] == "TERMINAL":
                        result["status"] = "delivered"
        result.update(bridge.delivery_details(result.get("delivery_id")))
        if isinstance(result.get("detail"), dict):
            for k in ("task_outcome", "reason", "refusal_reason", "reset_time", "resets_at", "message"):
                if k in result["detail"] and k not in result:
                    result[k] = result["detail"][k]
        response_id = result.get("response_record_id")
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT body_json, metadata_json FROM records WHERE record_id=?", (response_id,)).fetchone()
        result.update(request_id=rid, stream_id=sid, peer_id=peer_id,
                      requested_record_id=rec.record_id, response=json.loads(row[0]) if row else None)
        if row:
            metadata = json.loads(row[1])
            for key in ("usage", "usage_scope", "usage_attribution", "display_truncated", "output_truncated", "empty_response", "task_effects", "denied_actions"):
                if key in metadata:
                    result[key] = metadata[key]
        if result.get("certainty") == "TERMINAL" and isinstance(result.get("response"), str) and not result["response"].strip():
            result["empty_response"] = True
            result["task_effects"] = "unverified"
        # A conversation may have earlier pending work. Report its progress without claiming this prompt completed.
        if result.get("record_id") != rec.record_id:
            result["processed_status"] = result["status"]
            result["status"] = "pending_earlier_record"
        if not terminal and not refused:
            # Operation timing is activity evidence, never manufactured provider usage/quota.
            from peerhub.extensions.observation import ObservationStore
            from peerhub.extensions.observation_model import EvidenceState, Observation
            now = utc_now_iso()
            try:
                obs_payload: dict[str, Any] = {
                    "operation_elapsed_seconds": time.monotonic() - started_at,
                    "status": result["status"], "certainty": result.get("certainty"), "record_id": rec.record_id
                }
                if "reason" in result:
                    obs_payload["reason"] = result["reason"]
                if "reset_time" in result:
                    obs_payload["reset_time"] = result["reset_time"]
                ObservationStore(db_path).persist(Observation(
                    observation_id=uuid.uuid4().hex, subject_ref=peer_id, kind="activity", source="session_bridge.ask",
                    observed_at=now, captured_at=now, state=EvidenceState.MEASURED,
                    payload=obs_payload))
            except Exception as exc:
                result["observation_error"] = type(exc).__name__  # committed response truth remains authoritative
        if "error" not in result:
            result["error"] = (result.get("detail", {}).get("error") if isinstance(result.get("detail"), dict) else None)
            if not isinstance(result["error"], dict):
                result["error"] = None if result["status"] in ("delivered", "recovered_terminal") else dict(code="delivery_failed", message=f"Execution ended as {result['status']}.")
        return result
    finally:
        try:
            claims.release(token)
        except StaleClaimError:
            pass  # expiry/takeover cannot release a new owner's claim
