"""Provider quota probes migrated from v0; collection only, no legacy projection writes."""
import os
import re
import json
import math
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Sequence, Optional, TypedDict, Callable, cast, Any, TypeGuard

from peerhub.extensions.binary_resolution import CLAUDE_CMD, CODEX_CMD
from peerhub.extensions.process_tree import kill_process_tree
from peerhub.extensions.quota_types import IdSource, EvidenceValue, EvidenceState, EvidenceRef, UsageObserved, UsageMeasurement, ResetCreditObserved

AGY_QUOTA_FAMILIES = (("gemini-5h", "G-5H"), ("gemini-weekly", "G-7D"), ("3p-5h", "3P-5H"), ("3p-weekly", "3P-7D"))

_CODEX_CLIENT_INFO = {"name": "hub-credit", "version": "1.0"}
_RATE_LIMITS_READ_METHOD = "account/rateLimits/read"
"""Codex app-server JSON-RPC constants, shared with codex_credit.py (which
already reuses other private helpers from this module -- see its own
import comment)."""


def resolve_sys_dir(sys_dir: Optional[Path] = None) -> Path:
    """Resolve the _sys directory from explicit parameter, env var, or workspace-relative default.

    No hard-coded drive letters or Engram-specific paths.
    """
    if sys_dir is not None:
        return sys_dir

    env_sys = os.environ.get("PEERHUB_SYS_DIR")
    if env_sys:
        p = Path(env_sys)
        if p.exists() and p.is_dir():
            return p

    return Path.cwd() / "_sys"


def _resolve_workspace_root(sys_dir: Path) -> Path:
    """Derive workspace root from _sys dir (parent of _sys)."""
    return sys_dir.parent

_CLAUDE_USAGE_SECTIONS = {
    "current session": ("C-5H", 5.0),
    "current week (all models)": ("C-7D", 168.0),
    "current week (fable)": ("F-7D", 168.0),
}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

class ClaudeQuotaDict(TypedDict):
    label: str
    used_frac: float
    reset_at: datetime
    window_hours: float

def _parse_claude_usage_reset(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    if not text.strip():
        return None
    now = now or datetime.now().astimezone()
    value = text.strip()
    tz_name = None
    m_tz = re.search(r"\(([^)]+)\)\s*$", value)
    if m_tz:
        tz_name = m_tz.group(1).strip()
        value = value[:m_tz.start()].strip()
    try:
        tz = ZoneInfo(tz_name) if tz_name else now.tzinfo
    except Exception:
        tz = now.tzinfo

    m_date = re.match(
        r"^(?:(?P<mon>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s*)?"
        r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[ap]m)$",
        value,
        re.IGNORECASE,
    )
    if not m_date:
        return None
    mon_text = m_date.group("mon")
    if mon_text:
        month = _MONTHS.get(mon_text.lower())
        day = int(m_date.group("day"))
    else:
        month = now.month
        day = now.day
    if not month:
        return None
    hour = int(m_date.group("hour"))
    minute = int(m_date.group("minute") or 0)
    ampm = m_date.group("ampm").lower()
    if hour == 12:
        hour = 0
    if ampm == "pm":
        hour += 12
    try:
        dt = datetime(now.year, month, day, hour, minute, tzinfo=tz)
    except ValueError:
        return None
    now_in_tz = now.astimezone(tz)
    if mon_text and dt < now_in_tz - timedelta(days=30):
        try:
            dt = datetime(now.year + 1, month, day, hour, minute, tzinfo=tz)
        except ValueError:
            pass
    elif not mon_text and dt < now_in_tz:
        dt = dt + timedelta(days=1)
    return dt

