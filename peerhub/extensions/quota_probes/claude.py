"""Claude usage and account probes."""
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, TypedDict

from peerhub.extensions.observation_model import EvidenceState, account_text, valid_epoch_seconds
from peerhub.extensions.quota_types import (
    EvidenceRef,
    EvidenceValue,
    IdSource,
    UsageMeasurement,
    UsageObserved,
)

from . import common
from .common import AccountObserved, ProbeCancellation, TimezoneDatabaseMissingError, UnsupportedTimezoneError


# P4: pools each peer's profiles declare; a complete, successfully parsed report that omits one records it ABSENT.
# Callers may inject the profile-derived list (configured_pools); the probes import no profile code.
_CC_CONFIGURED_POOLS = ("C-5H", "C-7D", "F-7D")


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
    tz = common.resolve_explicit_tz(tz_name, now.tzinfo)

    m_date = re.match(
        r"^(?:(?P<mon>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s*)?"
        r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[ap]m)$",
        value,
        re.IGNORECASE,
    )
    if not m_date:
        return None
    mon_text = m_date.group("mon")
    now_in_tz = now.astimezone(tz)
    if mon_text:
        month = _MONTHS.get(mon_text.lower())
        day = int(m_date.group("day"))
        year = now_in_tz.year
    else:
        year = now_in_tz.year
        month = now_in_tz.month
        day = now_in_tz.day
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
        dt = datetime(year, month, day, hour, minute, tzinfo=tz)
    except ValueError:
        return None
    if mon_text and dt < now_in_tz - timedelta(days=30):
        try:
            dt = datetime(year + 1, month, day, hour, minute, tzinfo=tz)
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


def _claude_section_labels(text: str) -> set[str]:
    """Pool labels of every recognised /usage section mentioned in the output, parsed or not."""
    inline = re.compile(r"^(Current session|Current week \(all models\)|Current week \(Fable\)):", re.IGNORECASE)
    labels: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        m = inline.match(line)
        spec = _CLAUDE_USAGE_SECTIONS.get((m.group(1) if m else line.rstrip(":")).strip().lower())
        if spec is not None:
            labels.add(spec[0])
    return labels

def _claude_usage_has_drift(text: str) -> bool:
    """True when /usage shows a usage line or section this parser does not know (renamed/new window): a pool that looks
    missing may merely be renamed, so no ABSENT may be inferred. Ordinary UI chrome is not drift."""
    labelled_usage = re.compile(r"^[^:]{1,60}:\s*[^\s%]+%\s+used\b", re.IGNORECASE)
    current: bool = False  # inside a recognised section header, awaiting its "N% used" / "Resets" lines
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        label = line.split(":", 1)[0].strip().lower() if ":" in line else line.rstrip(":").strip().lower()
        if label in _CLAUDE_USAGE_SECTIONS:
            current = ":" not in line or not re.search(r"%\s+used\b", line, re.IGNORECASE)
            continue
        if re.match(r"current\b", line, re.IGNORECASE) or labelled_usage.match(line):
            return True
        if re.search(r"%\s+used\b", line, re.IGNORECASE) and not current:
            return True  # a usage figure that belongs to no recognised section
        if current and re.search(r"\bresets?\b", line, re.IGNORECASE):
            current = False
    return False


