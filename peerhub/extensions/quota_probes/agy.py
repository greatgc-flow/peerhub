"""Antigravity usage and account probes, including statusline quota and known buckets."""
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, cast

import peerhub.extensions.win_job as win_job
from peerhub.extensions.observation_model import DISABLED_REASON, EvidenceState
from peerhub.extensions.quota_types import (
    EvidenceRef,
    EvidenceValue,
    IdSource,
    UsageMeasurement,
    UsageObserved,
)

from . import common
from .common import AccountObserved, ProbeCancellation


_AGY_ARGON_WEEKLY_POOL = "A-7D"
AGY_QUOTA_FAMILIES = (
    ("gemini-5h", "G-5H"),
    ("gemini-weekly", "G-7D"),
    ("3p-5h", "3P-5H"),
    ("3p-weekly", "3P-7D"),
    ("gemini-argon-wtu-weekly", _AGY_ARGON_WEEKLY_POOL),
)


AGY_USAGE_EXIT_GRACE_SECONDS = 2.0


_AGY_GROUP_PREFIX_MAP: dict[str, str] = {
    "gemini models": "G",
    "gemini": "G",
    "g": "G",
    "claude and gpt models": "3P",
    "claude": "3P",
    "3p": "3P",
    "gemini 4 argon": "A",
    "argon": "A",
    "a": "A",
}


def _derive_agy_pool_label(
    group_name: Optional[str],
    bucket_id: str,
    window: Optional[str] = None,
) -> Optional[str]:
    """Derive pool label (e.g. 'G-7D', '3P-5H', 'A-7D') from group name and window/id.

    Only registered bucket IDs get labels. Unfamiliar buckets return None to preserve
    their raw identity as drift.
    """
    known_ids = dict(AGY_QUOTA_FAMILIES)
    if bucket_id not in known_ids:
        return None

    prefix: Optional[str] = None
    if group_name:
        prefix = _AGY_GROUP_PREFIX_MAP.get(group_name.strip().lower())

    window_code: Optional[str] = None
    w = (window or "").strip().lower()
    bid = bucket_id.lower()
    if w == "weekly" or "weekly" in bid:
        window_code = "7D"
    elif w == "5h" or "5h" in bid:
        window_code = "5H"

    if prefix and window_code:
        if prefix == "A" and window_code != "7D":
            return None
        derived = f"{prefix}-{window_code}"
        if derived != known_ids[bucket_id]:
            return None
        return derived

    return known_ids.get(bucket_id)


def _disabled_description(value: object) -> str:
    """The provider's own words for a disabled window: printable ASCII only, no ';'/'=' (they delimit reason fields), <=100 chars."""
    text = re.sub(r"[^A-Za-z0-9 .,()'/%+-]", " ", value) if isinstance(value, str) else ""
    return " ".join(text.split())[:100].strip()


def _disabled_reason(q: dict[str, Any]) -> str:
    description = _disabled_description(q.get("description"))
    return f"{DISABLED_REASON}: {description}" if description else DISABLED_REASON


def _check_agy_bucket_conflict(key: str, label: str, q: dict[str, Any]) -> Optional[str]:
    """Return conflict description if envelope group/window contradicts registered bucket label."""
    parts = label.split("-")
    if len(parts) != 2:
        return None
    expected_prefix, expected_window = parts[0], parts[1]

    group_name = q.get("group")
    if group_name is not None and isinstance(group_name, str) and group_name.strip():
        prefix = _AGY_GROUP_PREFIX_MAP.get(group_name.strip().lower())
        if prefix is None:
            return f"bucket '{key}' registered as '{label}' appeared in unrecognized group '{group_name}'"
        if prefix != expected_prefix:
            return (
                f"bucket '{key}' registered as '{label}' (group prefix '{expected_prefix}') "
                f"appeared in group '{group_name}' (prefix '{prefix}')"
            )

    window = q.get("window")
    if window is not None and isinstance(window, str) and window.strip():
        w = window.strip().lower()
        w_code: Optional[str] = None
        if w == "weekly":
            w_code = "7D"
        elif w == "5h":
            w_code = "5H"
        if w_code is None:
            return f"bucket '{key}' registered as '{label}' appeared with unrecognized window '{window}'"
        if w_code != expected_window:
            return (
                f"bucket '{key}' registered as '{label}' (window '{expected_window}') "
                f"appeared with window '{window}' ('{w_code}')"
            )

    return None


