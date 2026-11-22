"""One-peer ask over the durable Session Bridge, independent of v0 dispatch."""
from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from contextlib import AbstractContextManager, ExitStack, closing, nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, cast

if TYPE_CHECKING:
    from peerhub.extensions.accounts import AccountBinding

from peerhub.extensions.adapters import CliRuntimeTarget
from peerhub.extensions.adapters.api import API_TARGETS, ApiRuntimeTarget, RequestRejected
from peerhub.core.peer_kinds import ALIASES as ALIASES, BUILTIN_PEER_CHOICES, peer_kind  # ALIASES: re-exported
from peerhub.extensions.bridge import MAY_HAVE_STARTED, NOT_STARTED, STARTED, PrespawnError, Bridge, CycleResult, RuntimeTarget, normalize_session_selector
from peerhub.extensions.caller import detect_caller
from peerhub.extensions.bridge_claims import ClaimScopeError, ClaimStore, StaleClaimError
from peerhub.core.models import Peer, Stream, utc_now_iso
from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
from peerhub.core.store import AlreadyExistsError, CoreStore, IdempotencyConflictError, StorageCorruptError, storage_errors
from peerhub.core.workspace import Workspace


_DEFAULT_AUTHOR = "user"
_ASK_KEY_PREFIX = "ask:"


class LegacySelectorUnsupported(ValueError):
    """Historical ask identity has no final selector intent; no replay shim."""
    code = "legacy_selector_unsupported"


class PeerNotFoundError(ValueError):
    """The target peer is neither registered with an adapter nor a builtin name: a user error (#401)."""
    code = "peer_not_found"


class StreamPeerMismatchError(ValueError):
    """An established ask stream cannot be retargeted to a different peer (#125)."""
    code = "stream_peer_mismatch"


def _refuse_second_ask(conn: sqlite3.Connection, target: RuntimeTarget, stream_id: str, author: str, key: str) -> None:
    """A stateless peer (ca, oa, ga; #378) has no session to continue: its ask needs a stream of its own. Any record on the
    stream refuses this ask before anything is stored or sent: another ask (delivered, failed, uncertain or still
    running), or a record put there some other way, which would be delivered first as one more API call. Only the same
    ask (the same author AND the same request id; a request id is unique per stream and author) may come again."""
    if getattr(target, "stateless", False) and conn.execute(
            "SELECT 1 FROM records WHERE stream_id=? AND NOT EXISTS "
            "(SELECT 1 FROM records WHERE stream_id=? AND author_peer_id=? AND idempotency_key=?) LIMIT 1",
            (stream_id, stream_id, author, key)).fetchone():
        raise RequestRejected(f"{target.runtime_kind} is stateless; use a new stream")


def open_ask_snapshot(db: Path) -> sqlite3.Connection:
    """Open committed store state without initializing SQLite sidecars on an idle store."""
    wal = db.with_name(db.name + "-wal")
    uri = db.as_uri() + "?mode=ro&readonly_shm=1"
    try:
        has_wal = wal.stat().st_size > 0
    except FileNotFoundError:
        has_wal = False
    if not has_wal:
        uri += "&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _request_record(conn: sqlite3.Connection, stream: str, author: str, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM records WHERE stream_id=? AND author_peer_id=? AND idempotency_key=?",
                        (stream, author, key)).fetchone()


def _settled_delivery(conn: sqlite3.Connection, stream: str, peer: str, record: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND record_id=? "
                        "AND acked=1 AND result_json IS NOT NULL ORDER BY attempt DESC LIMIT 1",
                        (stream, peer, record)).fetchone()


def read_replay_counts(db: Path, requests: Iterable[tuple[str, str, str]]) -> dict[str, int] | None:
    """Sample stored ask state for (stream, peer, request) tuples without preparing a replay."""
    settled = uncertain = 0
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"records", "bridge_deliveries"} <= tables:
                return None
            has_invocations = {"bridge_invocations", "bridge_invocation_resolutions"} <= tables
            for stream, peer, request in requests:
                previous = _request_record(conn, stream, _DEFAULT_AUTHOR, f"{_ASK_KEY_PREFIX}{request}")
                if previous is None or previous["kind"] != "prompt":
                    continue
                record = previous["record_id"]
                if _settled_delivery(conn, stream, peer, record) is not None:
                    settled += 1
                    continue
                latest = conn.execute("SELECT delivery_id, certainty, acked FROM bridge_deliveries "
                                      "WHERE stream_id=? AND peer_id=? AND record_id=? ORDER BY attempt DESC LIMIT 1",
                                      (stream, peer, record)).fetchone()
                if latest is not None and not latest["acked"] and (
                    latest["certainty"] in (STARTED, MAY_HAVE_STARTED)
                    or (latest["certainty"] == NOT_STARTED and has_invocations
                        and Bridge.open_invocation(conn, latest["delivery_id"]) is not None)
                ):
                    uncertain += 1
    except (OSError, sqlite3.Error):
        return None  # Replay evidence is optional when a selected store cannot be read.
    return {"settled": settled, "uncertain": uncertain} if settled or uncertain else None