def poll_claude_usage(
    ids: IdSource,
    instance_id: str,
    profile_id: str,
    clock: Optional[Callable[[], float]] = None,
    deadline_sec: float = 15.0,
    freshness_ttl: int = 60,
    sys_dir: Optional[Path] = None,
    configured_pools: Sequence[str] | None = None,
    cancellation: ProbeCancellation | None = None,
) -> Sequence[UsageObserved]:
    """Poll claude.cmd /usage and return observations for each quota pool.

    A declared pool whose section is absent from a fully parsed report is recorded ABSENT (never zero/unlimited);
    any malformed or unrecognised-but-present section means no ABSENT rows at all."""
    resolved_sys = common.resolve_sys_dir(sys_dir)
    workspace_root = common.resolve_workspace_root(resolved_sys)
    clock_fn = clock if clock else (lambda: datetime.now(timezone.utc).timestamp())
    observed_at = int(clock_fn())

    claude_cmd = common.real_command("cc", resolved_sys)
    if not claude_cmd:
        return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ABSENT, observed_at, freshness_ttl),)

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
        proc = common.spawn_probe(
            cancellation,
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
            run_ok = getattr(proc, "returncode", None) == 0  # a failed/unfinished run may print partial usage
        except subprocess.TimeoutExpired:
            return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl,
                                       extra={"reason": "claude_usage_timeout"}),)
    except Exception as exc:
        return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl,
                                   extra={"reason": f"claude_usage_spawn_failed:{type(exc).__name__}"}),)
    finally:
        if proc is not None:
            common.finish_probe(proc, cancellation)

    text = "\n".join(str(part) for part in (stdout, stderr) if part)
    dt_now = datetime.fromtimestamp(observed_at, tz=timezone.utc).astimezone()
    try:
        quotas = _parse_claude_usage(text, now=dt_now)
    except TimezoneDatabaseMissingError:
        return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.UNAVAILABLE, observed_at, freshness_ttl,
                                   extra={"reason": "timezone_database_missing"}),)
    except UnsupportedTimezoneError as exc:
        return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.UNAVAILABLE, observed_at, freshness_ttl,
                                   extra={"reason": f"unsupported_timezone:{exc.tz_name}"}),)

    if not quotas:
        return (common.fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl,
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

    reported = {q["label"] for q in quotas}
    # exit 0, no unrecognised usage section (drift), and every recognised section that appeared parsed into a row
    if run_ok and not _claude_usage_has_drift(text) and _claude_section_labels(text) <= reported:
        for pool_scope in (_CC_CONFIGURED_POOLS if configured_pools is None else configured_pools):
            if pool_scope not in reported:
                results.append(common.absent_pool(ids, instance_id, profile_id, "cc", observed_at, freshness_ttl, pool_scope))

    return tuple(results)


def _login_expiry(value: object) -> Optional[int]:
    """Epoch seconds of the login-token expiry, or None unless it is a representable UTC instant (1970..9999)."""
    seconds = _login_expiry_seconds(value)
    return seconds if seconds is not None and valid_epoch_seconds(seconds) else None


def _login_expiry_seconds(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):  # bound the int before any float conversion: 10**309 does not fit a float
        if not 0 < value <= 10**18:
            return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value <= 0:
            return None
        scaled = value / 1000.0 if value > 1e12 else value  # epoch milliseconds
        return int(scaled) if scaled <= 1e18 else None
    if isinstance(value, str):
        text = value.strip()
        try:
            return _login_expiry_seconds(float(text))
        except ValueError:
            pass
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return int((stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp())
        except (ValueError, OverflowError, OSError):
            return None
    return None


def parse_claude_auth_status(text: str) -> Optional[dict[str, Any]]:
    """`claude auth status` JSON -> normalized payload, or None when it is not a JSON object."""
    env = common.json_object_from_output(text)
    if env is None:
        return None
    flag = env.get("loggedIn")
    logged_in: object = flag if isinstance(flag, bool) else "UNKNOWN"
    if logged_in is False:
        return common.account_payload(common.CLAUDE_ACCOUNT_REF, logged_in=False, plan=None, plan_status="n/a", expiry_status="n/a",
                                      auth_method=account_text(env.get("authMethod")))
    method, provider = account_text(env.get("authMethod")), account_text(env.get("apiProvider"))
    # API-key / Bedrock / Vertex style logins have no subscription plan or login-token expiry to expose: n/a, not "could not determine".
    subscription = (method is None or method.lower() == "claude.ai") and (provider is None or provider.lower() == "firstparty")
    plan = (account_text(env.get("subscriptionType")) or account_text(env.get("planTier"))) if subscription else None  # residue is ignored
    expires = _login_expiry(env.get("loginExpiresAt")) if subscription else None  # a missing expiry never means logged out
    return common.account_payload(common.CLAUDE_ACCOUNT_REF, logged_in=logged_in, plan=plan,
                                  plan_status="reported" if plan else "UNKNOWN" if subscription else "n/a",
                                  expires=expires, expiry_status="reported" if expires else "UNKNOWN" if subscription else "n/a",
                                  auth_method=method)


def poll_claude_account(*, clock: Optional[Callable[[], float]] = None, deadline_sec: float = 10.0,
                        sys_dir: Optional[Path] = None, cancellation: ProbeCancellation | None = None) -> AccountObserved:
    """`claude auth status` for the default account, with the quota probe's CLAUDE_CONFIG_DIR handling. No model turn."""
    ref, source = common.CLAUDE_ACCOUNT_REF, "claude_auth_status"
    observed_at = int((clock if clock else (lambda: datetime.now(timezone.utc).timestamp()))())
    resolved_sys = common.resolve_sys_dir(sys_dir)
    command = common.real_command("cc", resolved_sys)
    if not command:
        return common.account_failure(ref, source, observed_at, EvidenceState.UNAVAILABLE, "cli_not_found")
    env = os.environ.copy()
    root = os.environ.get("PEERHUB_CLAUDE_CONFIG_DIR") or env.get("CLAUDE_CONFIG_DIR")
    if root:
        env["CLAUDE_CONFIG_DIR"] = root
    proc = None
    try:
        proc = common.spawn_probe(cancellation, [*command, "auth", "status"], cwd=str(common.resolve_workspace_root(resolved_sys)), env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                errors="replace", start_new_session=sys.platform != "win32")
        try:
            stdout, _stderr = proc.communicate(timeout=deadline_sec)  # a logged-out CLI exits 1 but still prints the JSON
        except subprocess.TimeoutExpired:
            return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "timeout")
    except Exception:
        return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "spawn_failed")
    finally:
        if proc is not None:
            common.finish_probe(proc, cancellation)
    payload = parse_claude_auth_status(str(stdout or ""))
    if payload is None:
        return common.account_failure(ref, source, observed_at, EvidenceState.ERROR, "unparseable")
    return AccountObserved(ref, EvidenceState.MEASURED, source, observed_at, payload)
