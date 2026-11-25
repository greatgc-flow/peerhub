"""Codex app-server usage, reset-credit and account probes."""
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, cast

from peerhub.extensions.quota_types import (
    NOT_REPORTED_REASON,
    EvidenceRef,
    EvidenceState,
    EvidenceValue,
    IdSource,
    ResetCreditObserved,
    UsageMeasurement,
    UsageObserved,
    account_text,
)

from . import common
from .common import AccountObserved, ProbeCancellation


_CODEX_CLIENT_INFO = {"name": "hub-credit", "version": "1.0"}
_RATE_LIMITS_READ_METHOD = "account/rateLimits/read"


def _plan_label(value: object) -> str:
    """planType is display data only (X5): sanitised, bounded, never branched on."""
    text = re.sub(r"[^A-Za-z0-9_.-]", "", value) if isinstance(value, str) else ""
    return text[:24] or "unknown"


_RESET_CREDIT_REF = "probe:cx:reset-credits"
_CREDIT_STR_FIELDS = (("id", "id_ref"), ("resetType", "reset_type"), ("status", "status"), ("title", "title"))


def _ts(v: object) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _reset_credit_reading(ids: IdSource, instance_id: str, profile_id: str, envelope: dict[str, Any], observed_at: int) -> ResetCreditObserved:
    """`rateLimitResetCredits` -> evidence. Missing/null = UNKNOWN (never zero coupons); present+valid (also count 0) = MEASURED;
    anything malformed = ERROR with a reason only (no partial values). Free-text `description` is deliberately not stored."""
    def make(state: EvidenceState, payload: dict[str, Any]) -> ResetCreditObserved:
        return ResetCreditObserved(ids.new_id("reset-credit-observation"), instance_id, profile_id, state, "codex_app_server",
                                   observed_at, observed_at, _RESET_CREDIT_REF, payload)

    def bad(reason: str) -> ResetCreditObserved:
        return make(EvidenceState.ERROR, {"reason": reason})

    raw = envelope.get("rateLimitResetCredits")
    if raw is None:
        return make(EvidenceState.UNKNOWN, {"reason": "reset_credits_absent"})
    if not isinstance(raw, dict):
        return bad("reset_credits_not_an_object")
    obj = cast(dict[str, Any], raw)
    count = obj.get("availableCount")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return bad("available_count_invalid")
    items = obj.get("credits")
    if not isinstance(items, list):
        return bad("credits_not_a_list")
    credits: list[dict[str, Any]] = []
    for item in cast(list[Any], items):
        if not isinstance(item, dict):
            return bad("credit_not_an_object")
        c = cast(dict[str, Any], item)
        out: dict[str, Any] = {}
        for src, dst in _CREDIT_STR_FIELDS:
            v = c.get(src)
            if not isinstance(v, str) or not v:
                return bad(f"credit_{dst}_invalid")
            out[dst] = v
        granted, expires = _ts(c.get("grantedAt")), _ts(c.get("expiresAt"))
        if granted is None or expires is None:
            return bad("credit_timestamp_invalid")
        if expires < granted:
            return bad("credit_expires_before_granted")
        out["granted_at"], out["expires_at"] = granted, expires
        credits.append(out)
    credits.sort(key=lambda c: (c["expires_at"], c["id_ref"]))
    live = [c["expires_at"] for c in credits if c["status"] == "available"]
    return make(EvidenceState.MEASURED, {"available_count": count, "credits": credits, "nearest_expires_at": min(live) if live else None})