def _read_delivery_result(conn: sqlite3.Connection, delivery: sqlite3.Row, peer_id: str | None = None) -> dict[str, Any]:
    stored = json.loads(delivery["result_json"])
    refused = stored.get("task_outcome") == "NOT_EXECUTED"
    incomplete = delivery["certainty"] == "STARTED" and stored.get("reason") in ("truncated", "incomplete")  # API peers (#378)
    status = ("failed_not_started" if delivery["certainty"] == "NOT_STARTED" else "not_executed" if refused
              else stored["reason"] if incomplete else "recovered_terminal")
    result = asdict(CycleResult(status, delivery["record_id"], delivery["delivery_id"],
                               delivery["certainty"], delivery["response_record_id"], delivery["session_generation"],
                               session=stored.get("session")))
    # The private terminal payload may contain vendor/runner identity. Only the
    # final public envelope and allowlisted delivery fields cross the API boundary.
    result.update({key: stored[key] for key in ("response", "session", "error", "task_outcome", "reason", "refusal_reason",
                  "reset_time", "resets_at", "message", "output_truncated", "display_truncated",
                  "empty_response", "task_effects", "denied_actions", "env_scrubbed", "partial_response", "usage") if key in stored})
    for _kind, detail in conn.execute("SELECT kind,detail FROM bridge_evidence WHERE delivery_id=? AND kind IN ('terminal','refusal_detected','execution_metadata') ORDER BY seq", (delivery["delivery_id"],)):
        data = json.loads(detail)
        for key in ("effective_mode", "fallback_reason", "injected_record_ids", "output_truncated", "display_truncated", "reason", "refusal_reason", "reset_time", "resets_at", "message", "empty_response", "task_effects", "denied_actions", "env_scrubbed",
                    "ag_transport", "ag_transport_source", "ag_cli_version", "ag_stream_validation"):
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
    _mark_denied_empty(result, peer_id)
    return result


def _mark_denied_empty(result: dict[str, Any], peer_id: str | None = None) -> None:
    """An empty terminal reply with denied tool actions is not a usable answer (#300).

    Certainty stays TERMINAL: the turn ended. Only the public status changes, so callers cannot mistake it
    for a delivered answer. Idempotent.
    """
    denied = result.get("denied_actions")
    if (result.get("status") in ("delivered", "recovered_terminal") and result.get("certainty") == "TERMINAL"
            and result.get("empty_response") is True and isinstance(denied, list) and denied):
        names = ", ".join(str(n) for n in cast(list[Any], denied))
        result["status"] = "denied_empty"
        if not isinstance(result.get("error"), dict):
            result["error"] = dict(code="denied_empty", message=f"{peer_id or result.get('peer_id') or 'provider'} denied tool action(s): {names}; response empty (headless cannot prompt).")


def default_stream(peer_id: str, request_id: str | None, *, stateless: bool) -> str | None:
    """The stream of an ask without --stream: the one rule, for `ask` and for the CLI's guidance.

    A stateless peer (ca, oa, ga; #378) takes one ask per stream, so its stream is the request's own, not a shared chat;
    None when its request id is not known (`ask` generates one, a caller that has none cannot name the stream)."""
    if not stateless:
        return f"peer:{peer_id}:chat"
    return f"peer:{peer_id}:{request_id}" if request_id else None


def _packaged_cx_binding_change(saved: object, current: str, workspace: str | Path,
                                model: str | None, effort: str | None) -> bool:
    """The saved binding contains effective values. Use the current layers to identify packaged drift."""
    if not isinstance(saved, str):
        return False
    old_binding, _, old_scope = saved.partition("#")
    new_binding, _, new_scope = current.partition("#")
    old_parts, new_parts = old_binding.split("/"), new_binding.split("/")
    if not new_scope or old_scope != new_scope or len(old_parts) != 3 or len(new_parts) != 3:
        return False
    profile, new_model, new_effort = new_parts
    old_profile, old_model, old_effort = old_parts
    if profile != old_profile:
        return False
    from peerhub.extensions.adapters.profiles import inspect_profile

    fields = inspect_profile(profile, workspace, include_meta=True)["fields"]
    if fields["alias_of"]["source"] not in ("packaged", "default"):
        return False
    return all(old == new or (explicit is None and fields[key]["source"] == "packaged"
                              and new == (fields[key]["value"] or "default"))
               for key, old, new, explicit in (("model", old_model, new_model, model), ("effort", old_effort, new_effort, effort)))


