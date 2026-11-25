"""Project monitor ask activity, caller liveness, and lead summaries."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, cast

from peerhub.extensions.liveness import ORPHAN_RUNNING, PROCESSES_DEAD, ask_alive, ask_process_state
from peerhub.extensions.monitor_queries import (
    ACTIVITY_SCOPE_CAP,
    ALERT_WINDOW_S,
    PRESTART_KINDS,
    START_KINDS,
    SUPERSEDED_CAP,
    TEXT_MAX_CHARS,
    activity_rows,
)
from peerhub.extensions.monitor_sessions import public_telemetry
from peerhub.extensions.monitor_values import (
    activity_lines,
    display_text,
    finite_number,
    fit_line,
    iso_seconds,
    public_binding_value,
)


LONG_TEXT = "(long text)"

_ERROR_KINDS = ("prespawn_failure", "request_rejected", "refusal_detected", "control_halt", "session_unavailable")  # certainly not run
_UNSURE_KINDS = ("no_terminal", "start_uncertain", "session_tainted", "invocation_unresolved_recovered", "fenced_before_invoke",
                 "fenced_mid_delivery", "stale_terminal_callback", "terminal")  # plus every runtime_* kind: the provider may have run
BAD_ASK_STATES = ("empty", "error", "unsure")


def _workspace_generation(db_path: str) -> tuple[str | None, str | None]:
    """The lineage of this store, read exactly as `diag running` reads it (diag_running.py); no file means every generation matches."""
    path = Path(db_path).parent / "workspace.generation"
    try:
        return (path.read_text(encoding="utf-8") if path.is_file() else None), None
    except OSError:
        return None, "workspace.generation unreadable"


def _this_host() -> str | None:
    """The `host` value of a caller row written on this machine (frozen caller schema v1); the writer's helper when present."""
    try:
        from peerhub.extensions.caller import host_id
        return host_id()
    except ImportError:
        return f"{platform.node()}|{sys.platform}"[:255]


def _this_ns() -> str | None:
    try:
        from peerhub.extensions.caller import pid_namespace
        return pid_namespace()
    except ImportError:
        pass
    if not sys.platform.startswith("linux"):
        return "-"
    try:
        return os.readlink("/proc/self/ns/pid")
    except OSError:
        return None


def _process_created(pid: int) -> float | str | None:
    """Creation time of `pid` on this host, None when no such process exists, "denied" when it cannot be read."""
    try:
        import psutil  # pyright: ignore[reportMissingModuleSource]
    except ImportError:
        return "denied"
    try:
        return float(psutil.Process(pid).create_time())
    except psutil.NoSuchProcess:
        return None
    except (psutil.Error, OSError, ValueError, OverflowError):
        return "denied"


def _caller(raw: object) -> tuple[dict[str, Any] | None, bool]:
    """A valid caller detail (frozen schema v1) or None; the flag says a row was present but unreadable."""
    if raw is None:
        return None, False
    try:
        parsed: object = json.loads(raw) if isinstance(raw, str) else None
    except (ValueError, RecursionError):  # RecursionError: a deeply nested detail
        return None, True
    if not isinstance(parsed, dict):
        return None, True
    item = cast(dict[str, Any], parsed)
    optional_text = ("session", "ns", "batch", "task")
    pid, created = item.get("ask_pid"), item.get("ask_created")
    valid = (item.get("v") == 1 and item.get("tool") in ("cc", "?") and isinstance(item.get("label"), str) and item["label"]
             and isinstance(item.get("host"), str) and item["host"]
             and all(item.get(key) is None or isinstance(item.get(key), str) for key in optional_text)
             and isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
             and (created is None or (isinstance(created, (int, float)) and not isinstance(created, bool))))
    if not valid:
        return None, True
    item["ask_created"] = finite_number(created)  # a non-finite or overflowing creation time is unknown (None)
    return item, False