def poll_codex_usage(
    ids: IdSource,
    instance_id: str,
    profile_id: str,
    clock: Optional[Callable[[], float]] = None,
    deadline_sec: float = 12.0,
    freshness_ttl: int = 60,
    sys_dir: Optional[Path] = None,
    configured_pools: Sequence[str] | None = None,
    cancellation: ProbeCancellation | None = None,
) -> Sequence[UsageObserved | ResetCreditObserved]:
    """Poll codex app-server (JSON-RPC `account/rateLimits/read`, no model turn) and return observations for each quota pool,
    plus one reset-credit observation from the same response."""
    import threading
    import queue
    import json
    import time

    resolved_sys = common.resolve_sys_dir(sys_dir)
    workspace_root = common.resolve_workspace_root(resolved_sys)
    clock_fn = clock if clock else (lambda: datetime.now(timezone.utc).timestamp())
    observed_at = int(clock_fn())

    codex_cmd = common.real_command("cx", resolved_sys)
    if not codex_cmd:
        return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ABSENT, observed_at, freshness_ttl, peer="cx"),)

    proc = None
    try:
        proc = common.spawn_probe(
            cancellation,
            [*codex_cmd, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=str(workspace_root),
            start_new_session=sys.platform != "win32",
        )

        q: "queue.Queue[str | None]" = queue.Queue()

        def _reader() -> None:
            if proc.stdout is None:
                q.put(None)
                return
            try:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    q.put(line)
            except Exception:
                pass
            q.put(None)

        threading.Thread(target=_reader, daemon=True).start()
        deadline = time.monotonic() + deadline_sec

        def _wait_for_id(expected_id: int) -> Optional[dict[str, Any]]:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (cancellation is not None and cancellation.cancelled()):
                    return None
                try:
                    line = q.get(timeout=min(0.5, remaining))
                except queue.Empty:
                    continue
                if line is None:
                    return None
                try:
                    raw: object = json.loads(str(line))
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(raw, dict):
                    continue
                obj = cast(dict[str, Any], raw)
                if obj.get("id") == expected_id and isinstance(obj.get("result"), dict):
                    return obj["result"]

        if not proc.stdin:
            return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl, peer="cx"),)

        proc.stdin.write(json.dumps({
            "id": 0, "method": "initialize", "params": {
                "clientInfo": _CODEX_CLIENT_INFO,
                "capabilities": {"experimentalApi": True},
            },
        }) + "\n")
        proc.stdin.flush()

        if _wait_for_id(0) is None:
            extra = {"reason": "codex_usage_timeout"} if time.monotonic() >= deadline else None
            return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl, peer="cx", extra=extra),)

        proc.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        proc.stdin.write(json.dumps({
            "id": 1, "method": _RATE_LIMITS_READ_METHOD, "params": None,
        }) + "\n")
        proc.stdin.flush()

        rate_limits_envelope = _wait_for_id(1)
        if rate_limits_envelope is None:
            extra = {"reason": "codex_usage_timeout"} if time.monotonic() >= deadline else None
            return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl, peer="cx", extra=extra),)

        # account/rateLimits/read nests primary/secondary one level under
        # "rateLimits" (plus a duplicate under "rateLimitsByLimitId"); this
        # code previously read them off the envelope directly, so it always
        # found neither key and silently returned ERROR (confirmed against
        # a live response: correct rate-limit data was present, just nested).
        credit_reading = _reset_credit_reading(ids, instance_id, profile_id, rate_limits_envelope, observed_at)
        rate_limits_raw = rate_limits_envelope.get("rateLimits")
        rate_limits_by_id_raw = rate_limits_envelope.get("rateLimitsByLimitId")

        malformed_envelope = False
        limits_to_process: list[tuple[str, Any]]
        if isinstance(rate_limits_raw, dict):
            # Extract from primary/secondary
            rate_limits = cast(dict[str, Any], rate_limits_raw)
            limits_to_process = []
            for key in ("primary", "secondary"):
                q_limit_raw = rate_limits.get(key)
                if key not in rate_limits or (q_limit_raw is not None and not isinstance(q_limit_raw, dict)):
                    malformed_envelope = True
                if isinstance(q_limit_raw, dict):
                    limits_to_process.append((key, cast(dict[str, Any], q_limit_raw)))
        elif isinstance(rate_limits_by_id_raw, dict):
            # Fall back to rateLimitsByLimitId
            rate_limits_by_id = cast(dict[str, Any], rate_limits_by_id_raw)
            limits_to_process = []
            for limit_id, q_limit_raw in rate_limits_by_id.items():
                if isinstance(q_limit_raw, dict):
                    limits_to_process.append((limit_id, cast(dict[str, Any], q_limit_raw)))
        else:
            return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl, peer="cx"), credit_reading)

        results: list[UsageObserved | ResetCreditObserved] = []
        # X1: a configured pool may be recorded ABSENT only from a complete response; any window that is
        # present but unusable (or whose duration cannot be trusted) makes the whole response incomplete.
        malformed = malformed_envelope
        reported_labels: set[str] = set()
        for key, q_limit_raw in limits_to_process:
            q_limit = cast(dict[str, Any], q_limit_raw)
            label, window_hours = "X-UNK", 0.0
            duration_mins: object | None = q_limit.get("windowDurationMins")
            duration_ok = False
            if duration_mins is not None:
                try:
                    reported_hours = float(str(duration_mins)) / 60.0
                    if math.isfinite(reported_hours) and reported_hours > 0:
                        window_hours = reported_hours
                        duration_ok = True
                        if reported_hours <= 24:
                            label = f"X-{round(reported_hours)}H"
                        else:
                            label = f"X-{round(reported_hours / 24.0)}D"
                except (TypeError, ValueError, OverflowError):
                    pass
            if not duration_ok:
                malformed = True
                continue

            used_val: object | None = q_limit.get("usedPercent")
            if used_val is None:
                malformed = True
                continue  # absent usage is never manufactured as zero
            try:
                used = float(str(used_val))
            except (ValueError, TypeError):
                malformed = True
                continue
            if not math.isfinite(used) or not 0 <= used <= 100:
                malformed = True
                continue
            used_frac = used / 100.0

            resets_at: object | None = q_limit.get("resetsAt")
            if resets_at is None:
                malformed = True
                continue

            reset_at_ts = None
            is_numeric = isinstance(resets_at, (int, float))
            if not is_numeric and isinstance(resets_at, str):
                try:
                    float(resets_at.strip())
                    is_numeric = True
                except (TypeError, ValueError):
                    pass

            if is_numeric:
                try:
                    num = float(str(resets_at))
                    if abs(num) > 1e12:
                        num /= 1000.0
                    reset_at_ts = int(num)
                except Exception:
                    pass
            else:
                try:
                    reset_at_dt = datetime.fromisoformat(str(resets_at).replace("Z", "+00:00"))
                    reset_at_ts = int(reset_at_dt.timestamp())
                except Exception:
                    pass

            if reset_at_ts is None:
                malformed = True
                continue

            window_sec = int(window_hours * 3600)
            window_started_at = reset_at_ts - window_sec

            measurement = UsageMeasurement(
                quota_pool_scope=label,
                used_fraction=float(used_frac),
                remaining_fraction=max(0.0, 1.0 - float(used_frac)),
                window_started_at=window_started_at,
                resets_at=reset_at_ts,
            )

            evidence = EvidenceValue[UsageMeasurement](
                state=EvidenceState.MEASURED,
                source_tag="codex_app_server",
                provider_id="peerhub.telemetry.cx",
                provider_version="1.0",
                observed_at=observed_at,
                captured_at=observed_at,
                freshness_ttl=freshness_ttl,
                evidence_ref=EvidenceRef("probe:cx:app-server"),
                value=measurement,
            )

            results.append(
                UsageObserved(
                    observation_id=ids.new_id("usage-observation"),
                    instance_id=instance_id,
                    profile_id=profile_id,
                    evidence=evidence,
                )
            )
            reported_labels.add(label)

        if malformed:
            results.append(common.fail_closed(ids, instance_id, profile_id, EvidenceState.UNKNOWN, observed_at, freshness_ttl, peer="cx"))
        if not results:
            return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl, peer="cx"), credit_reading)

        if isinstance(rate_limits_raw, dict) and not malformed:
            plan = _plan_label(cast(dict[str, Any], rate_limits_raw).get("planType"))
            for pool_scope in (("X-5H", "X-7D") if configured_pools is None else configured_pools):
                if pool_scope in reported_labels:
                    continue
                results.append(common.fail_closed(
                    ids, instance_id, profile_id, EvidenceState.ABSENT, observed_at, freshness_ttl, peer="cx",
                    extra={"pool_scope": pool_scope, "reason": f"{NOT_REPORTED_REASON}; plan={plan}"},
                ))

        return (*results, credit_reading)
    except Exception:
        return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl, peer="cx"),)
    finally:
        if proc is not None:
            common.finish_probe(proc, cancellation)