def _check_stream_peer(conn: sqlite3.Connection, sid: str, peer_id: str) -> None:
    for row_targets_json, row_meta_json, row_key in conn.execute(
        "SELECT targets_json, metadata_json, idempotency_key FROM records WHERE stream_id=? AND kind='prompt'",
        (sid,),
    ):
        if not (isinstance(row_key, str) and row_key.startswith(_ASK_KEY_PREFIX)):
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


def _set_ask_error(result: dict[str, Any]) -> None:
    if "error" not in result:
        result["error"] = (result.get("detail", {}).get("error") if isinstance(result.get("detail"), dict) else None)
        if not isinstance(result["error"], dict):
            result["error"] = None if result["status"] in ("delivered", "recovered_terminal") else dict(code="delivery_failed", message=f"Execution ended as {result['status']}.")


def _mark_progress(result: dict[str, Any], requested_record_id: str | None) -> None:
    """Report earlier work without claiming this prompt completed."""
    if requested_record_id is None or result.get("record_id") != requested_record_id:
        result["processed_status"] = result["status"]
        result["status"] = "pending_earlier_record"


def _account_failure(rid: str, sid: str, peer_id: str, exc: Exception) -> dict[str, Any]:
    failure = asdict(CycleResult("failed_not_started", certainty="NOT_STARTED"))
    failure.update(request_id=rid, stream_id=sid, peer_id=peer_id, response=None,
                   env_scrubbed=[], error=dict(code="account_invalid", message=str(exc)))
    return failure


