import os
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Sequence, Optional, TypedDict, Callable

from peerhub.core.context import IdSource
from peerhub.core.evidence import EvidenceValue, EvidenceState, EvidenceRef
from peerhub.telemetry.contract import UsageObserved, UsageMeasurement

PORTABLE_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SYS_DIR = PORTABLE_ROOT / "_sys"
CLI_DIR = SYS_DIR / "cli"

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

def _claude_usage_emit(section: str, used_pct: str, reset_text: str, now: Optional[datetime] = None) -> Optional[ClaudeQuotaDict]:
    key = str(section or "").strip().lower()
    spec = _CLAUDE_USAGE_SECTIONS.get(key)
    if not spec:
        return None
    reset_at = _parse_claude_usage_reset(reset_text, now=now)
    if reset_at is None:
        return None
    try:
        used_frac = max(0.0, min(1.0, float(used_pct) / 100.0))
    except (TypeError, ValueError):
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
        r"\s*([0-9]+(?:\.[0-9]+)?)%\s+used\b.*?\bresets\s+(.+)$",
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
            if row:
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
            m_pct = re.search(r"([0-9]+(?:\.[0-9]+)?)%\s+used\b", line, re.IGNORECASE)
            if m_pct:
                current_pct = m_pct.group(1)
            continue
        if current_section and current_pct is not None:
            m_reset = re.search(r"\bresets?\s+(.+)$", line, re.IGNORECASE)
            if m_reset:
                row = _claude_usage_emit(current_section, current_pct, m_reset.group(1), now=now)
                if row:
                    rows.append(row)
                current_section = None
                current_pct = None
    return rows

def _real_binary(peer: str) -> Optional[str]:
    cand = SYS_DIR / "env" / "nodejs" / "npm-global" / "claude.cmd"
    if not cand.exists():
        return None
    resolved = cand.resolve()
    if resolved == CLI_DIR.resolve() or CLI_DIR.resolve() in resolved.parents:
        raise RuntimeError(f"refusing wrapper binary for {peer}: {resolved}")
    return str(resolved)

def _fail_closed(
    ids: IdSource,
    instance_id: str,
    profile_id: str,
    state: EvidenceState,
    observed_at: int,
    freshness_ttl: int,
) -> UsageObserved:
    evidence = EvidenceValue[UsageMeasurement](
        state=state,
        source_tag="claude_cli_usage",
        provider_id="peerhub.telemetry.cc",
        provider_version="1.0",
        observed_at=observed_at,
        captured_at=observed_at,
        freshness_ttl=freshness_ttl,
        evidence_ref=EvidenceRef("probe:cc:usage"),
        value=None,
    )
    return UsageObserved(
        observation_id=ids.new_id("usage-observation"),
        instance_id=instance_id,
        profile_id=profile_id,
        evidence=evidence,
    )

def poll_claude_usage(
    ids: IdSource,
    instance_id: str,
    profile_id: str,
    clock: Optional[Callable[[], float]] = None,
    deadline_sec: float = 15.0,
    freshness_ttl: int = 60,
) -> Sequence[UsageObserved]:
    """Poll claude.cmd /usage and return observations for each quota pool."""
    clock_fn = clock if clock else (lambda: datetime.now(timezone.utc).timestamp())
    observed_at = int(clock_fn())
    
    claude_exe = _real_binary("cc")
    if not claude_exe:
        return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ABSENT, observed_at, freshness_ttl),)

    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str((SYS_DIR / "claude" / "config").resolve())
    
    try:
        proc = subprocess.run(
            [claude_exe, "/usage"],
            cwd=str(PORTABLE_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=deadline_sec,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl),)
    except Exception:
        return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl),)
        
    text = "\n".join(str(part) for part in (proc.stdout, proc.stderr) if part)
    dt_now = datetime.fromtimestamp(observed_at, tz=timezone.utc).astimezone()
    quotas = _parse_claude_usage(text, now=dt_now)
    
    if not quotas:
        return (_fail_closed(ids, instance_id, profile_id, EvidenceState.ERROR, observed_at, freshness_ttl),)
        
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