def _claude_usage_emit(section: str, used_pct: str | float, reset_text: str, now: Optional[datetime] = None) -> Optional[ClaudeQuotaDict]:
    key = str(section or "").strip().lower()
    spec = _CLAUDE_USAGE_SECTIONS.get(key)
    if not spec:
        return None
    reset_at = _parse_claude_usage_reset(reset_text, now=now)
    if reset_at is None:
        return None
    try:
        if isinstance(used_pct, bool):
            return None
        pct = float(used_pct)
        if not math.isfinite(pct) or not 0.0 <= pct <= 100.0:
            return None
        used_frac = pct / 100.0
    except (TypeError, ValueError, OverflowError):
        return None
    label, window_hours = spec
    return {
        "label": label,
        "used_frac": used_frac,
        "reset_at": reset_at,
        "window_hours": window_hours,
    }

def _parse_claude_usage(text: str, now: Optional[datetime] = None) -> list[ClaudeQuotaDict]:
    if not text.strip():
        return []
    rows: list[ClaudeQuotaDict] = []
    current_section = None
    current_pct = None
    inline = re.compile(
        r"^(Current session|Current week \(all models\)|Current week \(Fable\)):"
        r"\s*([^\s%]+)%\s+used\b.*?\bresets\s+(.+)$",
        re.IGNORECASE,
    )
    section_names = {k.lower(): k for k in _CLAUDE_USAGE_SECTIONS}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = inline.match(line)
        if m:
            row = _claude_usage_emit(m.group(1), m.group(2), m.group(3), now=now)
            if row is None:
                return []
            rows.append(row)
            current_section = None
            current_pct = None
            continue
        lowered = line.lower().rstrip(":")
        if lowered in section_names:
            current_section = section_names[lowered]
            current_pct = None
            continue
        if current_section and current_pct is None:
            m_pct = re.search(r"([^\s%]+)%\s+used\b", line, re.IGNORECASE)
            if m_pct:
                current_pct = m_pct.group(1)
            continue
        if current_section and current_pct is not None:
            m_reset = re.search(r"\bresets?\s+(.+)$", line, re.IGNORECASE)
            if m_reset:
                row = _claude_usage_emit(current_section, current_pct, m_reset.group(1), now=now)
                if row is None:
                    return []
                rows.append(row)
                current_section = None
                current_pct = None
    return rows

def _real_binary(peer: str, sys_dir: Optional[Path] = None) -> Optional[str]:
    names = {"ag": ("agy.exe", "agy"), "cc": (CLAUDE_CMD, "claude"), "cx": (CODEX_CMD, "codex")}
    if peer not in names:
        return None
    override_key = f"PEERHUB_{peer.upper()}_BINARY"
    override = os.environ.get(override_key)
    if override is not None:
        cand = Path(override)
        if not override or not cand.is_file():
            raise FileNotFoundError(f"{override_key} points to a missing file: {override!r}")
    else:
        npm = os.environ.get("PEERHUB_NPM_GLOBAL_DIR")
        configured = Path(npm) / names[peer][0] if npm and peer in ("cc", "cx") else None
        discovered = str(configured) if configured and configured.is_file() else next(
            (found for name in names[peer] if (found := shutil.which(name))), None)
        if discovered is None:
            return None
        cand = Path(discovered)
    resolved = cand.resolve()
    cli_dir = (resolve_sys_dir(sys_dir) / "cli").resolve()
    if resolved == cli_dir or cli_dir in resolved.parents:
        raise RuntimeError(f"refusing wrapper binary for {peer}: {resolved}")
    return str(cand)  # keep literal paths: resolved junction targets may contain cmd.exe metacharacters


def _real_command(peer: str, sys_dir: Optional[Path] = None) -> Optional[list[str]]:
    raw_bin = _real_binary(peer, sys_dir)
    if not raw_bin:
        return None
    cand = Path(raw_bin)
    from peerhub.extensions.binary_resolution import resolve_direct_binary
    if peer in ("cc", "cx"):
        if cand.suffix.lower() == ".cmd":
            result = resolve_direct_binary(cand)
            if result is not None:
                return result
        return [raw_bin]
    return [raw_bin]