def ask(db_path: str | Path, peer_id: str, prompt: str, *, stream_id: str | None = None,
        request_id: str | None = None, author: str = _DEFAULT_AUTHOR, workspace: str | Path | None = None,
        model: str | None = None, effort: str | None = None, timeout_s: float = 60,
        profile: str | None = None, silence_timeout_s: float | None = None, writable: bool = False,
        max_bytes: int = 1_000_000, runtime: RuntimeTarget | None = None,
        fresh: bool = False, session: str | None = None,
        on_output: Callable[[str], None] | None = None,
        on_stream_budget: Callable[[], None] | None = None,
        before_dispatch: Callable[[Path, str], None] | None = None,
        query_file: str | Path | None = None, notice_alias: bool = False,
        dry_run: bool = False) -> dict[str, Any]:
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
    account_binding: AccountBinding | None = None
    db = Path(db_path).resolve()
    db_exists = db.is_file()
    has_store_data = db_exists and db.stat().st_size > 0
    with ExitStack() as reads:
        preview_conn = None
        if dry_run and has_store_data:
            with storage_errors():
                preview_conn = reads.enter_context(closing(open_ask_snapshot(db)))
                preview_conn.execute("PRAGMA temp_store=MEMORY")
                preview_conn.execute("BEGIN")

        def read_store() -> AbstractContextManager[sqlite3.Connection]:
            if preview_conn is not None:
                return nullcontext(preview_conn)
            return closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True))

        tables: set[str] = set()
        if has_store_data:
            with storage_errors(), read_store() as conn:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version > SUPPORTED_SCHEMA_VERSION:
                    raise SchemaVersionError(future_schema_message(version, SUPPORTED_SCHEMA_VERSION))
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if "peers" in tables:
                    row = conn.execute("SELECT adapter_ref,metadata_json FROM peers WHERE peer_id=?", (peer_id,)).fetchone()
                    adapter_ref = row[0] if row else None
                    marker = json.loads(row[1]).get("account_bound") is True if row else False
                    if marker or "account_bindings" in tables:
                        from peerhub.extensions import accounts
                        try:
                            account_binding = accounts.get_binding(db, peer_id, conn=conn) if dry_run else accounts.get_binding(db, peer_id)
                        except accounts.AccountError as exc:
                            raise PrespawnError("invalid account binding") from exc
                        if marker != (account_binding is not None):
                            raise PrespawnError("account marker and binding disagree; bind a new peer identity")
                        if account_binding is not None and adapter_ref != account_binding.provider:
                            raise PrespawnError("account adapter and binding disagree")
                elif tables:
                    raise StorageCorruptError("database does not contain the communication Core schema; v0 support is archived on branch legacy/v0-main-final")
        kind = peer_kind(adapter_ref, peer_id)
        target: RuntimeTarget
        execution_workspace: str | Path | None = None
        account_args: dict[str, Any] = {}
        if account_binding is not None:
            from peerhub.extensions.accounts import AccountRef
            if runtime is not None:
                raise PrespawnError("runtime injection is forbidden for an account-bound peer")
            account_args["account"] = AccountRef(account_binding.slot_id)
        if runtime is None:
            if kind is None:
                raise PeerNotFoundError(f"register this peer with --adapter {BUILTIN_PEER_CHOICES} before asking it")
            execution_workspace = workspace or Path.cwd()
            options: dict[str, Any] = dict(model=model, effort=effort, profile=profile, silence_timeout_s=silence_timeout_s,
                                           writable=writable, timeout_s=timeout_s, max_bytes=max_bytes, on_output=on_output,
                                           notice_alias=notice_alias)
            target = (API_TARGETS[kind](execution_workspace, **options) if kind in API_TARGETS
                      else CliRuntimeTarget(kind, execution_workspace, **options, **account_args))
        else:
            target = runtime
        rid = request_id or uuid.uuid4().hex
        sid = stream_id or cast(str, default_stream(peer_id, rid, stateless=getattr(target, "stateless", False)))  # rid is known here
        key = f"{_ASK_KEY_PREFIX}{rid}"
        prompt_metadata = {"runtime_kind": target.runtime_kind, "binding": target.binding(), "session_selector": selector}
        generation_path = db.parent / "workspace.generation"
        identity_metadata = {"adapter_fingerprint": target.fingerprint(),
                             "workspace_generation": generation_path.read_text(encoding="utf-8") if generation_path.is_file() else None}
        settled: sqlite3.Row | None = None
        previous: sqlite3.Row | None = None
        # Validate immutable scoped request identity before inspecting cached execution,
        # and return the original durable result without constructing Bridge or a claim.
        if has_store_data:
            with storage_errors(), read_store() as conn:
                conn.row_factory = sqlite3.Row
                previous = _request_record(conn, sid, author, key)
                _refuse_second_ask(conn, target, sid, author, key)
                if previous is not None:
                    previous_metadata = json.loads(previous["metadata_json"])
                    base_metadata = {k: v for k, v in previous_metadata.items() if k not in ("session_selector", *identity_metadata)}
                    stored_binding = base_metadata.pop("binding", None)
                    expected_base = {"runtime_kind": target.runtime_kind}
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
                        settled = _settled_delivery(conn, sid, peer_id, previous["record_id"])
                    else:
                        settled = None
                    current_binding = prompt_metadata["binding"]
                    if stored_binding != current_binding and not (
                        settled is not None and runtime is None and kind == "cx" and execution_workspace is not None
                        and _packaged_cx_binding_change(stored_binding, current_binding, execution_workspace, model, effort)
                    ):
                        raise IdempotencyConflictError(f"request {rid!r} conflicts with its stored identity")
                    if settled is not None and not dry_run:
                        replay = _read_delivery_result(conn, settled, peer_id)
                        replay.update(request_id=rid, stream_id=sid, peer_id=peer_id, requested_record_id=previous["record_id"])
                        return replay
        validate_policy: Callable[[], None] | None = getattr(target, "validate_execution_policy", None)
        if settled is None and callable(validate_policy):
            validate_policy()
        if dry_run:
            session_handle = None
            stream_exists = False
            if has_store_data:
                with storage_errors(), read_store() as conn:
                    stream = conn.execute("SELECT stream_id, state FROM streams WHERE stream_id=?", (sid,)).fetchone()
                    stream_exists = stream is not None
                    if settled is None:
                        Bridge.check_preview_claim(conn, tables, peer_id, sid, identity_metadata["workspace_generation"])
                        if account_binding is not None:
                            from peerhub.extensions import accounts
                            try:
                                accounts.validate_launch(db, account_binding, workspace or Path.cwd(), conn=conn)
                            except (accounts.AccountError, PrespawnError) as exc:
                                return _account_failure(rid, sid, peer_id, exc)
                        generation = identity_metadata["workspace_generation"]
                        preflight_exact = session is not None and previous is None and (
                            "bridge_deliveries" not in tables or not Bridge.has_unsettled_delivery(conn, peer_id, sid))
                        if preflight_exact:
                            session_handle = Bridge.preview_session(conn, tables, target, peer_id, sid, generation, selector, previous)
                        _check_stream_peer(conn, sid, peer_id)
                        if stream is not None and previous is None:
                            CoreStore.check_append_stream(stream)
                        blocked = Bridge.preview_gate(conn, tables, peer_id, sid)
                        if blocked is not None:
                            failure = asdict(blocked)
                            failure.update(request_id=rid, stream_id=sid, peer_id=peer_id, response=None)
                            _mark_progress(failure, previous["record_id"] if previous is not None else None)
                            _set_ask_error(failure)
                            return failure
                        if not preflight_exact:
                            session_handle = Bridge.preview_session(conn, tables, target, peer_id, sid, generation, selector, previous)
            elif selector.startswith("sess:"):
                Bridge.check_exact_session(None, target, peer_id, sid, None, time.time())
            settings = cast(CliRuntimeTarget | ApiRuntimeTarget, target).session_binding()
            return dict(peer_id=peer_id, **settings, timeout_seconds=timeout_s,
                        silence_timeout_seconds=silence_timeout_s, workspace=str(Path(workspace or Path.cwd()).resolve()),
                        store_exists=db_exists, stream_id=sid, stream_exists=stream_exists,
                        session_mode=None if settled is not None else ("resumed" if session_handle is not None else "fresh"),
                        session_handle=session_handle,
                        request_id=rid, replay=settled is not None)
    limits = None
    if on_stream_budget is not None and not fresh and not getattr(target, "stateless", False):
        from peerhub.extensions.adapters.profiles import DEFAULT_STREAM_BUDGET, stream_budget
        try:
            limits = stream_budget(workspace or Path.cwd())
        except (OSError, ValueError):
            limits = DEFAULT_STREAM_BUDGET  # advisory settings cannot prevent an otherwise valid ask
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
    caller = detect_caller(query_file=query_file, workspace=execution_workspace)
    bridge = Bridge(store, claims, owner_id=f"ask-{uuid.uuid4().hex}", lease_sec=lease_sec, caller=caller,
                    stream_budget=limits, on_stream_budget=on_stream_budget)
    token = claims.acquire(peer_id, sid, bridge.owner_id, lease_sec)
    started_at = time.monotonic()
    try:
        if account_binding is not None:
            from peerhub.extensions import accounts
            try:
                snapshot = accounts.validate_launch(db, account_binding, workspace or Path.cwd())
                cast(CliRuntimeTarget, target).bind_account_env(snapshot)
            except (accounts.AccountError, PrespawnError) as exc:
                return _account_failure(rid, sid, peer_id, exc)
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            previous = conn.execute("SELECT created_at FROM records WHERE stream_id=? AND author_peer_id=? AND idempotency_key=?",
                                    (sid, author, key)).fetchone()
        def prompt_guard(conn: sqlite3.Connection, **scope: Any) -> None:
            if scope["stream_id"] != token.stream_id:
                raise ClaimScopeError("prompt and delivery claim must share a Stream")
            claims.check(conn, token)
            _refuse_second_ask(conn, target, sid, author, key)  # again under the claim: the stream is reserved before anything is sent
            _check_stream_peer(conn, sid, peer_id)
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
            if (before_dispatch is not None and runtime is None and account_binding is None
                    and kind in ("ag", "cc", "cx") and ALIASES.get(peer_id) == kind):
                try:
                    before_dispatch(db, kind)
                except Exception:
                    pass  # Advisory collection must not strand the persisted prompt.
            result = asdict(bridge.run_cycle(token, target))
        if result.get("delivery_id") is not None:
            with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                delivered = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (result["delivery_id"],)).fetchone()
                if delivered is not None and delivered["acked"] and delivered["result_json"] and delivered["certainty"] in ("NOT_STARTED", "STARTED", "TERMINAL"):
                    result.update(_read_delivery_result(conn, delivered, peer_id))
                    if not terminal and not refused and delivered["certainty"] == "TERMINAL" and result.get("status") != "denied_empty":
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
        _mark_denied_empty(result, peer_id)
        _mark_progress(result, rec.record_id)
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
        _set_ask_error(result)
        if account_binding is not None:
            result.setdefault("env_scrubbed", list(cast(CliRuntimeTarget, target).env_scrubbed))
        return result
    finally:
        try:
            claims.release(token)
        except StaleClaimError:
            pass  # expiry/takeover cannot release a new owner's claim