def _parse_agy_usage_output(text: str) -> dict[str, dict[str, Any]]:
    """Extract quota buckets from ``agy -p /usage --output-format json``.

    Agy returns a normal print-mode envelope whose ``command`` member carries
    the lossless slash-command payload.  Parsing that member, rather than the
    human ``response`` table, preserves exact fractions and reset timestamps.
    The line-wise fallback tolerates launchers that prepend a diagnostic line.
    """

    candidates = [text.strip(), *(line.strip() for line in reversed(text.split("\n")))]  # LF only: U+2028 may sit inside a JSON string (#373)
    envelope: dict[str, Any] | None = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            raw = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(raw, dict):
            envelope = cast(dict[str, Any], raw)
            break
    if envelope is None:
        return {}

    command_raw = envelope.get("command")
    if not isinstance(command_raw, dict):
        return {}
    command = cast(dict[str, Any], command_raw)
    if command.get("name") != "usage":
        return {}
    data_raw = command.get("data")
    if not isinstance(data_raw, dict):
        return {}
    data = cast(dict[str, Any], data_raw)
    groups_raw = data.get("groups")
    if not isinstance(groups_raw, list):
        return {}
    groups = cast(list[Any], groups_raw)

    known_ids = dict(AGY_QUOTA_FAMILIES)
    buckets: dict[str, dict[str, Any]] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_dict = cast(dict[str, Any], group)
        group_name = group_dict.get("name")
        raw_buckets_raw = group_dict.get("buckets")
        if not isinstance(raw_buckets_raw, list):
            continue
        raw_buckets = cast(list[Any], raw_buckets_raw)
        for bucket_raw in raw_buckets:
            if not isinstance(bucket_raw, dict):
                continue
            bucket = cast(dict[str, Any], bucket_raw)
            bucket_id = bucket.get("id")
            remaining = bucket.get("remaining_fraction")
            reset_time = bucket.get("reset_time")
            window = bucket.get("window")
            window_str = str(window) if isinstance(window, str) else None
            group_name_str = str(group_name) if isinstance(group_name, str) else None

            if isinstance(bucket_id, str) and bucket_id in known_ids and bucket.get("disabled") is True:
                # The provider switched this window off (e.g. 5h limit while the weekly limit is hit): it carries no usable
                # fraction or reset, so keep only the flag and its description and let the poller record it as not applicable.
                disabled_entry: dict[str, Any] = {"disabled": True, "description": bucket.get("description")}
                if window_str is not None:
                    disabled_entry["window"] = window_str
                if group_name_str is not None:
                    disabled_entry["group"] = group_name_str
                buckets[bucket_id] = disabled_entry
                continue
            if isinstance(bucket_id, str) and bucket_id in known_ids and not common.valid_fraction(remaining):
                return {}
            if (
                not isinstance(bucket_id, str)
                or not common.valid_fraction(remaining)
                or not isinstance(reset_time, str)
            ):
                continue
            entry: dict[str, Any] = {
                "remaining_fraction": float(remaining),
                "reset_time": reset_time,
            }
            label = _derive_agy_pool_label(group_name_str, bucket_id, window_str)
            if label is not None:
                entry["label"] = label
                entry["pool"] = label
            if window_str is not None:
                entry["window"] = window_str
            if group_name_str is not None:
                entry["group"] = group_name_str
            buckets[bucket_id] = entry
    return buckets