def _lead(caller: dict[str, Any], secrets: list[str]) -> dict[str, Any]:
    """A caller's lead; `key` (the full digest) groups and merges leads and is removed before output (#376 C8)."""
    session = caller.get("session")
    basis = session if session else f"{caller['host']}:{caller['ask_pid']}:{caller.get('ask_created')}"
    key = hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()  # a lone surrogate never raises
    return {"label": display_text(caller["label"], 40, secrets) or "?", "tool": caller["tool"], "id": key[:8], "key": key}


def _binding_fields(binding: object) -> tuple[str | None, str | None, str | None]:
    from peerhub.extensions.diag_running import _profile  # pyright: ignore[reportPrivateUsage]
    if not isinstance(binding, str) or not binding:
        return None, None, None
    parts = binding.split("#", 1)[0].split("/")
    model, effort = (parts[1], parts[2]) if len(parts) == 3 else (parts[0], parts[1]) if len(parts) == 2 else (None, None)
    return public_binding_value(_profile(binding)), public_binding_value(model), public_binding_value(effort)


def _liveness_of(caller: dict[str, Any], liveness: Callable[..., object] | None,
                 probe: Callable[[int], object]) -> tuple[bool | None, str | None]:
    alive: object = None
    pid = caller.get("caller_pid", caller["ask_pid"])
    created = caller.get("caller_created", caller.get("ask_created"))
    if liveness is not None:
        try:
            if liveness is ask_alive:
                stable = caller.get("caller_identity", caller.get("ask_identity"))
                alive = ask_alive(caller["host"], caller.get("ns"), pid, created, stable=stable)
            else:
                alive = liveness(caller["host"], caller.get("ns"), pid, created)
        except Exception:  # noqa: BLE001 - liveness is a best-effort source: any failure is "unknown", never a group error
            alive = None
    if isinstance(alive, bool):
        return alive, None
    if caller["host"] != _this_host():
        return None, "other host"
    here = _this_ns()
    if caller.get("ns") is None or here is None or caller.get("ns") != here:
        return None, "other namespace"
    if liveness is None or created is None:
        return None, "not accessible"
    try:
        found = probe(int(pid))
    except Exception:  # noqa: BLE001 - see above
        found = "denied"
    return None, "pid reused or clock stepped" if finite_number(found) is not None else "not accessible"