def _cleanup_probe(proc: subprocess.Popen[str]) -> None:
    try:
        kill_process_tree(proc)
    except Exception:
        pass  # best-effort cleanup must not discard the probe result
    if sys.platform != "win32":
        return
    # Windows retains PPID after wrapper exit; preserve cleanup of that lineage only.
    try:
        import psutil
        for child in psutil.process_iter(["pid", "ppid"]):
            try:
                if child.info.get("ppid") == proc.pid:
                    kill_process_tree(cast(Any, child))
            except Exception:
                continue
    except Exception:
        pass


def _valid_fraction(value: object) -> TypeGuard[int | float]:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0.0 <= value <= 1.0 and math.isfinite(value))


def _parse_agy_usage_output(text: str) -> dict[str, dict[str, Any]]:
    """Extract quota buckets from ``agy -p /usage --output-format json``.

    Agy returns a normal print-mode envelope whose ``command`` member carries
    the lossless slash-command payload.  Parsing that member, rather than the
    human ``response`` table, preserves exact fractions and reset timestamps.
    The line-wise fallback tolerates launchers that prepend a diagnostic line.
    """

    candidates = [text.strip(), *(line.strip() for line in reversed(text.splitlines()))]
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
            if isinstance(bucket_id, str) and bucket_id in known_ids and not _valid_fraction(remaining):
                return {}
            if (
                not isinstance(bucket_id, str)
                or bucket_id not in known_ids
                or not _valid_fraction(remaining)
                or not isinstance(reset_time, str)
            ):
                continue
            buckets[bucket_id] = {
                "remaining_fraction": float(remaining),
                "reset_time": reset_time,
            }
    return buckets

def _agy_envelope(text: str) -> Optional[dict[str, Any]]:
    """First JSON object in agy's stdout (whole text, else last-line-first, tolerating a launcher prefix line)."""
    for candidate in (text.strip(), *(line.strip() for line in reversed(text.splitlines()))):
        if not candidate:
            continue
        try:
            raw = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(raw, dict):
            return cast(dict[str, Any], raw)
    return None


def _agy_usage_violation(text: str) -> Optional[tuple[str, dict[str, Any]]]:
    """Evaluate the RAW print-mode envelope of `agy -p /usage`: a proper slash-command run has command.name == "usage", zero tokens and
    zero turns. Anything else means the text may have gone to a real model turn (quota burn) or is not the command: (evidence_ref, extra)
    or None when the run is clean/unparseable (unparseable keeps the legacy non-JSON behaviour)."""
    env = _agy_envelope(text)
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
    if not isinstance(command_raw, dict) or cast(dict[str, Any], command_raw).get("name") != "usage":
        extra["reason"] = "agy_usage_not_a_command"
        extra["warning"] = "agy /usage was not recognised as a slash command (possible model turn); the response is NOT used as quota"
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