def _agy_report_is_clean(text: str) -> bool:
    """True only if every group/bucket of the `/usage` payload is well formed (the parser silently skips malformed ones),
    so a bucket missing from the parsed dict really means the provider did not report it."""
    env = common.json_object_from_output(text)
    command = env.get("command") if env else None
    data = cast(dict[str, Any], command).get("data") if isinstance(command, dict) else None
    groups = cast(dict[str, Any], data).get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return False
    for group in cast(list[Any], groups):
        buckets = cast(dict[str, Any], group).get("buckets") if isinstance(group, dict) else None
        if not isinstance(buckets, list):
            return False
        for bucket in cast(list[Any], buckets):
            if not isinstance(bucket, dict):
                return False
            b = cast(dict[str, Any], bucket)
            bucket_id = b.get("id")
            if b.get("disabled") is True and isinstance(bucket_id, str) and bucket_id in dict(AGY_QUOTA_FAMILIES):
                continue  # a well-formed "switched off" bucket carries no fraction or reset
            if not isinstance(b.get("id"), str) or not common.valid_fraction(b.get("remaining_fraction")) or not isinstance(b.get("reset_time"), str):
                return False
    return True


def _agy_usage_violation(text: str, command_name: str = "usage") -> Optional[tuple[str, dict[str, Any]]]:
    """Evaluate the RAW print-mode envelope of `agy -p /usage`: a proper slash-command run has command.name == "usage", zero tokens and
    zero turns. Anything else means the text may have gone to a real model turn (quota burn) or is not the command: (evidence_ref, extra)
    or None when the run is clean/unparseable (unparseable keeps the legacy non-JSON behaviour)."""
    env = common.json_object_from_output(text)
    if env is None:
        return None
    usage_raw, command_raw = env.get("usage"), env.get("command")
    tokens: object = cast(dict[str, Any], usage_raw).get("total_tokens") if isinstance(usage_raw, dict) else None
    turns: object = env.get("num_turns")
    extra: dict[str, Any] = {}
    if isinstance(tokens, (int, float)) and not isinstance(tokens, bool):
        extra["consumed_tokens"] = tokens
    if isinstance(turns, (int, float)) and not isinstance(turns, bool):
        extra["num_turns"] = turns
    consumed = (isinstance(tokens, (int, float)) and not isinstance(tokens, bool) and not tokens <= 0) or                (isinstance(turns, (int, float)) and not isinstance(turns, bool) and not turns <= 0)
    if consumed:
        extra["reason"] = "agy_usage_consumed_tokens"
        extra["warning"] = (f"agy /usage consumed tokens (total_tokens={tokens!r}, num_turns={turns!r}): a model turn was dispatched, "
                            "the response is NOT used as quota")
        return "agy_usage_consumed_tokens", extra
    for name, v in (("total_tokens", tokens), ("num_turns", turns)):
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
            extra["reason"] = "agy_usage_unverifiable_tokens"
            extra["warning"] = f"agy /usage reported a non-numeric {name}; token consumption cannot be ruled out"
            return "agy_usage_unverifiable_tokens", extra
    if not isinstance(command_raw, dict) or cast(dict[str, Any], command_raw).get("name") != command_name:
        extra["reason"] = "agy_usage_not_a_command"
        extra["warning"] = f"agy /{command_name} was not recognised as a slash command (possible model turn); the response is NOT used as quota"
        return "agy_usage_not_a_command", extra
    return None


def _agy_cwd_refusal(cwd: Path) -> Optional[str]:
    """Pre-spawn guard: the slash command only exists in the workspace context; elsewhere agy runs a paid model turn."""
    try:
        if not cwd.is_dir():
            return f"agy probe cwd is not an existing directory: {cwd}"
        if cwd.resolve() == Path(tempfile.gettempdir()).resolve():
            return f"agy probe cwd is the system temp directory: {cwd}"
    except OSError as exc:
        return f"agy probe cwd cannot be inspected: {type(exc).__name__}"
    return None