def parse_codex_account(result: object) -> Optional[dict[str, Any]]:
    """app-server `account/read` result -> normalized payload, or None when it is not an object."""
    if not isinstance(result, dict):
        return None
    body = cast(dict[str, Any], result)
    if "account" not in body:
        return common.account_payload(common.CODEX_ACCOUNT_REF, logged_in="UNKNOWN", plan=None, plan_status="UNKNOWN")
    account_raw = body["account"]
    if account_raw is None:
        return common.account_payload(common.CODEX_ACCOUNT_REF, logged_in=False, plan=None, plan_status="n/a")
    if not isinstance(account_raw, dict):
        return common.account_payload(common.CODEX_ACCOUNT_REF, logged_in="UNKNOWN", plan=None, plan_status="UNKNOWN")
    account = cast(dict[str, Any], account_raw)
    kind = account_text(account.get("type"))
    if kind is None:
        return common.account_payload(common.CODEX_ACCOUNT_REF, logged_in="UNKNOWN", plan=None, plan_status="UNKNOWN")
    plan = account_text(account.get("planType")) if kind == "chatgpt" else None
    status = "reported" if plan else ("UNKNOWN" if kind == "chatgpt" else "n/a")
    return common.account_payload(common.CODEX_ACCOUNT_REF, logged_in=True, plan=plan, plan_status=status, auth_method=kind)