def _ask_row(r: sqlite3.Row, now: float, *, gen_reason: str | None, human: bool, text_on: bool, secrets: list[str],
             liveness: Callable[..., object] | None, probe: Callable[[int], object]) -> dict[str, Any]:
    from peerhub.extensions.diag_running import _started_at  # pyright: ignore[reportPrivateUsage]
    reasons: list[str] = []
    superseded = bool(r["sup"])
    expires_at = float(r["expires_at"])
    live = expires_at > now
    gen_match = r["claim_generation"] == r["c_generation"]
    last = r["last_kind"] if isinstance(r["last_kind"], str) else None
    note: str | None = None
    caller, caller_bad = _caller(r["caller_detail"])
    if caller_bad:
        reasons.append("caller unreadable")
    if superseded:
        state, note = "unsure", "superseded"
    elif r["abandonment"] is not None and r["result_ok"] is None:
        state, note = PROCESSES_DEAD, "result may be lost"
    elif r["certainty"] == "MAY_HAVE_STARTED":
        state = "unsure"
        note = last if last is not None and (last in _UNSURE_KINDS or last.startswith("runtime_")) else "may have started"
    elif r["result_ok"] == 0:
        state = "unsure"
        reasons.append("result unreadable")
    elif r["result_ok"] == 1:
        if r["error_type"] not in (None, "null"):
            state, note = "error", display_text(r["error_code"], 40, secrets) or "error"
        elif (r["empty_type"] == "true" or r["response_type"] in (None, "null")
              or (r["response_type"] == "text" and not r["reply_nonblank"])):
            state = "empty"
            denied: list[str] = []
            if isinstance(r["denied_json"], str):
                try:
                    raw_denied: object = json.loads(r["denied_json"])
                except (ValueError, RecursionError):
                    raw_denied = None
                if isinstance(raw_denied, list):
                    denied = [shown for shown in (display_text(item, 40, secrets) for item in cast(list[object], raw_denied)) if shown]
            note = fit_line("denied: " + ", ".join(denied), 120) if denied else None
        else:
            state = "ok"
    elif last in _ERROR_KINDS:
        state, note = "error", last
    elif last is not None and (last in _UNSURE_KINDS or last.startswith("runtime_")):
        state, note = "unsure", display_text(last, 40, secrets)
    elif last is None or last in START_KINDS or last in PRESTART_KINDS:
        if not r["wsgen_match"]:  # a claim of another workspace generation (a restored store), live or expired: never a2
            state, note = "unsure", "other generation"
        elif live:
            state, note = (("unsure", "generation unknown") if gen_reason else
                           ("unsure", "claim taken") if not gen_match else ("run", None))
        elif last in START_KINDS:
            state = "overdue"
        elif last is None:
            state, note = "unsure", "no evidence"
        else:
            state, note = "error", "not started"
    else:
        state, note = "unsure", display_text(last, 40, secrets)
    try:
        started_at = _started_at(r["start_detail"])
    except (OverflowError, RecursionError):  # an integer of 2**1024 or more; a deeply nested detail
        started_at = None
    if r["start_detail"] is not None and started_at is None:
        reasons.append("start time unreadable")
    terminal_at = iso_seconds(r["terminal_at"])
    lease_based = state in ("run", "overdue")
    may_have_orphan = (r["result_ok"] is None and r["abandonment"] is None and r["provider_detail"] is not None
                       and (r["certainty"] in ("STARTED", "MAY_HAVE_STARTED") or last in START_KINDS))
    if (((lease_based and gen_match) or may_have_orphan) and r["wsgen_match"] and gen_reason is None
            and (liveness is None or (caller is not None and "caller_pid" in caller))):
        process_state = ask_process_state(r["caller_detail"], r["provider_detail"])
        if process_state == PROCESSES_DEAD and lease_based:
            state, note = PROCESSES_DEAD, "result may be lost"
        elif process_state == ORPHAN_RUNNING:
            state, note = ORPHAN_RUNNING, "provider process still alive"
    # An overdue row is in the running set only for ALERT_WINDOW_S after its lease expired; then it is an ended (bad) row.
    open_row = state in ("run", ORPHAN_RUNNING) or (state == "overdue" and expires_at >= now - ALERT_WINDOW_S)
    ended_at: float | None = None
    ended_exact: bool | None = None
    if not open_row:
        if state == PROCESSES_DEAD:
            ended_at, ended_exact = now, False  # observed dead by this time; exact exit time is unknown
            if isinstance(r["abandonment"], str):
                try:
                    end_detail: object = json.loads(r["abandonment"])
                    if isinstance(end_detail, dict):
                        observed = finite_number(cast(dict[str, object], end_detail).get("observed_at_s"))
                        if observed is not None:
                            ended_at = observed
                except (ValueError, RecursionError):
                    pass
        elif terminal_at is not None:
            ended_at, ended_exact = terminal_at, True
        elif not live:  # the release time of the claim: exact for the claim that ran it, else the latest possible end
            ended_at, ended_exact = expires_at, gen_match
    age_s = round(max(0.0, now - ended_at), 3) if ended_at is not None and ended_exact else None
    if open_row:
        elapsed_s = None if started_at is None else round(max(0.0, now - started_at), 3)
    else:
        elapsed_s = (round(max(0.0, ended_at - started_at), 3)
                     if started_at is not None and ended_at is not None and ended_exact else None)
    alive: bool | None = None
    alive_reason: str | None = None
    if state == ORPHAN_RUNNING:
        alive, alive_reason = False, "caller and wrapper gone"
    elif open_row:
        alive, alive_reason = (None, "no caller row") if caller is None else _liveness_of(caller, liveness or ask_alive, probe)
    profile, model, effort = _binding_fields(r["binding"])
    stream = display_text(r["stream_id"], 120, secrets) or "?"
    batch, task = (caller.get("batch"), caller.get("task")) if caller is not None else (None, None)
    name = (f"{display_text(batch, 40, secrets)}/{display_text(task, 40, secrets)}" if isinstance(batch, str) and batch
            and isinstance(task, str) and task else stream)
    lines: dict[str, str | None] = {"ask_line": None, "ask_line_long": None, "reply_line": None, "reply_line_long": None}
    if text_on and r["want_text"]:
        if r["ask_len"] is None:
            reasons.append("request text unreadable" if r["has_record"] else "no request record")
        elif int(r["ask_len"]) > TEXT_MAX_CHARS:
            lines["ask_line"] = lines["ask_line_long"] = LONG_TEXT
        elif isinstance(r["ask_text"], str) and len(r["ask_text"]) > TEXT_MAX_CHARS:  # SQLite length() stops at a NUL
            lines["ask_line"] = lines["ask_line_long"] = LONG_TEXT
        elif isinstance(r["ask_text"], str):
            lines["ask_line"], lines["ask_line_long"] = activity_lines(r["ask_text"], last=False, secrets=secrets)
        if (r["reply_len"] is not None and int(r["reply_len"]) > TEXT_MAX_CHARS) or (
                isinstance(r["reply_text"], str) and len(r["reply_text"]) > TEXT_MAX_CHARS):
            lines["reply_line"] = lines["reply_line_long"] = LONG_TEXT
        elif isinstance(r["reply_text"], str):
            lines["reply_line"], lines["reply_line_long"] = activity_lines(r["reply_text"], last=True, secrets=secrets)
    tokens = ({"input": r["usage_in"], "output": r["usage_out"], "cache_read": r["usage_cache_read"],
               "cache_write": r["usage_cache_write"]}
              if r["usage_in"] is not None or r["usage_out"] is not None else None)
    row: dict[str, Any] = {
        "state": state, "superseded": superseded, "alive": alive, "alive_reason": alive_reason,
        "peer": display_text(r["peer_id"], 64, secrets) or "?", "profile": profile, "model": model, "effort": effort,
        "stream": stream, "name": name, "lead": _lead(caller, secrets) if caller is not None else None,
        "started_at": started_at, "elapsed_s": elapsed_s, "ended_at": ended_at, "ended_exact": ended_exact, "age_s": age_s,
        "lease_left_s": round(expires_at - now, 3) if open_row else None, "tokens": tokens,
        "ask_line": lines["ask_line"], "reply_line": lines["reply_line"],
        "ask_line_long": lines["ask_line_long"], "reply_line_long": lines["reply_line_long"],
        "note": note, "reasons": reasons, "session": _ask_session(r),
    }
    if state == PROCESSES_DEAD:
        row.update(certainty="UNKNOWN")
    if human:
        if text_on and r["want_text"] and caller is not None and isinstance(caller.get("query_file"), str):
            row["_caller_file"] = {key: caller.get(key) for key in ("query_file", "workspace", "host")}
        if open_row:
            lease = finite_number(r["lease_sec"])
            row["_lease_age_s"] = (max(0.0, now - (expires_at - lease))
                                   if state == "run" and lease is not None and lease > 0 else None)
        else:
            ask_id = r["record_id"]
            if isinstance(ask_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", ask_id):
                row["_ask_id"] = ask_id
    return row


def _ask_session(r: sqlite3.Row) -> dict[str, Any] | None:
    """The newest session of the ask's stream and peer, as it is now (#412): its generation, its mode (`reused` when it was
    reused at least once, else `fresh`), `uses` (reused and fresh calls), and the context estimate of its latest receipt.
    None for an ask without a joined session (a stream that never had one, or not among the newest ended asks) and for a
    tombstoned one."""
    if "ses_generation" not in r.keys() or r["ses_generation"] is None:
        return None
    if r["ses_tombstoned_at"] is not None or r["ses_state"] == "TOMBSTONED":
        return None
    generation, provider = int(r["ses_generation"]), str(r["ses_provider"])
    context = public_telemetry(r["ses_detail"], handle=str(r["ses_handle"]), generation=generation,
                               provider=provider)["context"]
    reuse, fresh = r["ses_reuse"], r["ses_fresh"]
    return {"generation": generation, "provider": provider,
            "execution_mode": "reused" if reuse and reuse > 0 else "fresh" if fresh and fresh > 0 else None,
            "uses": None if reuse is None or fresh is None else int(reuse) + int(fresh),
            "context": {key: context.get(key) for key in ("state", "percent", "occupancy_tokens", "window_tokens",
                                                          "observed_at")}}


def _activity_data(rows: list[sqlite3.Row], now: float, *, gen_reason: str | None, human: bool, text_on: bool,
                   liveness: Callable[..., object] | None, probe: Callable[[int], object]) -> dict[str, Any]:
    """Rows of the activity group. Lead `asks_24h` counts the streams (scopes) of that lead with an ask in the last 24 hours,
    one per stream and peer, not every ask: older asks of a stream are replaced by its newest one (superseded rows excluded)."""
    from peerhub.extensions.adapters.sanitize import env_secrets
    secrets = env_secrets()
    built = [_ask_row(r, now, gen_reason=gen_reason, human=human, text_on=text_on, secrets=secrets,
                      liveness=liveness, probe=probe) for r in rows]
    main = [a for a in built if not a["superseded"]]
    running: list[dict[str, Any]] = []
    ended: list[dict[str, Any]] = []
    for a in main:
        (running if a["ended_at"] is None and a["state"] in ("run", "overdue", ORPHAN_RUNNING) else ended).append(a)
    running.sort(key=lambda a: (0 if a["state"] == "overdue" or a["alive"] is False else 1,
                                -(a["elapsed_s"] if a["elapsed_s"] is not None else -1.0)))
    window_start = now - ALERT_WINDOW_S
    ended.sort(key=lambda a: (0 if a["state"] in BAD_ASK_STATES and a["note"] != "other generation"  # A4: a restored lineage is not news
                              and a["ended_at"] is not None and a["ended_at"] >= window_start else 1,
                              -(a["ended_at"] if a["ended_at"] is not None else now)))
    asks = running + ended + [a for a in built if a["superseded"]]
    leads: dict[str, dict[str, Any]] = {}
    for a in asks:
        lead = a["lead"]
        if lead is None or a["superseded"]:
            continue
        entry = leads.setdefault(lead["key"], {**lead, "running": 0, "asks_24h": 0, "last_ask_at": None})
        entry["asks_24h"] += 1
        if a in running:
            entry["running"] += 1
        stamp = a["started_at"] if a["started_at"] is not None else a["ended_at"]
        if stamp is not None and (entry["last_ask_at"] is None or stamp > entry["last_ask_at"]):
            entry["last_ask_at"] = stamp
    scope_total = int(rows[0]["scope_total"]) if rows else 0
    sup_total = int(rows[0]["sup_total"] or 0) if rows else 0
    omitted = max(0, sup_total - SUPERSEDED_CAP)
    return {
        "asks": asks,
        "leads": list(leads.values()),  # still keyed: `_merge_leads` merges, orders and removes the digest
        "counts": {"running": len(running), "overdue": sum(1 for a in running if a["state"] == "overdue"),
                   "by_peer": dict(sorted(Counter(a["peer"] for a in running).items())),
                   "leads": len({a["lead"]["key"] for a in running if a["lead"] is not None})},
        "sample_size": len(asks), "truncated": scope_total > ACTIVITY_SCOPE_CAP or omitted > 0,
        "scopes_total": scope_total, "superseded_omitted": omitted, "reason": gen_reason,
    }


LeadReader = Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]]
LEAD_CONTEXT_MARK_PCT = 80  # a lead session at this share of its model's maximum input is marked `!` and, when alive, first
_LEAD_READ_ERROR = {"state": "error", "unreadable": 0, "not_read": 0}