def poll_agy_usage(
    ids: IdSource,
    instance_id: str,
    profile_id: str,
    clock: Optional[Callable[[], float]] = None,
    deadline_sec: float = 15.0,
    freshness_ttl: int = 60,
    log_path: Optional[str | Path] = None,
    sys_dir: Optional[Path] = None,
    configured_pools: Sequence[str] | None = None,
    cancellation: ProbeCancellation | None = None,
) -> Sequence[UsageObserved]:
    """Poll Agy's local ``/usage`` command, with explicitly configured statusline fallback.

    The slash command returns account quota without dispatching a model turn.
    Current Agy reports zero input/output/thinking tokens for this operation.
    An explicitly supplied ``log_path`` skips the executable probe.

    A missing declared bucket or legacy Argon weekly bucket in a complete ``/usage`` report is recorded ABSENT.
    The statusline fallback, conflicts and any present-but-unusable bucket never infer missing buckets.
    """

    clock_fn = clock if clock else (lambda: datetime.now(timezone.utc).timestamp())
    observed_at_now = int(clock_fn())

    _AG_QUOTA_LABELS = dict(AGY_QUOTA_FAMILIES)

    resolved_sys = common.resolve_sys_dir(sys_dir)
    quota_dict_typed: dict[str, Any] | None = None
    report_clean = False  # the live /usage payload had no malformed bucket (needed before any ABSENT pool)
    evidence_observed_at = observed_at_now
    source_tag = "agy_cli_usage"
    evidence_ref = EvidenceRef("probe:ag:usage")
    timed_out = False

    if log_path is None and "PEERHUB_AG_STATUSLINE_LOG" not in os.environ:
        agy_cmd = common.real_command("ag", resolved_sys)
        if agy_cmd:
            agy_cwd = common.resolve_workspace_root(resolved_sys)
            refusal = _agy_cwd_refusal(agy_cwd)
            if refusal is not None:  # nothing is spawned
                return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at_now, freshness_ttl, peer="ag",
                                           evidence_ref_override="agy_usage_cwd_refused",
                                           extra={"reason": "agy_usage_cwd_refused", "warning": refusal}),)
            try:
                completed = common.run_text_probe(
                    [
                        *agy_cmd,
                        "-p",
                        "/usage",
                        "--output-format",
                        "json",
                        "--print-timeout",
                        f"{max(1, int(deadline_sec))}s",
                    ],
                    cwd=str(agy_cwd),
                    stdin=subprocess.DEVNULL,
                    errors="replace",
                    timeout=deadline_sec + AGY_USAGE_EXIT_GRACE_SECONDS if cancellation is None else deadline_sec,
                    cancellation=cancellation,
                )
                violation = _agy_usage_violation(completed.stdout)  # whatever the exit code: tokens may be spent either way
                if violation is not None:
                    ref, extra = violation
                    return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at_now, freshness_ttl, peer="ag",
                                               evidence_ref_override=ref, extra=extra),)
                if completed.returncode == 0:
                    parsed = _parse_agy_usage_output(completed.stdout)
                    if parsed:
                        quota_dict_typed = parsed
                        report_clean = _agy_report_is_clean(completed.stdout)
            except subprocess.TimeoutExpired:
                timed_out = True
            except win_job.ProbeResumeError:
                return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at_now, freshness_ttl,
                                           peer="ag", extra={"reason": "agy_usage_spawn_failed"}),)
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
    if quota_dict_typed is None:
        configured_log = log_path if log_path is not None else os.environ.get("PEERHUB_AG_STATUSLINE_LOG")
        if configured_log is None and sys_dir is not None:
            configured_log = resolved_sys / "data" / "temp" / "ag_statusline_stdin.log"
        if timed_out and (configured_log is None or not Path(configured_log).is_file()):
            return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at_now, freshness_ttl,
                                       peer="ag", evidence_ref_override="probe:ag:usage", extra={"reason": "agy_usage_timeout"}),)
        if configured_log is None:
            return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.UNKNOWN, observed_at_now, freshness_ttl, peer="ag"),)
        path = Path(configured_log)
        try:
            st = path.stat()
            mtime = int(st.st_mtime)
        except OSError:
            return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ABSENT, observed_at_now, freshness_ttl, peer="ag"),)

        if observed_at_now - mtime > freshness_ttl:
            return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.STALE, mtime, freshness_ttl, peer="ag"),)

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, mtime, freshness_ttl, peer="ag"),)

        quota_dict = data.get("quota")
        if not isinstance(quota_dict, dict):
            return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, mtime, freshness_ttl, peer="ag"),)
        quota_dict_typed = cast(dict[str, Any], quota_dict)
        evidence_observed_at = mtime
        source_tag = "agy_statusline"
        evidence_ref = EvidenceRef("probe:ag:statusline")

    unknown_buckets = [b for b in quota_dict_typed.keys() if b not in _AG_QUOTA_LABELS]
    for key, label in _AG_QUOTA_LABELS.items():
        q_raw = quota_dict_typed.get(key)
        if isinstance(q_raw, dict):
            q_check = cast(dict[str, Any], q_raw)
            if _check_agy_bucket_conflict(key, label, q_check) is not None:
                if key not in unknown_buckets:
                    unknown_buckets.append(key)

    results: list[UsageObserved] = []
    malformed = False
    disabled_pools: set[str] = set()

    for key, label in _AG_QUOTA_LABELS.items():
        q_raw = quota_dict_typed.get(key)
        if isinstance(q_raw, dict) and cast(dict[str, Any], q_raw).get("disabled") is True and _check_agy_bucket_conflict(key, label, cast(dict[str, Any], q_raw)) is None:
            # Provider-disabled window: not applicable (ABSENT), with the provider's description as provenance; the other pools stay measured.
            results.append(common.fail_closed(
                ids, instance_id, profile_id, EvidenceState.ABSENT, evidence_observed_at, freshness_ttl, peer="ag",
                evidence_ref_override=str(evidence_ref), source_tag_override=source_tag,
                extra={"pool_scope": label, "reason": _disabled_reason(cast(dict[str, Any], q_raw))}))
            disabled_pools.add(label)
            continue
        if not isinstance(q_raw, dict):
            if key in quota_dict_typed:
                malformed = True  # present but unusable: completeness cannot be claimed
            continue
        q = cast(dict[str, Any], q_raw)

        conflict_desc = _check_agy_bucket_conflict(key, label, q)
        if conflict_desc is not None:
            results.append(
                common.fail_closed(
                    ids,
                    instance_id,
                    profile_id,
                    EvidenceState.ERROR,
                    evidence_observed_at,
                    freshness_ttl,
                    peer="ag",
                    evidence_ref_override="probe:ag:conflict",
                    extra={
                        "reason": "envelope_consistency_conflict",
                        "bucket": key,
                        "conflict": conflict_desc,
                        "unknown_pools": list(unknown_buckets),
                    },
                )
            )
            continue

        rem = q.get("remaining_fraction")
        if not common.valid_fraction(rem):
            return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, evidence_observed_at, freshness_ttl,
                                       peer="ag", extra={"reason": "invalid_remaining_fraction"}),)

        remaining_frac = float(rem)
        used_frac = 1.0 - remaining_frac

        window_hours = 5.0 if "5H" in label else 168.0
        window_sec = int(window_hours * 3600)

        reset_sec = q.get("reset_in_seconds")
        resets_at_iso = q.get("reset_time")


        reset_at_ts = None
        if isinstance(reset_sec, (int, float)):
            reset_at_ts = evidence_observed_at + int(reset_sec)
        elif isinstance(resets_at_iso, str):
            try:
                dt = datetime.fromisoformat(resets_at_iso.replace("Z", "+00:00"))
                reset_at_ts = int(dt.timestamp())
            except Exception:
                pass

        if reset_at_ts is None:
            malformed = True
            continue

        window_started_at = reset_at_ts - window_sec

        measurement = UsageMeasurement(
            quota_pool_scope=label,
            used_fraction=float(used_frac),
            remaining_fraction=float(remaining_frac),
            window_started_at=window_started_at,
            resets_at=reset_at_ts,
        )

        evidence = EvidenceValue[UsageMeasurement](
            state=EvidenceState.MEASURED,
            source_tag=source_tag,
            provider_id="peerhub.telemetry.ag",
            provider_version="1.0",
            observed_at=evidence_observed_at,
            captured_at=observed_at_now,
            freshness_ttl=freshness_ttl,
            evidence_ref=evidence_ref,
            value=measurement,
        )

        obs_extra: dict[str, Any] = {}
        if unknown_buckets:
            obs_extra["unknown_pools"] = list(unknown_buckets)
        results.append(
            UsageObserved(
                observation_id=ids.new_id("usage-observation"),
                instance_id=instance_id,
                profile_id=profile_id,
                evidence=evidence,
                extra=obs_extra,
            )
        )

    if not results:
        extra_err: dict[str, Any] = {}
        if unknown_buckets:
            extra_err["unknown_pools"] = list(unknown_buckets)
        return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, evidence_observed_at, freshness_ttl, peer="ag", extra=extra_err),)

    # An unknown bucket id / drift means the vocabulary changed (a "missing" pool may only be renamed): infer nothing.
    if (source_tag == "agy_cli_usage" and report_clean and not malformed and not unknown_buckets
            and all(r.evidence.state == EvidenceState.MEASURED or r.extra.get("pool_scope") in disabled_pools for r in results)):
        reported = {r.evidence.value.quota_pool_scope for r in results if r.evidence.value is not None} | disabled_pools
        # Clear past Argon weekly evidence even when no current profile uses the legacy bucket.
        expected = (_AG_QUOTA_LABELS.values() if configured_pools is None
                    else dict.fromkeys((*configured_pools, _AGY_ARGON_WEEKLY_POOL)))
        for pool_scope in expected:
            if pool_scope not in reported:
                results.append(common.absent_pool(ids, instance_id, profile_id, "ag", observed_at_now, freshness_ttl, pool_scope))

    return tuple(results)