def _fail_closed(
    ids: IdSource,
    instance_id: str,
    profile_id: str,
    state: EvidenceState,
    observed_at: int,
    freshness_ttl: int,
    peer: str = "cc",
    evidence_ref_override: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> UsageObserved:
    if peer == "cx":
        source_tag = "codex_app_server"
        provider_id = "peerhub.telemetry.cx"
        evidence_ref = EvidenceRef("probe:cx:app-server")
    elif peer == "ag":
        source_tag = "agy_statusline"
        provider_id = "peerhub.telemetry.ag"
        evidence_ref = EvidenceRef("probe:ag:statusline")
    else:
        source_tag = "claude_cli_usage"
        provider_id = "peerhub.telemetry.cc"
        evidence_ref = EvidenceRef("probe:cc:usage")

    evidence = EvidenceValue[UsageMeasurement](
        state=state,
        source_tag=source_tag,
        provider_id=provider_id,
        provider_version="1.0",
        observed_at=observed_at,
        captured_at=observed_at,
        freshness_ttl=freshness_ttl,
        evidence_ref=EvidenceRef(evidence_ref_override) if evidence_ref_override else evidence_ref,
        value=None,
    )
    return UsageObserved(
        observation_id=ids.new_id("usage-observation"),
        instance_id=instance_id,
        profile_id=profile_id,
        evidence=evidence,
        extra=dict(extra or {}),
    )


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

def poll_claude_usage(
    ids: IdSource,
    instance_id: str,
    profile_id: str,
    clock: Optional[Callable[[], float]] = None,
    deadline_sec: float = 15.0,
    freshness_ttl: int = 60,
    sys_dir: Optional[Path] = None,
) -> Sequence[UsageObserved]:
    """Poll claude.cmd /usage and return observations for each quota pool."""
    resolved_sys = resolve_sys_dir(sys_dir)
    workspace_root = _resolve_workspace_root(resolved_sys)
    clock_fn = clock if clock else (lambda: datetime.now(timezone.utc).timestamp())
    observed_at = int(clock_fn())
    
    claude_cmd = _real_command("cc", resolved_sys)
    if not claude_cmd:
        return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ABSENT, observed_at, freshness_ttl),)

    env = os.environ.copy()
    claude_cfg_dir = (
        os.environ.get("PEERHUB_CLAUDE_CONFIG_DIR")
        or env.get("CLAUDE_CONFIG_DIR")
    )
    if claude_cfg_dir:
        env["CLAUDE_CONFIG_DIR"] = claude_cfg_dir

    # Direct binary invocation (bypassing claude.cmd wrapper per pattern a)
    # avoids both cmd.exe '&' splitting and orphaned grandchild process leaks.
    proc = None
    try:
        proc = subprocess.Popen(
            [*claude_cmd, "/usage"],
            cwd=str(workspace_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=sys.platform != "win32",
        )
        try:
            stdout, stderr = proc.communicate(timeout=deadline_sec)
        except subprocess.TimeoutExpired:
            return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl,
                                 extra={"reason": "claude_usage_timeout"}),)
    except Exception as exc:
        return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl,
                             extra={"reason": f"claude_usage_spawn_failed:{type(exc).__name__}"}),)
    finally:
        if proc is not None:
            _cleanup_probe(proc)

    text = "\n".join(str(part) for part in (stdout, stderr) if part)
    dt_now = datetime.fromtimestamp(observed_at, tz=timezone.utc).astimezone()
    quotas = _parse_claude_usage(text, now=dt_now)
    
    if not quotas:
        return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl,
                             extra={"reason": "claude_usage_unparseable"}),)
        
    results: list[UsageObserved] = []
    for q in quotas:
        reset_at_dt = q["reset_at"]
        reset_at_ts = int(reset_at_dt.timestamp())
        window_sec = int(float(q["window_hours"]) * 3600)
        window_started_at = reset_at_ts - window_sec
        
        measurement = UsageMeasurement(
            quota_pool_scope=q["label"],
            used_fraction=float(q["used_frac"]),
            remaining_fraction=max(0.0, 1.0 - float(q["used_frac"])),
            window_started_at=window_started_at,
            resets_at=reset_at_ts,
        )
        
        evidence = EvidenceValue[UsageMeasurement](
            state=EvidenceState.MEASURED,
            source_tag="claude_cli_usage",
            provider_id="peerhub.telemetry.cc",
            provider_version="1.0",
            observed_at=observed_at,
            captured_at=observed_at,
            freshness_ttl=freshness_ttl,
            evidence_ref=EvidenceRef("probe:cc:usage"),
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
        
    return tuple(results)

def poll_codex_usage(
    ids: IdSource,
    instance_id: str,
    profile_id: str,
    clock: Optional[Callable[[], float]] = None,
    deadline_sec: float = 12.0,
    freshness_ttl: int = 60,
    sys_dir: Optional[Path] = None,
) -> Sequence[UsageObserved | ResetCreditObserved]:
    """Poll codex app-server (JSON-RPC `account/rateLimits/read`, no model turn) and return observations for each quota pool,
    plus one reset-credit observation from the same response."""
    import threading
    import queue
    import json
    import time

    resolved_sys = resolve_sys_dir(sys_dir)
    workspace_root = _resolve_workspace_root(resolved_sys)
    clock_fn = clock if clock else (lambda: datetime.now(timezone.utc).timestamp())
    observed_at = int(clock_fn())

    codex_cmd = _real_command("cx", resolved_sys)
    if not codex_cmd:
        return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ABSENT, observed_at, freshness_ttl, peer="cx"),)

    proc = None
    try:
        proc = subprocess.Popen(
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
                    if not line or proc.poll() is not None:
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
                if remaining <= 0:
                    return None
                try:
                    line = q.get(timeout=min(0.5, remaining))
                except queue.Empty:
                    continue
                if line is None:
                    return None
                try:
                    obj = json.loads(str(line))
                except (json.JSONDecodeError, ValueError):
                    continue
                if obj.get("id") == expected_id and isinstance(obj.get("result"), dict):
                    return obj["result"]

        if not proc.stdin:
            return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl, peer="cx"),)

        proc.stdin.write(json.dumps({
            "id": 0, "method": "initialize", "params": {
                "clientInfo": _CODEX_CLIENT_INFO,
                "capabilities": {"experimentalApi": True},
            },
        }) + "\n")
        proc.stdin.flush()

        if _wait_for_id(0) is None:
            return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl, peer="cx"),)

        proc.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        proc.stdin.write(json.dumps({
            "id": 1, "method": _RATE_LIMITS_READ_METHOD, "params": None,
        }) + "\n")
        proc.stdin.flush()

        rate_limits_envelope = _wait_for_id(1)
        if rate_limits_envelope is None:
            return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl, peer="cx"),)

        # account/rateLimits/read nests primary/secondary one level under
        # "rateLimits" (plus a duplicate under "rateLimitsByLimitId"); this
        # code previously read them off the envelope directly, so it always
        # found neither key and silently returned ERROR (confirmed against
        # a live response: correct rate-limit data was present, just nested).
        credit_reading = _reset_credit_reading(ids, instance_id, profile_id, rate_limits_envelope, observed_at)
        rate_limits_raw = rate_limits_envelope.get("rateLimits")
        rate_limits_by_id_raw = rate_limits_envelope.get("rateLimitsByLimitId")

        limits_to_process: list[tuple[str, Any]]
        if isinstance(rate_limits_raw, dict):
            # Extract from primary/secondary
            rate_limits = cast(dict[str, Any], rate_limits_raw)
            limits_to_process = []
            for key in ("primary", "secondary"):
                q_limit_raw = rate_limits.get(key)
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
            return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl, peer="cx"), credit_reading)

        results: list[UsageObserved | ResetCreditObserved] = []
        legacy_windows = {
            "primary": ("X-5H", 5.0),
            "secondary": ("X-7D", 168.0),
        }
        for key, q_limit_raw in limits_to_process:
            q_limit = cast(dict[str, Any], q_limit_raw)
            label, window_hours = legacy_windows.get(key, ("X-UNK", 0.0))
            duration_mins: object | None = q_limit.get("windowDurationMins")
            if duration_mins is not None:
                try:
                    reported_hours = float(str(duration_mins)) / 60.0
                    if reported_hours > 0:
                        window_hours = reported_hours
                        if reported_hours <= 24:
                            label = f"X-{round(reported_hours)}H"
                        else:
                            label = f"X-{round(reported_hours / 24.0)}D"
                except (TypeError, ValueError, OverflowError):
                    pass

            used_val: object | None = q_limit.get("usedPercent")
            if used_val is None:
                continue  # absent usage is never manufactured as zero
            used = float(str(used_val))
            if not math.isfinite(used) or not 0 <= used <= 100:
                continue
            used_frac = used / 100.0

            resets_at: object | None = q_limit.get("resetsAt")
            if resets_at is None:
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

        if not results:
            return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl, peer="cx"), credit_reading)

        return (*results, credit_reading)
    except Exception:
        return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl, peer="cx"),)
    finally:
        if proc is not None:
            _cleanup_probe(proc)

