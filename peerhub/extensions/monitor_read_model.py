"""Read-only, bounded aggregate queries over canonical monitor projections."""
from __future__ import annotations

import hashlib
import os
import platform
import sqlite3
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, cast

from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
from peerhub.extensions.monitor_queries import (
    ACCOUNT_ROW_CAP,
    ACTIVITY_SCOPE_CAP,
    ALERT_WINDOW_S,
    PEER_CAP,
    PRESTART_KINDS,
    START_KINDS,
    SUPERSEDED_CAP,
    TEXT_MAX_CHARS,
    activity_rows,
    capacity_rows,
    delivery_scope_rows,
    last_measured_account_rows,
    latest_account_quota_row,
    no_observation_tables,
    peer_rows,
    quota_age_rows,
    quota_evidence_rows,
    recent_quota_payloads,
    session_rows,
)
from peerhub.extensions.observation_model import (
    LOW_QUOTA_FRACTION,
    MEASUREMENT_KEYS,
    ABSENT_FACT_REASONS,
    NOT_REPORTED_REASON,
    FreshnessPolicy,
    account_text as _account_text,  # stored account strings are display data: re-sanitized when read back
    changed_account_plan,
    oldest_measured_quota_age,
    valid_epoch_seconds,
    ObservationCorruptError,
    evaluate as evaluate_observation,
    row_to_observation,
)
from peerhub.extensions.quota_projection import project_exhaustion