def _lead_sessions(lead_reader: LeadReader | None, **facts: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """(sessions, read) of the injected reader (#376 3.3); none and no read object without a reader. A reader that raises
    or returns anything but a list of sessions with a `key` and a valid read object gives no sessions and `error`."""
    if lead_reader is None:
        return [], None
    try:
        result: object = lead_reader(**facts)
        sessions, read = cast(tuple[object, object], result)
    except Exception:  # noqa: BLE001 - the lead reader is a best-effort source: any failure is `error`, never a frame failure
        return [], dict(_LEAD_READ_ERROR)
    if _valid_lead_read(read) and isinstance(sessions, list) and all(
            isinstance(item, dict) and isinstance(cast(dict[str, Any], item).get("key"), str) for item in cast(list[object], sessions)):
        return cast(list[dict[str, Any]], sessions), cast(dict[str, Any], read)
    return [], dict(_LEAD_READ_ERROR)


def _valid_lead_read(read: object) -> bool:
    """A read object: `state` read, partial or error, and `unreadable` and `not_read` ints (not bool) of 0 or more."""
    item = cast(dict[str, Any], read) if isinstance(read, dict) else {}
    return item.get("state") in ("read", "partial", "error") and all(
        isinstance(item.get(key), int) and not isinstance(item.get(key), bool) and item[key] >= 0
        for key in ("unreadable", "not_read"))


def _merge_leads(data: dict[str, Any], sessions: list[dict[str, Any]], read: dict[str, Any] | None) -> None:
    """Merge the lead sessions into the leads on the full digest, order the leads and remove the digest (#376 3.3).

    A lead gets `session` (or None); a session without caller rows is a lead of its own, labelled by its name, else its
    folder, else `?`. Order: a session at LEAD_CONTEXT_MARK_PCT or more that is alive; leads with a session; more running
    asks; the newer of the last activity and the last ask; the id."""
    by_key = {item["key"]: {k: v for k, v in item.items() if k != "key"} for item in sessions}
    leads: list[dict[str, Any]] = data["leads"]
    for lead in leads:
        lead["session"] = by_key.pop(lead.pop("key"), None)
    leads += [{"label": item.get("name") or item.get("folder") or "?", "tool": "cc", "id": key[:8], "running": 0,
               "asks_24h": 0, "last_ask_at": None, "session": item} for key, item in by_key.items()]

    def rank(lead: dict[str, Any]) -> tuple[int, int, int, float, str]:
        item: dict[str, Any] = lead["session"] or {}
        pct = finite_number(item.get("context_pct"))
        marked = pct is not None and pct >= LEAD_CONTEXT_MARK_PCT and item.get("alive") is True
        newest = max(finite_number(item.get("active_at")) or 0.0, finite_number(lead["last_ask_at"]) or 0.0)
        return 0 if marked else 1, 0 if lead["session"] else 1, -int(lead["running"]), -newest, str(lead["id"])

    leads.sort(key=rank)
    for ask in data["asks"]:
        if ask["lead"] is not None:
            ask["lead"].pop("key", None)
    data["leads_read"] = read


def read_activity_group(conn: sqlite3.Connection, db_path: str, now: float, *, human: bool, text_on: bool,
                        liveness: Callable[..., object] | None, process_probe: Callable[[int], object] | None,
                        output: dict[str, Any], errors: dict[str, str]) -> dict[str, Any] | None:
    activity_data: dict[str, Any] | None = None
    generation, generation_reason = _workspace_generation(db_path)
    try:
        rows = activity_rows(conn, now, generation, text_on)
        activity_data = _activity_data(rows, now, gen_reason=generation_reason, human=human, text_on=text_on,
                                       liveness=liveness, probe=process_probe or _process_created)
        output["activity"] = {"status": "OK", "data": activity_data}
    except sqlite3.Error as exc:
        errors["activity"] = type(exc).__name__
        output["activity"] = {"status": "ERROR", "data": None}
    return activity_data


def merge_activity_leads(data: dict[str, Any], lead_reader: LeadReader | None, *, now: float, text_on: bool,
                         process_probe: Callable[[int], object] | None) -> None:
    from peerhub.extensions.adapters.sanitize import env_secrets
    sessions, leads_read = _lead_sessions(lead_reader, now=now, text_on=text_on, secrets=env_secrets(),
                                          created=process_probe or _process_created)
    _merge_leads(data, sessions, leads_read)