def poll_codex_account(*, clock: Optional[Callable[[], float]] = None, deadline_sec: float = 10.0,
                       sys_dir: Optional[Path] = None, cancellation: ProbeCancellation | None = None) -> AccountObserved:
    """codex app-server `account/read` (refreshToken false, no model turn) for the default account (the inherited CODEX_HOME)."""
    import queue
    import threading
    import time

    ref, source = common.CODEX_ACCOUNT_REF, "codex_app_server"
    observed_at = int((clock if clock else (lambda: datetime.now(timezone.utc).timestamp()))())
    resolved_sys = common.resolve_sys_dir(sys_dir)
    command = common.real_command("cx", resolved_sys)
    if not command:
        return common.account_failure(ref, source, observed_at, EvidenceState.UNAVAILABLE, "cli_not_found")
    env = os.environ.copy()
    proc = None
    try:
        try:
            proc = common.spawn_probe(cancellation, [*command, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, cwd=str(common.resolve_workspace_root(resolved_sys)), env=env,
                                    start_new_session=sys.platform != "win32")
        except Exception:
            return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "spawn_failed")
        lines: "queue.Queue[str | None]" = queue.Queue()
        stream = proc.stdout

        def read_lines() -> None:
            try:
                while stream is not None:
                    line = stream.readline()
                    if not line:
                        break
                    lines.put(line)
            except Exception:
                pass
            lines.put(None)

        threading.Thread(target=read_lines, daemon=True).start()
        deadline = time.monotonic() + deadline_sec

        def wait_for(expected: int) -> Optional[dict[str, Any]]:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (cancellation is not None and cancellation.cancelled()):
                    return None
                try:
                    line = lines.get(timeout=min(0.5, remaining))
                except queue.Empty:
                    continue
                if line is None:
                    return None
                try:
                    raw: object = json.loads(line)
                except ValueError:
                    continue
                if isinstance(raw, dict):
                    message = cast(dict[str, Any], raw)
                    if message.get("id") == expected and isinstance(message.get("result"), dict):
                        return cast(dict[str, Any], message["result"])

        if proc.stdin is None:
            return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "spawn_failed")
        proc.stdin.write(json.dumps({"id": 0, "method": "initialize", "params": {"clientInfo": _CODEX_CLIENT_INFO}}) + "\n")
        proc.stdin.flush()
        if wait_for(0) is None:
            return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "no_response")
        proc.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        proc.stdin.write(json.dumps({"id": 1, "method": "account/read", "params": {"refreshToken": False}}) + "\n")
        proc.stdin.flush()
        result = wait_for(1)
        if result is None:
            return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "no_response")
        payload = parse_codex_account(result)
        if payload is None:
            return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "unparseable")
        return AccountObserved(ref, EvidenceState.MEASURED, source, observed_at, payload)
    except Exception:
        return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "io_error")
    finally:
        if proc is not None:
            common.finish_probe(proc, cancellation)