def poll_agy_usage(
    ids: IdSource,
    instance_id: str,
    profile_id: str,
    clock: Optional[Callable[[], float]] = None,
    deadline_sec: float = 15.0,
    freshness_ttl: int = 60,
    log_path: Optional[str | Path] = None,
    sys_dir: Optional[Path] = None,
) -> Sequence[UsageObserved]:
    """Poll Agy's local ``/usage`` command, with explicitly configured statusline fallback.

    The slash command returns account quota without dispatching a model turn.
    Current Agy reports zero input/output/thinking tokens for this operation.
    An explicitly supplied ``log_path`` skips the executable probe.
    """
    
    clock_fn = clock if clock else (lambda: datetime.now(timezone.utc).timestamp())
    observed_at_now = int(clock_fn())
    
    _AG_QUOTA_LABELS = dict(AGY_QUOTA_FAMILIES)
    
    resolved_sys = resolve_sys_dir(sys_dir)
    quota_dict_typed: dict[str, Any] | None = None
    evidence_observed_at = observed_at_now
    source_tag = "agy_cli_usage"
    evidence_ref = EvidenceRef("probe:ag:usage")

    if log_path is None and "PEERHUB_AG_STATUSLINE_LOG" not in os.environ:
        agy_cmd = _real_command("ag", resolved_sys)
        if agy_cmd:
            agy_cwd = _resolve_workspace_root(resolved_sys)
            refusal = _agy_cwd_refusal(agy_cwd)
            if refusal is not None:  # nothing is spawned
                return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at_now, freshness_ttl, peer="ag",
                                     evidence_ref_override="agy_usage_cwd_refused",
                                     extra={"reason": "agy_usage_cwd_refused", "warning": refusal}),)
            try:
                completed = subprocess.run(
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
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=deadline_sec + 2.0,
                    check=False,
                )
                violation = _agy_usage_violation(completed.stdout)  # whatever the exit code: tokens may be spent either way
                if violation is not None:
                    ref, extra = violation
                    return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at_now, freshness_ttl, peer="ag",
                                         evidence_ref_override=ref, extra=extra),)
                if completed.returncode == 0:
                    parsed = _parse_agy_usage_output(completed.stdout)
                    if parsed:
                        quota_dict_typed = parsed
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
    if quota_dict_typed is None:
        configured_log = log_path if log_path is not None else os.environ.get("PEERHUB_AG_STATUSLINE_LOG")
        if configured_log is None and sys_dir is not None:
            configured_log = resolved_sys / "data" / "temp" / "ag_statusline_stdin.log"
        if configured_log is None:
            return (_fail_closed(ids, instance_id, profile_id, EvidenceState.UNKNOWN, observed_at_now, freshness_ttl, peer="ag"),)
        path = Path(configured_log)
        try:
            st = path.stat()
            mtime = int(st.st_mtime)
        except OSError:
            return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ABSENT, observed_at_now, freshness_ttl, peer="ag"),)

        if observed_at_now - mtime > freshness_ttl:
            return (_fail_closed(ids, instance_id, profile_id, EvidenceState.STALE, mtime, freshness_ttl, peer="ag"),)

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, mtime, freshness_ttl, peer="ag"),)

        quota_dict = data.get("quota")
        if not isinstance(quota_dict, dict):
            return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, mtime, freshness_ttl, peer="ag"),)
        quota_dict_typed = cast(dict[str, Any], quota_dict)
        evidence_observed_at = mtime
        source_tag = "agy_statusline"
        evidence_ref = EvidenceRef("probe:ag:statusline")
        
    results: list[UsageObserved] = []
    for key, label in _AG_QUOTA_LABELS.items():
        q_raw = quota_dict_typed.get(key)
        if not isinstance(q_raw, dict):
            continue
        q = cast(dict[str, Any], q_raw)
            
        rem = q.get("remaining_fraction")
        if not _valid_fraction(rem):
            return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, evidence_observed_at, freshness_ttl,
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
        
        results.append(
            UsageObserved(
                observation_id=ids.new_id("usage-observation"),
                instance_id=instance_id,
                profile_id=profile_id,
                evidence=evidence,
            )
        )
        
    if not results:
        return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, evidence_observed_at, freshness_ttl, peer="ag"),)
        
    return tuple(results)