def mapped_quota(configured_pools: list[str], peer_observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Peer quota over its profile's configured pools: the freshest row per pool, the most constrained wins.

    A pool the provider explicitly did not report (ABSENT "not reported by provider") is skipped, never
    limiting; if every configured pool is not reported the peer is UNKNOWN, never 100% free. Anything else
    that is not a fresh valid measurement makes the peer INCOMPLETE (fail closed)."""
    mapped_measured: list[tuple[float, dict[str, Any]]] = []
    not_reported_pools = 0
    for p_req in configured_pools:
        matching_obs = [
            o for o in peer_observations
            if (str(o.get("pool_ref") or o.get("pool_kind") or "").split(":")[-1]) == p_req
        ]
        if not matching_obs:
            return _incomplete_quota()
        best_matching = max(matching_obs, key=lambda o: int(o.get("effective_at_us") or 0))  # freshest, not first
        if (best_matching.get("state") == "ABSENT"
                and str(best_matching.get("reason") or "").startswith(ABSENT_FACT_REASONS)):
            not_reported_pools += 1  # the provider says this window does not exist or is disabled: never limiting
            continue
        raw_m_val = best_matching.get("measurements")
        q_m_dict = cast(dict[str, Any], raw_m_val) if isinstance(raw_m_val, dict) else {}
        rem_val = q_m_dict.get("remaining_fraction")
        is_valid = (
            isinstance(rem_val, (int, float)) and not isinstance(rem_val, bool)
            and math.isfinite(float(rem_val)) and 0.0 <= float(rem_val) <= 1.0
        )
        if best_matching.get("state") != "MEASURED" or not is_valid:
            return _incomplete_quota()
        mapped_measured.append((float(cast(float, rem_val)), best_matching))
    if mapped_measured:
        lowest_rem, best_obs = min(mapped_measured, key=lambda x: (x[0], str(x[1].get("pool_ref") or "")))
        return {
            "pool_ref": best_obs.get("pool_ref") or best_obs.get("pool_kind"),
            "remaining_fraction": lowest_rem,
            "percent": round(lowest_rem * 100),
            "state": "MEASURED",
            "source": best_obs.get("source"),
            "age_seconds": best_obs.get("age_seconds"),
            "scope": "mapped",
        }
    if not_reported_pools:
        return {
            "pool_ref": "not_reported",
            "remaining_fraction": None,
            "percent": None,
            "state": "UNKNOWN",
            "source": "derived",
            "age_seconds": None,
            "scope": "mapped",
        }
    return None


# One poller is one probe source even when its rows carry different refs: ag falls back from /usage to the statusline log inside
# a run, and a poller that raised is stored by refresh_quota as source `quota_probe:<kind>` without an evidence_ref.
# Deliberately NOT mapped (they stay visible after a later success): agy_usage_cwd_refused, agy_usage_consumed_tokens,
# agy_usage_unverifiable_tokens, agy_usage_not_a_command and probe:ag:conflict. They are safety signals about that run (a refusal,
# possible token spend, a vocabulary conflict), not transient probe failures.
_PROBE_SOURCE_FAMILY = {"probe:ag:statusline": "probe:ag:usage", "quota_probe:cx": "probe:cx:app-server",
                        "quota_probe:cc": "probe:cc:usage", "quota_probe:ag": "probe:ag:usage"}


def _probe_source(payload: dict[str, Any], source: str) -> str:
    ref = str(payload.get("evidence_ref") or "") or source
    return _PROBE_SOURCE_FAMILY.get(ref, ref)


def _superseded(item: dict[str, Any], newest_success_us: int) -> bool:
    """A peer-level (pool-less) quota failure is superseded by a strictly newer MEASURED pool row of the same peer (the account
    key: quota rows are stored per peer) and probe source. Successful probes write pool rows only, so they never replace the
    failure row themselves. Other sources, other peers and same-instant rows of one partial run never hide it; history is kept."""
    return (item["kind"] == "quota" and item["pool_ref"] is None and item["stored_state"] != "MEASURED"
            and newest_success_us > int(item["effective_at_us"]))


_ACCOUNT_TRI = {"reported", "n/a", "UNKNOWN"}
_ACCOUNT_ORDER = {"cc": 0, "cx": 1, "ag": 2}  # builtin peers first in the documented order, then the rest by id


def _account_item(observation: Any, freshness: Any, last_good: tuple[Any, Any] | None = None) -> dict[str, Any]:
    """One account observation -> the monitor's account fields. Only normalized stored fields are read (no identity exists to leak).

    A failed probe carries no account facts. When an earlier MEASURED reading exists (`last_good`: observation, freshness) it stays
    shown, forced to aged (STALE), with the failure as `probe_error`; otherwise every fact is UNKNOWN."""
    failed = freshness.stored_state.value != "MEASURED"
    probe_error = (_account_text(observation.payload.get("reason"), 40) or "unknown") if failed else None
    state_override = None
    if failed and last_good is not None:
        observation, freshness = last_good
        failed, state_override = False, "STALE"
    payload: dict[str, Any] = observation.payload
    ok = not failed
    login = payload.get("logged_in")
    expires = payload.get("login_token_expires_at")
    plan_status, expiry_status = payload.get("plan_status"), payload.get("login_expiry_status")
    plan = _account_text(payload.get("plan")) if ok and plan_status == "reported" else None
    expires_ok = ok and expiry_status == "reported" and valid_epoch_seconds(expires)  # re-validated: stored values may come from anywhere
    return {
        "peer_id": observation.subject_ref,
        "state": state_override or freshness.state.value,
        "stored_state": freshness.stored_state.value,
        "age_seconds": freshness.age_seconds,
        "ttl_seconds": freshness.ttl_seconds,
        "logged_in": login if ok and isinstance(login, bool) else "UNKNOWN",
        "plan": plan,
        "plan_status": "reported" if plan else (plan_status if ok and plan_status in {"n/a", "UNKNOWN"} else "UNKNOWN"),
        "login_token_expires_at": expires if expires_ok else None,
        "login_expiry_status": "reported" if expires_ok else ("n/a" if ok and expiry_status == "n/a" else "UNKNOWN"),
        "auth_method": _account_text(payload.get("auth_method")) if ok else None,
        "endpoint_access": "ok" if ok and payload.get("endpoint_access") == "ok" else None,
        "probe_error": probe_error,
    }


def _read_accounts(conn: sqlite3.Connection, latest_rows: list[sqlite3.Row], now: float) -> list[dict[str, Any]]:
    """Account items, builtin peers first. For a peer whose latest probe failed, one extra query fetches its last MEASURED reading."""
    def parsed(row: sqlite3.Row) -> tuple[Any, Any]:
        try:
            observation, capture_seq = row_to_observation(row)
        except ObservationCorruptError as exc:
            raise sqlite3.DataError(f"invalid {row['kind']} observation at capture_seq={row['capture_seq']}") from exc
        return observation, evaluate_observation(observation, capture_seq, round(now * 1_000_000), FreshnessPolicy())

    latest = [parsed(row) for row in latest_rows]
    failed = sorted({obs.subject_ref for obs, view in latest if view.stored_state.value != "MEASURED"})
    good: dict[str, tuple[Any, Any]] = {}
    if failed:
        for row in last_measured_account_rows(conn, failed):
            observation, view = parsed(row)
            good[observation.subject_ref] = (observation, view)
    items: list[dict[str, Any]] = []
    for obs, view in latest:
        item = _account_item(obs, view, good.get(obs.subject_ref))
        # Query the actual latest quota row, independently of the display's quota row cap.
        row = latest_account_quota_row(conn, obs.subject_ref)
        plan = changed_account_plan(obs, parsed(row)[0] if row is not None else None)
        if plan:
            item.update(plan=plan, plan_source="quota", plan_changed=True, state="STALE")
        items.append(item)
    return sorted(items, key=lambda a: (_ACCOUNT_ORDER.get(str(a["peer_id"]), len(_ACCOUNT_ORDER)), str(a["peer_id"])))


def _fill_unknown_plans(observations: list[dict[str, Any]], accounts: list[dict[str, Any]]) -> None:
    """A "not reported ... plan=unknown" pool note gets `display_plan` (+ `plan_source: "account"`) from a FRESH account observation of
    the same peer; the stored `reason` is never rewritten, a plan the quota probe itself reported is never replaced, and without a fresh
    account plan the note stays "unknown". The renderers read these fields, so every view agrees."""
    plans = {str(a["peer_id"]): str(a["plan"]) for a in accounts if a["state"] == "MEASURED" and a["plan_status"] == "reported" and a["plan"]}
    for item in observations:
        reason = item.get("reason")
        plan = plans.get(str(item["peer_id"]))
        if plan and item["pool_ref"] is not None and isinstance(reason, str) and reason.startswith(NOT_REPORTED_REASON) \
                and reason.endswith("plan=unknown"):
            item["display_plan"], item["plan_source"] = plan, "account"  # `reason` stays exactly as stored


def _quota_age_observations(conn: sqlite3.Connection, now: float) -> Iterator[dict[str, Any]]:
    """Supply quota age evidence for human frames without a capacity read."""
    read_us = round(now * 1_000_000)
    policy = FreshnessPolicy()
    for row in quota_age_rows(conn, read_us):
        try:
            observation, capture_seq = row_to_observation(row)
        except ObservationCorruptError:
            continue
        freshness = evaluate_observation(observation, capture_seq, read_us, policy)
        yield {"kind": observation.kind, "subject_ref": observation.subject_ref, "resource_pool_ref": observation.resource_pool_ref,
               "state": freshness.state.value, "age_seconds": freshness.age_seconds, "payload": observation.payload}


def _incomplete_quota() -> dict[str, Any]:
    return {
        "pool_ref": "incomplete",
        "remaining_fraction": None,
        "percent": None,
        "state": "INCOMPLETE",
        "source": "derived",
        "age_seconds": None,
        "scope": "mapped",
    }


def _safe_object(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str):
        return {}
    try:
        parsed: object = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}


_SAFE_MODEL = re.compile(r"[A-Za-z0-9._+-]{1,128}\Z")
_SECRET_LIKE_BINDING = re.compile(
    r"(?i)(?:\b(?:sk|pk|rk|ghp|gho|ghs|xox[abp])[-_][A-Za-z0-9_-]{8,}|"
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}|"
    r"\b(?:api[_-]?key|token|secret|password|credential)[_-][A-Za-z0-9_-]{8,}\b)"
)


def _public_binding_value(value: object) -> str | None:
    if isinstance(value, str) and _SAFE_MODEL.fullmatch(value) and not _SECRET_LIKE_BINDING.search(value):
        return value
    return None


def _current_profile(profile: object, workspace: Path) -> dict[str, Any]:
    if not isinstance(profile, str) or _public_binding_value(profile) is None:
        return {"basis": "current_config", "profile": None, "fields": None, "reason": "profile_unavailable"}
    from peerhub.extensions.adapters.profiles import inspect_profile
    try:
        current = inspect_profile(profile, workspace)
    except (OSError, ValueError):
        return {"basis": "current_config", "profile": profile, "fields": None, "reason": "profile_config_unavailable"}
    return {"basis": "current_config", **current}


def _monitor_workspace(db_path: str, workspace: str | Path | None) -> Path:
    if workspace is not None:
        return Path(workspace)
    database = Path(db_path).resolve()
    if database.parent.name == ".peerhub":
        return database.parent.parent
    if (database.parent / "workspace.generation").is_file():
        return database.parent
    return Path.cwd()


def context_estimate_age(observed_at: float | None, now: float | None) -> float | None:
    """Seconds since the context sample, clamped at zero for clock skew."""
    return None if observed_at is None or now is None else max(0.0, now - observed_at)


def public_telemetry(raw: object, *, handle: str, generation: int, provider: str) -> dict[str, Any]:
    """Validate and redact one session receipt for monitor and session inspection."""
    detail = _safe_object(raw)
    if (detail.get("schema_version") != 3 or detail.get("handle") != handle or
            detail.get("generation") != generation or detail.get("provider_kind") != provider):
        return {"configured_model": None, "resolved_model": None, "observed_at": None,
                "adapter_version": None, "provider_cli_version": None, "usage": None,
                "context": {"occupancy_tokens": None, "window_tokens": None, "percent": None,
                            "source": None, "observed_at": None, "window_reported_at": None,
                            "state": "UNKNOWN", "reason": "invalid_or_missing_receipt"}}
    def model(value: object) -> str | None:
        return value if isinstance(value, str) and _SAFE_MODEL.fullmatch(value) else None
    from peerhub.extensions.session_receipts import CONTEXT_SOURCE, context_estimate
    from peerhub.extensions.usage_normalization import validate_provider_usage
    usage_raw: object = detail.get("usage")
    public_usage: dict[str, Any] | None = None
    if isinstance(usage_raw, dict):
        usage = cast(dict[str, Any], usage_raw)
        candidate = {key: usage.get(key) for key in ("schema_version", "scope", "source", "reported", "normalized", "coverage")}
        checked = validate_provider_usage(candidate, provider)
        if checked == usage:
            public_usage = {key: usage.get(key) for key in ("schema_version", "scope", "attribution", "reported", "normalized", "coverage")}
    occupancy, window, pct = detail.get("last_input_tokens"), detail.get("context_window"), detail.get("context_pct")
    sample_at, window_at = detail.get("observed_at"), detail.get("window_reported_at")
    # One validated source (#376 P2): the cc adapter's reading of the ask's own stream, an ESTIMATE of the occupancy at the
    # end of that ask (`observed_at`). Every other source, provider or value that fails a check stays unknown.
    estimate = context_estimate(occupancy, window) if provider == "cc" and detail.get("source") == CONTEXT_SOURCE else None
    valid_context = (estimate is not None and _number(pct) == estimate[2]
                     and _number(sample_at) is not None and _number(window_at) is not None)
    has_context = any(detail.get(key) is not None for key in
                      ("last_input_tokens", "context_window", "context_pct", "context_sample"))
    tokens, size, percent = estimate if estimate is not None and valid_context else (None, None, None)
    context = {"occupancy_tokens": tokens, "window_tokens": size, "percent": percent,
               "source": model(detail.get("source")),
               "observed_at": sample_at if valid_context else None,
               "window_reported_at": window_at if valid_context else None,
               "state": "ESTIMATED" if valid_context else "UNKNOWN",
               "reason": None if valid_context else "unvalidated_context_source" if has_context else "no_context_measurement"}
    return {"configured_model": model(detail.get("configured_model")), "resolved_model": model(detail.get("resolved_model")),
            "adapter_version": _public_binding_value(detail.get("adapter_version")),
            "provider_cli_version": _public_binding_value(detail.get("provider_cli_version")),
            "observed_at": sample_at if isinstance(sample_at, (int, float)) and not isinstance(sample_at, bool) else None,
            "usage": public_usage, "context": context}


LINE_SHORT, LINE_LONG = 60, 200
TOKEN_EXPIRY_WARN_S = 48 * 3600
LONG_TEXT = "(long text)"

_ERROR_KINDS = ("prespawn_failure", "request_rejected", "refusal_detected", "control_halt", "session_unavailable")  # certainly not run
_UNSURE_KINDS = ("no_terminal", "start_uncertain", "session_tainted", "invocation_unresolved_recovered", "fenced_before_invoke",
                 "fenced_mid_delivery", "stale_terminal_callback", "terminal")  # plus every runtime_* kind: the provider may have run
_BAD_STATES = ("empty", "error", "unsure")


def _workspace_generation(db_path: str) -> tuple[str | None, str | None]:
    """The lineage of this store, read exactly as `diag running` reads it (diag_running.py); no file means every generation matches."""
    path = Path(db_path).parent / "workspace.generation"
    try:
        return (path.read_text(encoding="utf-8") if path.is_file() else None), None
    except OSError:
        return None, "workspace.generation unreadable"


# Control (Cc) and format (Cf) characters, except the line breaks LF and CR; a tab becomes a space. Format characters are listed
# explicitly (a test checks this table against unicodedata), so a secret split by a zero-width character is joined before redaction.
_FORMAT_RANGES = ((0x00AD, 0x00AD), (0x0600, 0x0605), (0x061C, 0x061C), (0x06DD, 0x06DD), (0x070F, 0x070F), (0x0890, 0x0891),
                  (0x08E2, 0x08E2), (0x180E, 0x180E), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2064), (0x2066, 0x206F),
                  (0xFEFF, 0xFEFF), (0xFFF9, 0xFFFB), (0x110BD, 0x110BD), (0x110CD, 0x110CD), (0x13430, 0x1343F),
                  (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0001, 0xE0001), (0xE0020, 0xE007F))
_CONTROL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f" + "".join(
    re.escape(chr(low)) if low == high else f"{re.escape(chr(low))}-{re.escape(chr(high))}" for low, high in _FORMAT_RANGES) + "]")
_UNSAFE_CATEGORIES = {"Cc", "Cf", "Cs", "Cn", "Co", "Zl", "Zp"}


def _strip_controls(text: str) -> str:
    return _CONTROL_RE.sub("", text.replace("\t", " "))


def _fit_line(line: str, cap: int) -> str:
    """Collapse whitespace, replace what a terminal cannot show safely, cut to `cap` characters with an ellipsis."""
    head = " ".join(line.split())[:cap + 1]
    head = "".join("�" if unicodedata.category(ch) in _UNSAFE_CATEGORIES else ch for ch in head)
    return head if len(head) <= cap else head[:cap - 1] + "…"


def _alnum_line(text: str, *, last: bool) -> str | None:
    lines = text.splitlines()
    for line in (reversed(lines) if last else lines):
        if any(ch.isalnum() for ch in line):
            return line
    return None


def activity_lines(text: str, *, last: bool, secrets: list[str] | None = None) -> tuple[str | None, str | None]:
    """(short, long) display line of a request (first line with a letter or digit) or a reply (`last`: the last such line).
    Order: drop control and format characters from the COMPLETE text, redact the complete text, pick the line, fit, cap."""
    from peerhub.extensions.adapters.sanitize import Sanitizer, env_secrets
    cleaned = Sanitizer(env_secrets() if secrets is None else secrets).clean(_strip_controls(text))
    line = _alnum_line(cleaned, last=last)
    return (None, None) if line is None else (_fit_line(line, LINE_SHORT), _fit_line(line, LINE_LONG))


def _display(value: object, cap: int, secrets: list[str]) -> str | None:
    if not isinstance(value, str):
        return None
    from peerhub.extensions.adapters.sanitize import Sanitizer
    shown = _fit_line(Sanitizer(secrets).clean(_strip_controls(value)), cap)
    return shown or None


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


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:  # an integer of 2**1024 or more
        return None


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
    item["ask_created"] = _number(created)  # a non-finite or overflowing creation time is unknown (None)
    return item, False


def _lead(caller: dict[str, Any], secrets: list[str]) -> dict[str, Any]:
    """A caller's lead; `key` (the full digest) groups and merges leads and is removed before output (#376 C8)."""
    session = caller.get("session")
    basis = session if session else f"{caller['host']}:{caller['ask_pid']}:{caller.get('ask_created')}"
    key = hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()  # a lone surrogate never raises
    return {"label": _display(caller["label"], 40, secrets) or "?", "tool": caller["tool"], "id": key[:8], "key": key}


def _iso_seconds(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.timestamp() if parsed.tzinfo is not None else None


def _binding_fields(binding: object) -> tuple[str | None, str | None, str | None]:
    from peerhub.extensions.diag_running import _profile  # pyright: ignore[reportPrivateUsage]
    if not isinstance(binding, str) or not binding:
        return None, None, None
    parts = binding.split("#", 1)[0].split("/")
    model, effort = (parts[1], parts[2]) if len(parts) == 3 else (parts[0], parts[1]) if len(parts) == 2 else (None, None)
    return _public_binding_value(_profile(binding)), _public_binding_value(model), _public_binding_value(effort)


def _liveness_of(caller: dict[str, Any], liveness: Callable[..., object] | None,
                 probe: Callable[[int], object]) -> tuple[bool | None, str | None]:
    alive: object = None
    if liveness is not None:
        try:
            alive = liveness(caller["host"], caller.get("ns"), caller["ask_pid"], caller.get("ask_created"))
        except Exception:  # noqa: BLE001 - liveness is a best-effort source: any failure is "unknown", never a group error
            alive = None
    if isinstance(alive, bool):
        return alive, None
    if caller["host"] != _this_host():
        return None, "other host"
    here = _this_ns()
    if caller.get("ns") is None or here is None or caller.get("ns") != here:
        return None, "other namespace"
    if liveness is None or caller.get("ask_created") is None:
        return None, "not accessible"
    try:
        found = probe(int(caller["ask_pid"]))
    except Exception:  # noqa: BLE001 - see above
        found = "denied"
    return None, "pid reused or clock stepped" if _number(found) is not None else "not accessible"


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
    if superseded:
        state, note = "unsure", "superseded"
    elif r["certainty"] == "MAY_HAVE_STARTED":
        state = "unsure"
        note = last if last is not None and (last in _UNSURE_KINDS or last.startswith("runtime_")) else "may have started"
    elif r["result_ok"] == 0:
        state = "unsure"
        reasons.append("result unreadable")
    elif r["result_ok"] == 1:
        if r["error_type"] not in (None, "null"):
            state, note = "error", _display(r["error_code"], 40, secrets) or "error"
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
                    denied = [shown for shown in (_display(item, 40, secrets) for item in cast(list[object], raw_denied)) if shown]
            note = _fit_line("denied: " + ", ".join(denied), 120) if denied else None
        else:
            state = "ok"
    elif last in _ERROR_KINDS:
        state, note = "error", last
    elif last is not None and (last in _UNSURE_KINDS or last.startswith("runtime_")):
        state, note = "unsure", _display(last, 40, secrets)
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
        state, note = "unsure", _display(last, 40, secrets)
    try:
        started_at = _started_at(r["start_detail"])
    except (OverflowError, RecursionError):  # an integer of 2**1024 or more; a deeply nested detail
        started_at = None
    if r["start_detail"] is not None and started_at is None:
        reasons.append("start time unreadable")
    terminal_at = _iso_seconds(r["terminal_at"])
    # An overdue row is in the running set only for ALERT_WINDOW_S after its lease expired; then it is an ended (bad) row.
    open_row = state == "run" or (state == "overdue" and expires_at >= now - ALERT_WINDOW_S)  # the SQL `is_open` expression
    ended_at: float | None = None
    ended_exact: bool | None = None
    if not open_row:
        if terminal_at is not None:
            ended_at, ended_exact = terminal_at, True
        elif not live:  # the release time of the claim: exact for the claim that ran it, else the latest possible end
            ended_at, ended_exact = expires_at, gen_match
    age_s = round(max(0.0, now - ended_at), 3) if ended_at is not None and ended_exact else None
    if open_row:
        elapsed_s = None if started_at is None else round(max(0.0, now - started_at), 3)
    else:
        elapsed_s = (round(max(0.0, ended_at - started_at), 3)
                     if started_at is not None and ended_at is not None and ended_exact else None)
    caller, caller_bad = _caller(r["caller_detail"])
    if caller_bad:
        reasons.append("caller unreadable")
    alive: bool | None = None
    alive_reason: str | None = None
    if open_row:
        alive, alive_reason = (None, "no caller row") if caller is None else _liveness_of(caller, liveness, probe)
    profile, model, effort = _binding_fields(r["binding"])
    stream = _display(r["stream_id"], 120, secrets) or "?"
    batch, task = (caller.get("batch"), caller.get("task")) if caller is not None else (None, None)
    name = (f"{_display(batch, 40, secrets)}/{_display(task, 40, secrets)}" if isinstance(batch, str) and batch
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
        "peer": _display(r["peer_id"], 64, secrets) or "?", "profile": profile, "model": model, "effort": effort,
        "stream": stream, "name": name, "lead": _lead(caller, secrets) if caller is not None else None,
        "started_at": started_at, "elapsed_s": elapsed_s, "ended_at": ended_at, "ended_exact": ended_exact, "age_s": age_s,
        "lease_left_s": round(expires_at - now, 3) if open_row else None, "tokens": tokens,
        "ask_line": lines["ask_line"], "reply_line": lines["reply_line"],
        "ask_line_long": lines["ask_line_long"], "reply_line_long": lines["reply_line_long"],
        "note": note, "reasons": reasons, "session": _ask_session(r),
    }
    if human:
        if text_on and r["want_text"] and caller is not None and isinstance(caller.get("query_file"), str):
            row["_caller_file"] = {key: caller.get(key) for key in ("query_file", "workspace", "host")}
        if open_row:
            lease = _number(r["lease_sec"])
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
    running = [a for a in main if a["state"] == "run" or (a["state"] == "overdue" and a["ended_at"] is None)]
    ended = [a for a in main if not (a["state"] == "run" or (a["state"] == "overdue" and a["ended_at"] is None))]
    running.sort(key=lambda a: (0 if a["state"] == "overdue" or a["alive"] is False else 1,
                                -(a["elapsed_s"] if a["elapsed_s"] is not None else -1.0)))
    window_start = now - ALERT_WINDOW_S
    ended.sort(key=lambda a: (0 if a["state"] in _BAD_STATES and a["note"] != "other generation"  # A4: a restored lineage is not news
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
        pct = _number(item.get("context_pct"))
        marked = pct is not None and pct >= LEAD_CONTEXT_MARK_PCT and item.get("alive") is True
        newest = max(_number(item.get("active_at")) or 0.0, _number(lead["last_ask_at"]) or 0.0)
        return 0 if marked else 1, 0 if lead["session"] else 1, -int(lead["running"]), -newest, str(lead["id"])

    leads.sort(key=rank)
    for ask in data["asks"]:
        if ask["lead"] is not None:
            ask["lead"].pop("key", None)
    data["leads_read"] = read


# ---- alerts (#374 section 3): a pure function over already-read facts -------------------------------------------------------

LastKnown = dict[str, tuple[float, float | None]]  # observation_id -> (validated fraction, resets_at) of a STALE row


def pool_states(observations: list[dict[str, Any]], last_known: LastKnown | None = None,
                now: float | None = None, samples: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Display state of every quota pool row and its rounded percent left. A fresh (MEASURED) row is out, low, fast, ok or
    reset; a STALE row with a last-known fraction is `aged` (never out, low or fast, so a3 stays fresh-only), or `reset` once
    its resets_at passed; anything else is `?`. `fresh` marks a MEASURED row with a valid fraction; `na` marks a window the
    provider says it does not have or has disabled (ABSENT with one of ABSENT_FACT_REASONS, also when aged).

    `shown` is what the POOLS table shows (precedence n/a ? reset out low fast aged ok): `state` for a fresh row; for a STALE
    row out at 0, low at `LOW_QUOTA_FRACTION` or less, fast on a threat projection, else aged. `fast` needs two projection
    samples, as in the view (monitor_view `_compact_pool`): the item's `projection_samples`, else `samples[observation_id]`, else 0."""
    pools: list[dict[str, Any]] = []
    for item in observations:
        if item.get("kind") != "quota" or item.get("pool_ref") is None:
            continue
        pool = str(item.get("pool_ref")).split(":")[-1]
        evidence = item.get("state")
        if evidence == "STALE" and item.get("stored_state", "MEASURED") != "MEASURED":
            evidence = item.get("stored_state")  # an aged ABSENT or failed reading is not a stale measurement
        raw_m: object = item.get("measurements")
        measured = cast(dict[str, Any], raw_m) if isinstance(raw_m, dict) else {}
        remaining = _number(measured.get("remaining_fraction")) if evidence == "MEASURED" else None
        if remaining is not None and not 0 <= remaining <= 1:
            remaining = None
        fresh = remaining is not None
        raw_p: object = item.get("projection")
        projection = cast(dict[str, Any], raw_p) if isinstance(raw_p, dict) else {}
        reset_in = _number(item.get("reset_in_seconds"))
        count = _number(item.get("projection_samples"))
        if count is None:
            count = _number((samples or {}).get(str(item.get("observation_id"))))
        threat = projection.get("state") == "threat" and (count or 0) >= 2

        def graded(fraction: float, otherwise: str) -> str:
            return "out" if fraction == 0 else "low" if fraction <= LOW_QUOTA_FRACTION else "fast" if threat else otherwise
        known = (last_known or {}).get(str(item.get("observation_id"))) if evidence == "STALE" else None
        if known is not None:
            remaining, resets_at = known
            passed = resets_at is not None and now is not None and resets_at <= now
            state, remaining = ("reset", None) if passed else ("aged", remaining)
            shown = state if remaining is None else graded(remaining, "aged")
        elif remaining is None:
            state = shown = "?"
        elif reset_in is not None and reset_in <= 0:
            state, remaining = "reset", None
            shown = state
        else:
            state = shown = graded(remaining, "ok")
        pools.append({"pool": f"{item.get('peer_id')} {pool}", "left_pct": None if remaining is None else round(remaining * 100),
                      "state": state, "shown": shown, "window": pool.rpartition("-")[2] if "-" in pool else "?",
                      "evidence": evidence, "fresh": fresh,
                      "na": evidence == "ABSENT" and str(item.get("reason") or "").startswith(ABSENT_FACT_REASONS)})
    return pools


def capacity_source(pools: list[dict[str, Any]]) -> str:
    """The 7-day rows, without the windows a provider says it does not have (`na`): `read` when there is none or one is fresh
    (MEASURED with a valid fraction); else `stale` when at least one is STALE; else `unknown` (every one is unknown or in
    error, so no 7-day pool has a number)."""
    weekly = [p for p in pools if p.get("window") == "7D" and not p.get("na")]
    if not weekly or any(p.get("fresh") for p in weekly):
        return "read"
    return "stale" if any(p.get("evidence") == "STALE" for p in weekly) else "unknown"


def min_quota(pools: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The lowest 7-day pool with a known percent left: its shown state (as in the POOLS table) and whether it is stale."""
    weekly = [p for p in pools if p["window"] == "7D" and p["left_pct"] is not None]
    if not weekly:
        return None
    low = min(weekly, key=lambda p: (p["left_pct"], p["pool"]))
    return {"pool": low["pool"], "left_pct": low["left_pct"], "state": low.get("shown", low["state"]),
            "aged": low.get("evidence") == "STALE"}


def _alert(code: str, kind: str, subject: str, age_s: float | None, severity: str) -> dict[str, Any]:
    return {"code": code, "kind": kind, "subject": subject, "age_s": age_s, "severity": severity}


def build_alerts(*, asks: list[dict[str, Any]] | None, pools: list[dict[str, Any]] | None,
                 accounts: list[dict[str, Any]] | None, facts_report: dict[str, Any] | None,
                 failures: list[tuple[str, str]], now: float) -> list[dict[str, Any]]:
    """Alerts in severity order: a6 failure; a2 gone; a2 overdue; a1; a5 login or expired token; a3 pool out; then the low ones
    (a5 token expiring within 48 hours, a6 facts needs_recheck). A source that was not read (None) gives no alert."""
    failed = [_alert("a6", "store", f"{group}: {reason}", None, "high") for group, reason in failures]
    gone: list[dict[str, Any]] = []
    overdue: list[dict[str, Any]] = []
    ended_badly: list[dict[str, Any]] = []
    for row in asks or []:
        if row.get("superseded"):
            continue
        subject = f"{row.get('profile') or row.get('peer') or '?'} {row.get('name') or '?'}"
        state = row.get("state")
        if state == "run" and row.get("alive") is False:
            gone.append(_alert("a2", "gone", subject, row.get("elapsed_s"), "high"))
        elif state == "overdue":
            left = _number(row.get("lease_left_s"))
            if left is not None and -left <= ALERT_WINDOW_S:
                overdue.append(_alert("a2", "overdue", subject, round(max(0.0, -left), 3), "high"))
        elif state in _BAD_STATES and row.get("note") != "other generation":  # A4: no alert at all for another lineage
            ended = _number(row.get("ended_at"))
            if ended is not None and now - ended <= ALERT_WINDOW_S:
                ended_badly.append(_alert("a1", str(state), subject, row.get("age_s"), "high"))
    login: list[dict[str, Any]] = []
    expiring: list[dict[str, Any]] = []
    for account in accounts or []:
        peer = str(account.get("peer_id") or "?")
        if account.get("logged_in") is False:
            login.append(_alert("a5", "login", peer, None, "high"))
            continue
        expires = _number(account.get("login_token_expires_at"))
        if expires is not None and expires <= now:
            login.append(_alert("a5", "token_expired", peer, round(now - expires, 3), "high"))
        elif expires is not None and expires - now <= TOKEN_EXPIRY_WARN_S:
            expiring.append(_alert("a5", "token_expiring", peer, None, "low"))
    out_pools = [_alert("a3", "pool_out", str(p["pool"]), None, "high") for p in pools or [] if p.get("state") == "out"]
    facts: list[dict[str, Any]] = []
    if isinstance(facts_report, dict) and facts_report.get("status") == "needs_recheck":
        raw_reasons: object = facts_report.get("reasons")
        reasons = [str(r) for r in cast(list[object], raw_reasons)] if isinstance(raw_reasons, list) else []
        facts.append(_alert("a6", "facts", _fit_line(", ".join(reasons) or "needs_recheck", 120), None, "low"))
    return failed + gone + overdue + ended_badly + login + out_pools + expiring + facts


def _decode_text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def _saved_defaults_source(groups: list[str]) -> str | None:
    """The saved-defaults layer when `groups` is exactly what it selects; the read model has no other view of the selection."""
    try:
        from peerhub.extensions.monitor_preferences import effective_preferences
        preference = effective_preferences()
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        return None
    if preference.get("status") == "OK" and preference.get("source") in ("workspace", "global") \
            and list(preference.get("selected_groups") or []) == list(groups):
        return str(preference["source"])
    return None


def render_monitor_frame(db_path: str, groups: list[str], *, read_at: float | None = None,
                         trace_callback: Callable[[str], None] | None = None,
                         workspace: str | Path | None = None, human: bool = False,
                         liveness: Callable[..., object] | None = None,
                         process_probe: Callable[[int], object] | None = None,
                         preference_source: str | None = None,
                         lead_reader: LeadReader | None = None) -> dict[str, Any]:
    """Read grouped monitor facts from one query-only SQLite snapshot (five bounded query families).

    `lead_reader` (composed in peerhub/cli) gives the lead sessions; it runs only when `activity` was read, after the
    read transaction ends. Without it no file is read, every lead has `session: None` and `leads_read` is None."""
    import time
    now = time.time() if read_at is None else read_at
    end_day = datetime.fromtimestamp(now, timezone.utc).date()
    start_day = (end_day - timedelta(days=29)).isoformat()
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # A stored "\ud800" escape makes json_extract return invalid UTF-8; decode it with U+FFFD instead of failing the fetch.
    conn.text_factory = _decode_text
    output: dict[str, Any] = {}
    errors: dict[str, str] = {}
    select_count = 0
    try:
        conn.execute("PRAGMA query_only=ON")
        if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise sqlite3.OperationalError("read-only monitor connection unavailable")
        conn.execute("BEGIN")
        schema = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if schema > SUPPORTED_SCHEMA_VERSION:
            raise SchemaVersionError(future_schema_message(schema, SUPPORTED_SCHEMA_VERSION))
        def trace(statement: str) -> None:
            nonlocal select_count
            if statement.lstrip().upper().startswith(("SELECT", "WITH")):
                select_count += 1
            if trace_callback is not None:
                trace_callback(statement)
        conn.set_trace_callback(trace)
        peers: list[dict[str, Any]] = []
        peer_sample_truncated = False
        peers_total = 0
        try:
            rows = peer_rows(conn) if bool(set(groups) & {"overview", "peers"}) else []
            peer_sample_truncated = len(rows) > PEER_CAP
            peers = [{"peer_id": str(row[0]), "display_name": row[1], "adapter_ref": row[2]} for row in rows[:PEER_CAP]]
            if peer_sample_truncated:  # what "showing N of M peers" needs
                peers_total = int(rows[0][3])
            if "overview" in groups:
                output["overview"] = {"status": "OK", "data": {"peers": peers, "sample_size": len(peers),
                                                                     "truncated": peer_sample_truncated,
                                                                     **({"peers_total": peers_total}
                                                                        if peer_sample_truncated else {})}}
        except sqlite3.Error as exc:
            errors["overview"] = type(exc).__name__
            if "overview" in groups:
                output["overview"] = {"status": "ERROR", "data": None}
        sessions: list[dict[str, Any]] = []
        truncated = False
        sessions_total = 0
        try:
            needs_sessions = bool(set(groups) & {"overview", "sessions", "usage", "context", "runtime", "peers"})
            rows = session_rows(conn) if needs_sessions else []
            truncated = len(rows) > 100
            if truncated:  # the count that makes the sample marker "sample 100/M"
                sessions_total = int(rows[0]["total_sessions"])
            for row in rows[:100]:
                binding = _safe_object(row["binding_json"])
                telemetry = public_telemetry(row["detail"], handle=str(row["session_handle"]),
                                             generation=int(row["session_generation"]), provider=str(row["runtime_kind"]))
                rc, fc = row["reuse_count"], row["fresh_count"]
                exec_mode = ("reused" if rc and rc > 0 else "fresh" if fc and fc > 0 else None)
                sessions.append({"handle": str(row["session_handle"]), "stream_id": str(row["stream_id"]),
                                 "peer_id": str(row["peer_id"]), "generation": int(row["session_generation"]),
                                 "provider": str(row["runtime_kind"]), "state": str(row["state"]),
                                 "resumable": bool(row["resumable"]), "execution_mode": exec_mode,
                                 "uses": None if rc is None or fc is None else int(rc) + int(fc),
                                 "last_seen": float(row["last_seen"]),
                                 "created_at": float(row["created_at"]), "tombstoned_at": row["tombstoned_at"],
                                 "configured_binding": {key: _public_binding_value(binding.get(key)) for key in ("profile", "model", "effort")},
                                 "telemetry": telemetry})
        except sqlite3.Error as exc:
            errors["sessions"] = type(exc).__name__
        # One session sample feeds sessions, usage and context (#394): they carry the same size, truncation and total.
        sample = {"sample_size": len(sessions), "truncated": truncated, **({"sample_total": sessions_total} if truncated else {})}
        if "sessions" in groups:
            output["sessions"] = {"status": "ERROR" if "sessions" in errors else "OK",
                                  "data": {"items": sessions, **sample}}
        if "usage" in groups:
            output["usage"] = {"status": "ERROR" if "sessions" in errors else "OK", "data": {"sessions": [{"handle": item["handle"], "peer_id": item["peer_id"],
                                                                            "generation": item["generation"], "telemetry": item["telemetry"]["usage"]}
                                                                   for item in sessions], **sample}}
        if "context" in groups:
            output["context"] = {"status": "ERROR" if "sessions" in errors else "OK", "data": {"sessions": [{"handle": item["handle"], "peer_id": item["peer_id"],
                                                                              "generation": item["generation"], "context": item["telemetry"]["context"]}
                                                                     for item in sessions], **sample}}
        if "runtime" in groups:
            config_workspace = _monitor_workspace(db_path, workspace)
            current_profiles: dict[str, dict[str, Any]] = {}
            runtime_sessions: list[dict[str, Any]] = []
            for item in sessions:
                binding = item["configured_binding"]
                profile = binding.get("profile")
                if isinstance(profile, str):
                    if profile not in current_profiles:
                        current_profiles[profile] = _current_profile(profile, config_workspace)
                    current_profile = current_profiles[profile]
                else:
                    current_profile = _current_profile(profile, config_workspace)
                runtime_sessions.append({
                    "handle": item["handle"], "peer_id": item["peer_id"], "provider": item["provider"],
                    "configured_binding": binding, "resolved_model": item["telemetry"]["resolved_model"],
                    "adapter_version": item["telemetry"]["adapter_version"],
                    "provider_cli_version": item["telemetry"]["provider_cli_version"],
                    "configured_binding_provenance": {
                        "basis": "session_snapshot",
                        "field_sources": {"profile": None, "model": None, "effort": None}},
                    "current_profile": current_profile,
                })
            output["runtime"] = {"status": "ERROR" if "sessions" in errors else "OK",
                                  "data": {"sessions": runtime_sessions}}
        scopes: list[dict[str, Any]] = []
        scope_rows: list[sqlite3.Row] = []
        scope_truncated_peers: set[str] = set()
        try:
            needs_scopes = bool(set(groups) & {"execution", "coverage", "usage", "sessions"})
            if needs_scopes:
                scope_rows = delivery_scope_rows(conn, start_day, end_day.isoformat())
            else:
                scope_rows = []
            selected_scope_rows = [row for row in scope_rows if int(row[22]) <= 3 and int(row[21]) <= 20]
            scope_truncated_peers = {str(row[1]) for row in scope_rows
                                     if (int(row[22]) == 4 or (int(row[22]) <= 3 and int(row[21]) == 21))}
            scope_truncated = bool(scope_truncated_peers)
            for row in selected_scope_rows:
                scopes.append({"stream_id": str(row[0]), "peer_id": str(row[1]),
                               "certainty": {"not_started": int(row[2]), "may_have_started": int(row[3]),
                                             "started": int(row[4]), "terminal": int(row[5])},
                               "usage_coverage": {"terminal": int(row[5]), "measured": int(row[6]), "absent": int(row[7]),
                                                  "unsupported": int(row[8]), "unknown": int(row[9]), "missing": int(row[10]),
                                                  "complete": bool(row[12]), "coverage_start_seq": int(row[14])},
                               "delivery_complete": bool(row[11]),
                               "latency_30d": {"sum_ns": int(row[15]), "count": int(row[16]),
                                               "missing_start": int(row[17]), "domain_mismatch": int(row[18]),
                                               "invalid_ticks": int(row[19]), "terminal_before_start": int(row[20])},
                               "retained_session_count": row[23], "retained_complete": bool(row[24])})
            if scope_truncated:
                errors["scope_cap"] = "truncated"
        except sqlite3.Error as exc:
            errors["execution"] = f"{type(exc).__name__}: {exc}"
        if "execution" in groups:
            output["execution"] = {"status": "ERROR" if "execution" in errors and errors["execution"] != "truncated" else "OK",
                                   "data": {"scopes": scopes, "sample_size": len(scopes), "truncated": "scope_cap" in errors}}
        if "coverage" in groups:
            output["coverage"] = {"status": "ERROR" if "execution" in errors and errors["execution"] != "truncated" else "OK",
                                  "data": {"scopes": [{"stream_id": item["stream_id"], "peer_id": item["peer_id"],
                                                        "delivery_complete": item["delivery_complete"],
                                                        "usage_coverage": item["usage_coverage"]} for item in scopes],
                                           "truncated": "scope_cap" in errors}}
        if "execution" in errors and errors["execution"] != "truncated":
            for dependent in ("overview", "sessions", "usage"):
                if dependent in groups and isinstance(output.get(dependent), dict):
                    cast(dict[str, Any], output[dependent])["status"] = "ERROR"
        if "usage" in groups and isinstance(output.get("usage"), dict):
            usage_group = cast(dict[str, Any], output["usage"])
            usage_data = cast(dict[str, Any], usage_group["data"])
            usage_data["coverage_by_scope"] = [{"stream_id": item["stream_id"], "peer_id": item["peer_id"],
                                                **item["usage_coverage"]} for item in scopes]
        if "sessions" in groups and isinstance(output.get("sessions"), dict):
            sessions_group = cast(dict[str, Any], output["sessions"])
            sessions_data = cast(dict[str, Any], sessions_group["data"])
            retained: dict[str, dict[str, Any]] = {}
            for item in scopes:
                peer = str(item["peer_id"])
                entry = retained.setdefault(peer, {"value": 0, "complete": True, "reason": None, "scope_count": 0})
                entry["scope_count"] += 1
                if item["retained_session_count"] is None:
                    entry.update(value=None, complete=False, reason="projection_missing")
                elif not item["retained_complete"] and entry["complete"]:
                    entry.update(value=None, complete=False, reason="legacy_incomplete")
                elif entry["complete"]:
                    entry["value"] += int(item["retained_session_count"])
            for peer, entry in retained.items():
                if peer in scope_truncated_peers and entry["complete"]:
                    entry.update(value=None, complete=False, reason="scope_cap")
            sessions_data["retained_sessions_by_peer"] = {
                peer: {"value": item["value"], "complete": item["complete"], "reason": item["reason"]}
                for peer, item in retained.items()
            }
            if len(scope_rows) > 0 and max((int(row[22]) for row in scope_rows), default=0) > 3:
                sessions_data["peer_sample_truncated"] = True
        observations: list[dict[str, Any]] = []
        staged: list[tuple[tuple[str, str], dict[str, Any]]] = []  # (peer, probe source) with its row, in query order
        newest_success: dict[tuple[str, str], int] = {}  # newest MEASURED pool row per (peer, probe source)
        reset_credits: list[dict[str, Any]] = []
        accounts: list[dict[str, Any]] = []
        latest_facts_report: dict[str, Any] | None = None
        latest_facts_seq = -1  # capture_seq is store-wide: the newest report wins, not the first in query order (#366 M1b)
        capacity_truncated = accounts_truncated = False
        last_known: LastKnown = {}
        capacity_never_refreshed = False  # the observation tables do not exist yet: no quota evidence, not a store failure
        sample_counts: dict[str, int] = {}  # observation_id -> distinct MEASURED samples of its window (0..2)
        if bool(set(groups) & {"capacity", "peers"}):
            try:
                all_rows = capacity_rows(conn)
                rows = [row for row in all_rows if row["kind"] != "account"]
                account_rows = [row for row in all_rows if row["kind"] == "account"]
                for row in rows[:100]:
                    try:
                        observation, capture_seq = row_to_observation(row)
                    except ObservationCorruptError as exc:
                        raise sqlite3.DataError(f"invalid quota observation at capture_seq={row['capture_seq']}") from exc
                    if capture_seq > latest_facts_seq and "facts_report" in observation.payload:
                        raw_f: object = observation.payload.get("facts_report")
                        if isinstance(raw_f, dict):
                            latest_facts_report, latest_facts_seq = cast(dict[str, Any], raw_f), capture_seq
                    freshness = evaluate_observation(observation, capture_seq, round(now * 1_000_000), FreshnessPolicy())
                    if observation.kind == "reset_credit":
                        shown = freshness.state.value in {"MEASURED", "STALE"}
                        reset_credits.append({
                            "peer_id": observation.subject_ref,
                            "state": freshness.state.value,
                            "age_seconds": freshness.age_seconds,
                            "available_count": observation.payload.get("available_count") if shown else None,
                            "nearest_expires_at": observation.payload.get("nearest_expires_at") if shown else None,
                        })
                        continue
                    observed_at = observation.observed_at
                    try:
                        measured_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).timestamp()
                    except (ValueError, OverflowError):
                        measured_at = None
                    remaining = observation.payload.get("remaining_fraction")
                    used_fraction = (1 - float(remaining)
                                     if isinstance(remaining, (int, float)) and not isinstance(remaining, bool)
                                     and math.isfinite(float(remaining)) and 0 <= remaining <= 1 else None)
                    projection = project_exhaustion(
                        used_fraction, observation.payload.get("window_started_at"), measured_at,
                        observation.payload.get("resets_at"), now,
                        evidence_state=freshness.state.value,
                        anchor_basis=observation.payload.get("anchor_basis"),
                    )
                    measurements = None
                    reset_in_seconds = None
                    checked_remaining = _number(remaining) if used_fraction is not None else None  # the value validated above
                    if freshness.state.value == "STALE" and checked_remaining is not None:  # alerts only: public value stays None
                        stale_resets: object = observation.payload.get("resets_at")
                        last_known[observation.observation_id] = (checked_remaining, _number(stale_resets))
                    if row["projection_samples"] is not None:  # from the capacity statement itself, on every path
                        sample_counts[observation.observation_id] = int(row["projection_samples"])
                    if freshness.state.value == "MEASURED":
                        resets_at = observation.payload.get("resets_at")
                        if (isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool)
                                and math.isfinite(resets_at)):
                            reset_in_seconds = max(0.0, resets_at - now)
                        measured: dict[str, int | float] = {}
                        for key in MEASUREMENT_KEYS.intersection(observation.payload):
                            value = observation.payload[key]
                            if (isinstance(value, (int, float)) and not isinstance(value, bool) and
                                    math.isfinite(float(value)) and value >= 0 and
                                    (key != "remaining_fraction" or value <= 1)):
                                measured[key] = value
                        measurements = measured
                    pool_ref = observation.resource_pool_ref
                    probe = (str(observation.subject_ref), _probe_source(observation.payload, observation.source))  # account key = peer id
                    if observation.kind == "quota" and pool_ref is not None and freshness.stored_state.value == "MEASURED":
                        newest_success[probe] = max(newest_success.get(probe, 0), int(row["effective_at_us"]))
                    staged.append((probe, {
                        "observation_id": observation.observation_id,
                        "peer_id": observation.subject_ref,
                        "provider": row["provider"],
                        "kind": observation.kind,
                        "pool_ref": pool_ref,
                        "pool_kind": row["pool_kind"],
                        "attribution_scope": "peer_account_pool" if pool_ref is not None else "peer",
                        "source": observation.source,
                        "projection": projection,
                        "state": freshness.state.value,
                        "stored_state": freshness.stored_state.value,
                        "observed_at": observation.observed_at,
                        "captured_at": observation.captured_at,
                        "effective_at_us": int(row["effective_at_us"]),
                        "age_seconds": freshness.age_seconds,
                        "ttl_seconds": freshness.ttl_seconds,
                        "freshness_basis": freshness.basis,
                        "skew_seconds": freshness.skew_seconds,
                        "measurements": measurements,
                        "reset_in_seconds": reset_in_seconds,
                        "reason": observation.payload.get("reason") or observation.payload.get("condition"),
                    }))
                observations = [item for probe, item in staged if not _superseded(item, newest_success.get(probe, 0))]
                capacity_truncated, accounts_truncated = len(rows) > 100, len(account_rows) > ACCOUNT_ROW_CAP
                accounts = _read_accounts(conn, account_rows[:ACCOUNT_ROW_CAP], now)
                _fill_unknown_plans(observations, accounts)
                if "capacity" in groups:
                    output["capacity"] = {"status": "OK", "data": {"observations": observations,
                        "reset_credits": reset_credits,
                        **({"accounts": accounts} if accounts else {}),
                        **({"accounts_truncated": True} if len(account_rows) > ACCOUNT_ROW_CAP else {}),
                        "sample_size": min(len(rows), 100), "truncated": len(rows) > 100}}
            except sqlite3.Error as exc:
                capacity_never_refreshed = no_observation_tables(conn, exc)
                if not capacity_never_refreshed:
                    errors["capacity"] = type(exc).__name__
                if "capacity" in groups:
                    # No observation table at all is a capacity group read with nothing in it (as when the tables are
                    # empty), not an error: the human frame and the JSON agree, and `alert_sources` says `no_evidence`.
                    output["capacity"] = ({"status": "OK", "data": {"observations": [], "reset_credits": [],
                                                                    "sample_size": 0, "truncated": False}}
                                          if capacity_never_refreshed else {"status": "ERROR", "data": None})
        if "peers" in groups:
            config_workspace = _monitor_workspace(db_path, workspace)
            peer_records: list[dict[str, Any]] = []
            for p in peers:
                pid = p["peer_id"]
                peer_sessions = [s for s in sessions if s["peer_id"] == pid]
                s_item = max(
                    peer_sessions,
                    key=lambda s: (float(s.get("last_seen") or 0.0), int(s.get("generation") or 0)),
                    default=None,
                )
                prof_data: dict[str, Any] | None = None
                configured_model: object = None
                configured_pools: list[str] = []
                is_mapped = False
                if s_item is not None:
                    raw_binding: object = s_item.get("configured_binding")
                    binding: dict[str, Any] = cast(dict[str, Any], raw_binding) if isinstance(raw_binding, dict) else {}
                    prof_name = binding.get("profile")
                    recorded_model = binding.get("model")
                    prof_source = "unknown"
                    if isinstance(prof_name, str):
                        cp = _current_profile(prof_name, config_workspace)
                        raw_fields: object = cp.get("fields")
                        fields: dict[str, Any] = cast(dict[str, Any], raw_fields) if isinstance(raw_fields, dict) else {}
                        raw_model: object = fields.get("model")
                        model_field: dict[str, Any] = cast(dict[str, Any], raw_model) if isinstance(raw_model, dict) else {}
                        configured_model = model_field.get("value")
                        model_source: object = model_field.get("source")
                        raw_pools: object = fields.get("pools")
                        pools_field: dict[str, Any] = cast(dict[str, Any], raw_pools) if isinstance(raw_pools, dict) else {}
                        pools_val: object = pools_field.get("value")
                        if isinstance(pools_val, list):
                            configured_pools = [str(x) for x in cast(list[object], pools_val)]
                        if model_source and recorded_model is not None and recorded_model == configured_model:
                            prof_source = str(model_source)
                            if configured_pools:
                                is_mapped = True
                    prof_data = {
                        "profile": prof_name,
                        "model": binding.get("model"),
                        "source": prof_source,
                    }

                peer_observations = [o for o in observations if o["peer_id"] == pid]
                q_data: dict[str, Any] | None = None
                measured_candidates: list[tuple[float, dict[str, Any]]] = []
                stale_candidates: list[dict[str, Any]] = []
                for o in peer_observations:
                    raw_m: object = o.get("measurements")
                    q_m: dict[str, Any] = cast(dict[str, Any], raw_m) if isinstance(raw_m, dict) else {}
                    raw_rem: object = q_m.get("remaining_fraction")
                    is_valid_rem = (
                        isinstance(raw_rem, (int, float)) and not isinstance(raw_rem, bool)
                        and math.isfinite(float(raw_rem)) and 0.0 <= float(raw_rem) <= 1.0
                    )
                    state = o.get("state")
                    if state == "MEASURED" and is_valid_rem:
                        measured_candidates.append((float(cast(float, raw_rem)), o))
                    elif state == "STALE":
                        stale_candidates.append(o)

                if is_mapped:
                    q_data = mapped_quota(configured_pools, peer_observations)
                else:
                    unmapped_scope = "unmapped" if s_item is not None else None
                    if measured_candidates:
                        lowest_rem, best_obs = min(measured_candidates, key=lambda x: (x[0], str(x[1].get("pool_ref") or "")))
                        q_data = {
                            "pool_ref": best_obs.get("pool_ref") or best_obs.get("pool_kind"),
                            "remaining_fraction": lowest_rem,
                            "percent": round(lowest_rem * 100),
                            "state": "MEASURED",
                            "source": best_obs.get("source"),
                            "age_seconds": best_obs.get("age_seconds"),
                        }
                        if unmapped_scope is not None:
                            q_data["scope"] = unmapped_scope
                    elif stale_candidates:
                        stale_obs = stale_candidates[0]
                        q_data = {
                            "pool_ref": stale_obs.get("pool_ref") or stale_obs.get("pool_kind"),
                            "remaining_fraction": None,
                            "percent": None,
                            "state": "STALE",
                            "source": stale_obs.get("source"),
                            "age_seconds": stale_obs.get("age_seconds"),
                        }
                        if unmapped_scope is not None:
                            q_data["scope"] = unmapped_scope
                    elif peer_observations:
                        first_obs = peer_observations[0]
                        q_data = {
                            "pool_ref": first_obs.get("pool_ref") or first_obs.get("pool_kind"),
                            "remaining_fraction": None,
                            "percent": None,
                            "state": first_obs.get("state") or "UNKNOWN",
                            "source": first_obs.get("source"),
                            "age_seconds": first_obs.get("age_seconds"),
                        }
                        if unmapped_scope is not None:
                            q_data["scope"] = unmapped_scope
                sess_data: dict[str, Any] | None = None
                if s_item is not None:
                    sess_data = {
                        "generation": s_item.get("generation"),
                        "execution_mode": s_item.get("execution_mode"),
                        "resumable": s_item.get("resumable"),
                    }
                ctx_data: dict[str, Any] | None = None
                if s_item is not None:
                    raw_tel: object = s_item.get("telemetry")
                    tel: dict[str, Any] = cast(dict[str, Any], raw_tel) if isinstance(raw_tel, dict) else {}
                    raw_ctx: object = tel.get("context")
                    tel_ctx: dict[str, Any] = cast(dict[str, Any], raw_ctx) if isinstance(raw_ctx, dict) else {}
                    if isinstance(raw_ctx, dict):
                        ctx_data = {
                            "percent": tel_ctx.get("percent"),
                            "occupancy_tokens": tel_ctx.get("occupancy_tokens"),
                            "window_tokens": tel_ctx.get("window_tokens"),
                            "observed_at": tel_ctx.get("observed_at"),  # the end of the ask the estimate belongs to
                            "state": tel_ctx.get("state"),
                        }
                peer_records.append({
                    "peer_id": pid,
                    "quota": q_data,
                    "profile": prof_data,
                    "session": sess_data,
                    "context": ctx_data,
                })
            from peerhub.extensions.adapters.profiles import packaged_defaults_meta
            defaults_meta = packaged_defaults_meta()
            if latest_facts_report is None:
                try:
                    f_rows = recent_quota_payloads(conn)
                    for (f_str,) in f_rows:
                        if not f_str or "facts_report" not in f_str:
                            continue
                        try:
                            data_obj: object = json.loads(f_str)
                            if isinstance(data_obj, dict):
                                rep_val: object = cast(dict[str, Any], data_obj).get("facts_report")
                                if isinstance(rep_val, dict):
                                    latest_facts_report = cast(dict[str, Any], rep_val)
                                    break
                        except (ValueError, TypeError):
                            continue
                except (sqlite3.Error, ValueError, TypeError):
                    pass
            output["peers"] = {
                "status": "ERROR" if "sessions" in errors else "OK",
                "data": {
                    "peers": peer_records,
                    "sample_size": len(peer_records),
                    "truncated": truncated or peer_sample_truncated,
                    "session_sample_truncated": truncated,
                    "peer_sample_truncated": peer_sample_truncated,
                    **({"peers_total": peers_total} if peer_sample_truncated else {}),
                    "defaults_revision": defaults_meta.get("defaults_revision"),
                    "observed_at": defaults_meta.get("observed_at"),
                    "facts_report": latest_facts_report,
                },
            }
        activity_data: dict[str, Any] | None = None
        text_on = os.environ.get("PEERHUB_MONITOR_TEXT", "").strip().lower() != "off"
        if "activity" in groups:
            generation, generation_reason = _workspace_generation(db_path)
            try:
                rows = activity_rows(conn, now, generation, text_on)
                if liveness is None:
                    try:
                        from peerhub.extensions.liveness import ask_alive
                        liveness = ask_alive
                    except ImportError:
                        liveness = None
                activity_data = _activity_data(rows, now, gen_reason=generation_reason, human=human, text_on=text_on,
                                               liveness=liveness, probe=process_probe or _process_created)
                output["activity"] = {"status": "OK", "data": activity_data}
            except sqlite3.Error as exc:
                errors["activity"] = type(exc).__name__
                output["activity"] = {"status": "ERROR", "data": None}
        if "store" in groups:
            try:
                output["store"] = {"status": "OK", "data": {"schema_version": schema,
                                                                  "database_size_bytes": Path(db_path).stat().st_size}}
            except OSError as exc:
                errors["store"] = type(exc).__name__
                output["store"] = {"status": "ERROR", "data": None}
        if "overview" in groups and isinstance(output.get("overview"), dict):
            overview = cast(dict[str, Any], output["overview"])
            overview_data = cast(dict[str, Any], overview["data"])
            overview_data["session_sample_size"] = len(sessions)
            overview_data["session_sample_truncated"] = truncated
        if "sessions" in errors or ("execution" in errors and errors["execution"] != "truncated"):
            for dependent in ("overview", "peers", "sessions", "usage", "context", "runtime"):
                if dependent in groups and isinstance(output.get(dependent), dict):
                    cast(dict[str, Any], output[dependent])["status"] = "ERROR"
        for _group_id, raw_group in output.items():
            group = cast(dict[str, Any], raw_group)
            data_obj: object = group.get("data")
            data = cast(dict[str, Any], data_obj) if isinstance(data_obj, dict) else {}
            is_truncated = bool(data.get("truncated", False) or data.get("session_sample_truncated", False)
                                or data.get("peer_sample_truncated", False))
            group_status = str(group.get("status", "ERROR"))
            sample_size = data.get("sample_size")
            if sample_size is None:
                sample_size = next((len(cast(list[Any], value)) for value in data.values() if isinstance(value, list)), None)
            group["coverage"] = {
                "read": "unavailable" if group_status == "ERROR" else "partial" if is_truncated else "complete",
                "sample_size": sample_size, "truncated": is_truncated,
                "reason": "query_error" if group_status == "ERROR" else "sample_cap" if is_truncated else None,
            }
        human_observations: list[dict[str, Any]] = []
        quota_age: dict[str, float | None] = {}
        if human and not set(groups) & {"capacity", "peers"}:
            try:
                quota_age["_quota_age_seconds"] = oldest_measured_quota_age(_quota_age_observations(conn, now))
            except sqlite3.Error as exc:
                if no_observation_tables(conn, exc):
                    quota_age["_quota_age_seconds"] = None
        if human and observations:
            # One batch within this read snapshot; redraws reuse these immutable facts.
            ids = [item["observation_id"] for item in observations]
            evidence = quota_evidence_rows(conn, ids, now)
            by_id = {row["observation_id"]: row for row in evidence}
            for item in observations:
                row = by_id.get(item["observation_id"])
                raw: dict[str, Any] = json.loads(row["payload_json"]) if row is not None else {}
                display = {**item, "last_measurements": raw, "resets_at": raw.get("resets_at"),
                           "projection_samples": row["samples"] if row else 0}
                if row is not None:
                    display["_capture_seq"] = row["capture_seq"]
                    if row["prior_at_us"] is not None:
                        prior: object = json.loads(row["prior_payload"])
                        if isinstance(prior, dict):
                            display["_prior_measurement"] = prior
                            display["_prior_age"] = now - row["prior_at_us"] / 1_000_000
                human_observations.append(display)
        conn.execute("ROLLBACK")
        if activity_data is not None:  # only when `activity` was read; after the read transaction (#376 3.3)
            from peerhub.extensions.adapters.sanitize import env_secrets
            sessions, leads_read = _lead_sessions(lead_reader, now=now, text_on=text_on, secrets=env_secrets(),
                                                  created=process_probe or _process_created)
            _merge_leads(activity_data, sessions, leads_read)
        capacity_read = bool(set(groups) & {"capacity", "peers"})
        capacity_failed = "capacity" in errors

        no_evidence = capacity_read and capacity_never_refreshed  # never a quota refresh: not read, but not a store failure

        def source(selected: bool, failed: bool, cut: bool) -> str:
            return "not_selected" if not selected else "error" if failed else "truncated" if cut else "read"

        def quota_source(state: str) -> str:
            return "no_evidence" if no_evidence else state
        pools = (pool_states(observations, last_known, now, sample_counts)
                 if capacity_read and not capacity_failed else None)
        capacity_state = source(capacity_read, capacity_failed, capacity_truncated)
        alert_sources: dict[str, Any] = {
            # Only the scope cap weakens the source: superseded rows never alert, so their cap stays in data.truncated.
            "activity": source("activity" in groups, "activity" in errors or bool(activity_data and activity_data["reason"]),
                               bool(activity_data and activity_data["scopes_total"] > ACTIVITY_SCOPE_CAP)),
            "capacity": quota_source(capacity_source(pools) if capacity_state == "read" and pools is not None else capacity_state),
            "accounts": quota_source(source(capacity_read, capacity_failed, accounts_truncated)),
            "facts": quota_source(source(capacity_read, capacity_failed, False)),
        }
        if no_evidence:
            alert_sources["reasons"] = {"capacity": "no quota evidence"}
        if "activity" not in groups and (preference_source or _saved_defaults_source(groups)) in ("workspace", "global"):
            alert_sources["hint"] = "peerhub monitor defaults reset"
        alerts = build_alerts(asks=activity_data["asks"] if activity_data else None, pools=pools,
                              accounts=accounts if capacity_read and not capacity_failed else None,
                              facts_report=latest_facts_report if capacity_read and not capacity_failed else None,
                              failures=[(key, str(value).split(":")[0]) for key, value in errors.items()
                                        if key != "scope_cap"],
                              now=now)
        return {"schema_version": "2.0", "status": "PARTIAL" if errors else "OK", "read_at": now,
                "groups": output, "errors": errors, "alert_sources": alert_sources, "alerts": alerts,
                "min_quota": min_quota(pools) if pools is not None else None, "query_families": select_count,
                **({"_human_observations": human_observations, **quota_age} if human else {})}
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.close()