def poll_agy_account(*, clock: Optional[Callable[[], float]] = None, deadline_sec: float = 10.0,
                     sys_dir: Optional[Path] = None, cancellation: ProbeCancellation | None = None) -> Optional[AccountObserved]:
    """`agy -p /credits --output-format json` (envelope verified like /usage: zero tokens, zero turns, a real slash command).
    Success proves endpoint access only: login, plan and identity stay UNKNOWN / n/a (the Gemini accounts file is never read).
    None when the statusline override is active (no spawning probe is wanted then)."""
    ref, source = common.AGY_ACCOUNT_REF, "agy_cli_credits"
    if "PEERHUB_AG_STATUSLINE_LOG" in os.environ:
        return None
    observed_at = int((clock if clock else (lambda: datetime.now(timezone.utc).timestamp()))())
    resolved_sys = common.resolve_sys_dir(sys_dir)
    command = common.real_command("ag", resolved_sys)
    if not command:
        return common.account_failure(ref, source, observed_at, EvidenceState.UNAVAILABLE, "cli_not_found")
    cwd = common.resolve_workspace_root(resolved_sys)
    refusal = _agy_cwd_refusal(cwd)
    if refusal is not None:  # nothing is spawned: outside the workspace the slash command would be a paid model turn
        return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "cwd_refused", refusal)
    try:
        completed = common.run_text_probe([*command, "-p", "/credits", "--output-format", "json", "--print-timeout", f"{max(1, int(deadline_sec))}s"],
                                   cwd=str(cwd), stdin=subprocess.DEVNULL, errors="replace",
                                   timeout=deadline_sec, cancellation=cancellation)
    except subprocess.TimeoutExpired:
        return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "timeout")
    except (OSError, subprocess.SubprocessError, ValueError):
        return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "spawn_failed")
    violation = _agy_usage_violation(completed.stdout, "credits")  # whatever the exit code: tokens may be spent either way
    if violation is not None:
        violation_ref, extra = violation
        return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, str(extra.get("reason") or "envelope_violation"),
                                      str(extra.get("warning") or "") or None, evidence_ref=violation_ref)
    if completed.returncode != 0:
        return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "nonzero_exit")
    envelope = common.json_object_from_output(completed.stdout)
    if envelope is None:
        return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "unparseable")
    if str(envelope.get("status") or "").upper() != "SUCCESS":
        return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "command_failed")
    usage = envelope.get("usage")
    tokens: object = cast(dict[str, Any], usage).get("total_tokens") if isinstance(usage, dict) else None
    turns: object = envelope.get("num_turns")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0 for v in (tokens, turns)):
        return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "unverified_usage")  # endpoint access needs explicit zero usage
    payload = common.account_payload(ref, logged_in="UNKNOWN", plan=None, plan_status="n/a", endpoint_access="ok")
    return AccountObserved(ref, EvidenceState.MEASURED, source, observed_at, payload)
