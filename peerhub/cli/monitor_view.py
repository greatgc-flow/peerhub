"""Pure human presentation for the schema 2.0 grouped monitor frame."""
from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, cast

from peerhub.cli.monitor_preferences import GROUPS

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _obj(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, Mapping) else {}


def _items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    items = cast(Sequence[Any], value)
    return [cast(dict[str, Any], item) for item in items if isinstance(item, Mapping)]


def _clean(value: object) -> str:
    return _CONTROL.sub("", str(value)).replace("\n", " ").strip()


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
               for char in _ANSI.sub("", text))


def _fit(text: str, width: int, unicode: bool) -> str:
    if width <= 0:
        return ""
    text = _clean(text)
    if _width(text) <= width:
        return text
    mark = "…" if unicode else "~"
    if width == 1:
        return mark
    result = ""
    for char in text:
        if _width(result + char + mark) > width:
            break
        result += char
    return result + mark


def _bar(used: float, cells: int, unicode: bool) -> str:
    used = min(1.0, max(0.0, used))
    filled = int(used * cells + 0.5)
    full, empty = ("█", "░") if unicode else ("#", ".")
    return full * filled + empty * (cells - filled)


def _number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _count(value: object) -> str:
    number = _number(value)
    return f"{int(number):,}" if number is not None and number >= 0 else "?"


def _size(value: object) -> str:
    number = _number(value)
    if number is None or number < 0:
        return "unknown size"
    units = ("B", "KiB", "MiB", "GiB")
    amount = number
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return "unknown size"


def _coverage(data: dict[str, Any]) -> str:
    coverage = _obj(data.get("coverage"))
    if coverage.get("truncated") or data.get("truncated"):
        return "partial · sample truncated"
    return ""


def _peer(item: dict[str, Any]) -> str:
    return _fit(_clean(item.get("peer_id") or "?"), 20, False)


def _group_lines(group_id: str, group: dict[str, Any], *, width: int, unicode: bool,
                 elapsed_seconds: float = 0.0) -> list[str]:
    data = _obj(group.get("data"))
    if data == {} and group.get("data") is None:
        return ["data unavailable"]
    rows: list[str] = []

    if group_id == "overview":
        peers = _items(data.get("peers"))
        if peers:
            names = [f"{_peer(item)} ({_clean(item.get('display_name') or item.get('adapter_ref') or 'peer')})"
                     for item in peers]
            rows.append(f"Peers: {', '.join(names)}")
        else:
            rows.append(f"Peers: {_count(data.get('sample_size'))} registered")
        if data.get("truncated") or data.get("session_sample_truncated") or data.get("peer_sample_truncated"):
            rows.append("Coverage: partial · sample truncated")

    elif group_id == "execution":
        scopes = _items(data.get("scopes"))
        totals = {key: 0 for key in ("not_started", "may_have_started", "started", "terminal")}
        latency_ns = latency_count = 0
        for scope in scopes:
            for key in totals:
                totals[key] += int(_number(_obj(scope.get("certainty")).get(key)) or 0)
            latency = _obj(scope.get("latency_30d"))
            latency_ns += int(_number(latency.get("sum_ns")) or 0)
            latency_count += int(_number(latency.get("count")) or 0)
        if scopes:
            rows.append("Delivery certainty: " + ", ".join(f"{key.replace('_', ' ')} {_count(value)}"
                         for key, value in totals.items()))
            avg = latency_ns / latency_count / 1_000_000_000 if latency_count else None
            rows.append(f"Measured delivery latency: {avg:.2f}s avg · {latency_count} samples" if avg is not None
                        else "Measured delivery latency: unknown")
        else:
            rows.append("No delivery evidence")
        note = _coverage(data)
        if note:
            rows.append(f"Coverage: {note}")

    elif group_id == "sessions":
        sessions = _items(data.get("items"))
        if sessions:
            for session in sessions[:2]:
                binding = _obj(session.get("configured_binding"))
                profile = _clean(binding.get("profile") or "profile unknown")
                model = _clean(binding.get("model") or "model unknown")
                effort = _clean(binding.get("effort") or "effort unknown")
                resume = "resumable" if session.get("resumable") else "not resumable"
                rows.append(f"{_peer(session)} {_clean(session.get('state') or 'UNKNOWN')} · gen {_count(session.get('generation'))} · {profile} / {model} ({effort}) · {resume}")
            if len(sessions) > 2:
                rows.append(f"+{len(sessions) - 2} sessions not shown")
        else:
            rows.append(f"Retained sessions: {_count(data.get('sample_size', 0))}")
        note = _coverage(data)
        if note:
            rows.append(f"Coverage: {note}")

    elif group_id == "usage":
        sessions = _items(data.get("sessions"))
        if sessions:
            for session in sessions[:2]:
                telemetry = _obj(session.get("telemetry"))
                normalized = _obj(telemetry.get("normalized"))
                reported = _obj(telemetry.get("reported"))
                tokens = normalized.get("provider_total_tokens", reported.get("total_tokens"))
                rows.append(f"{_peer(session)} provider-reported tokens: {_count(tokens)}")
            if len(sessions) > 2:
                rows.append(f"+{len(sessions) - 2} sessions not shown")
        else:
            rows.append("Provider-reported usage: unknown")
        coverage = _items(data.get("coverage_by_scope"))
        complete = sum(1 for item in coverage if item.get("complete") is True)
        if coverage:
            rows.append(f"Telemetry coverage: {complete}/{len(coverage)} scopes complete")

    elif group_id == "context":
        sessions = _items(data.get("sessions"))
        if sessions:
            for session in sessions[:2]:
                context = _obj(session.get("context"))
                percent = _number(context.get("percent"))
                state = _clean(context.get("state") or "UNKNOWN").upper()
                if percent is not None and 0 <= percent <= 100:
                    cells = max(3, min(12, width // 12))
                    rows.append(f"{_peer(session)} {percent:.0f}% {_bar(percent / 100, cells, unicode)} · {state}")
                else:
                    rows.append(f"{_peer(session)} UNKNOWN · {_clean(context.get('reason') or 'context occupancy unreported')}")
            if len(sessions) > 2:
                rows.append(f"+{len(sessions) - 2} sessions not shown")
        else:
            rows.append("Runtime context occupancy: unknown")

    elif group_id == "capacity":
        observations = _items(data.get("observations"))
        if observations:
            for item in observations[:2]:
                measurements = _obj(item.get("measurements"))
                remaining = _number(measurements.get("remaining_fraction"))
                pool = _clean(item.get("pool_ref") or item.get("pool_kind") or "quota pool")
                state = _clean(item.get("state") or "UNKNOWN").upper()
                projection = _obj(item.get("projection"))
                pace = _number(projection.get("pace_pt"))
                age = _number(item.get("age_seconds"))
                if remaining is not None and 0 <= remaining <= 1:
                    cells = max(3, min(12, width // 12))
                    detail = f"{remaining * 100:.0f}% remaining {_bar(1 - remaining, cells, unicode)}"
                else:
                    detail = "remaining unknown"
                pace_text = f" · pace {pace:+.1f}pp" if pace is not None else ""
                projection_state = _clean(projection.get("state") or "unknown").upper()
                exhaustion = _number(projection.get("exhaustion_at"))
                eta_text = ""
                if exhaustion is not None:
                    try:
                        eta_text = " · ETA " + datetime.fromtimestamp(exhaustion, timezone.utc).strftime("%m-%d %H:%MZ")
                    except (OverflowError, OSError, ValueError):
                        eta_text = " · ETA unknown"
                age_text = f" · age {max(0.0, age + elapsed_seconds):.0f}s" if age is not None else ""
                projection_text = f" · {projection_state}{eta_text}"
                rows.append(f"{_peer(item)} {pool}: {detail} · {state}{projection_text}{pace_text}{age_text}")
            if len(observations) > 2:
                rows.append(f"+{len(observations) - 2} quota observations not shown")
        else:
            rows.append("Quota capacity: no observations")
        note = _coverage(data)
        if note:
            rows.append(f"Coverage: {note}")

    elif group_id == "runtime":
        sessions = _items(data.get("sessions"))
        if sessions:
            for session in sessions[:2]:
                binding = _obj(session.get("configured_binding"))
                current = _obj(session.get("current_profile"))
                rows.append(f"{_peer(session)} profile {_clean(binding.get('profile') or 'unknown')} · current {_clean(current.get('source') or 'unknown')} / {_clean(current.get('status') or 'UNKNOWN')}")
            if len(sessions) > 2:
                rows.append(f"+{len(sessions) - 2} runtime sessions not shown")
        else:
            rows.append("Runtime profile provenance: no sessions")

    elif group_id == "coverage":
        scopes = _items(data.get("scopes"))
        complete = sum(1 for scope in scopes if scope.get("delivery_complete") is True
                       and _obj(scope.get("usage_coverage")).get("complete") is True)
        rows.append(f"Scopes complete: {complete}/{len(scopes)}" if scopes else "No scope evidence")
        if data.get("truncated"):
            rows.append("Coverage: partial · sample truncated")

    elif group_id == "store":
        rows.append(f"Schema {_count(data.get('schema_version'))} · database {_size(data.get('database_size_bytes'))}")

    else:
        # Keep future fields visible without exposing a JSON-shaped dump.
        rows.extend(_fallback_rows(data))

    return rows or ["No data"]


def _fallback_rows(data: dict[str, Any], prefix: str = "", depth: int = 0) -> list[str]:
    if depth >= 2:
        return []
    rows: list[str] = []
    for key, value in list(data.items())[:4]:
        label = f"{prefix}{_clean(key).replace('_', ' ')}"
        if isinstance(value, Mapping):
            rows.extend(_fallback_rows(_obj(cast(Mapping[str, Any], value)), f"{label} · ", depth + 1))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            rows.append(f"{label}: {_count(len(cast(Sequence[Any], value)))} items")
        elif value is None:
            rows.append(f"{label}: unknown")
        else:
            rows.append(f"{label}: {_clean(value)}")
    return rows or ["No data"]


def render_grouped_frame(payload: dict[str, Any], *, view: str = "rich", color: bool = False,
                         unicode: bool = True, width: int = 100) -> str:
    """Render one schema-2 frame without querying providers or serializing raw JSON."""
    width = max(20, width)
    selected = payload.get("selected_groups")
    group_ids = [str(group_id) for group_id in cast(Sequence[Any], selected)] if isinstance(selected, Sequence) and not isinstance(selected, str) else []
    groups = _obj(payload.get("groups"))
    if not group_ids:
        group_ids = [str(group_id) for group_id in groups.keys()]
    status_label = _clean(payload.get("status") or "UNKNOWN")
    title = f"PeerHub Monitor | {status_label}"
    read_at = _number(payload.get("read_at"))
    next_refresh = _number(payload.get("next_refresh_in"))
    elapsed_seconds = _number(payload.get("display_elapsed_seconds")) or 0.0
    if width < 48:
        title = f"Monitor {status_label}"
    elif read_at is not None:
        try:
            title += " | " + datetime.fromtimestamp(read_at, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        except (OverflowError, OSError, ValueError):
            title += " | time unknown"
    if next_refresh is not None:
        title += f" | refresh in {max(0, math.ceil(next_refresh))}s"
    lines = [_fit(title, width, unicode)]
    descriptions = {str(item["id"]): str(item["description"]) for item in GROUPS}
    for group_id in group_ids:
        group = _obj(groups.get(group_id))
        status = _clean(group.get("status") or "UNKNOWN").upper()
        label = f"{group_id.upper()} [{status}]"
        if group_id not in descriptions:
            label += " [unrecognized group]"
        if view == "rich" and unicode and width >= 32:
            border = "─" * max(0, width - _width(label) - 5)
            heading = _fit(f"╭─ {label} {border}╮", width, unicode)
            if color:
                heading = f"\x1b[1;36m{heading}\x1b[0m"
            lines.append(heading)
        else:
            lines.append(_fit(label, width, unicode))
        content_width = width - 4 if view == "rich" and unicode and width >= 32 else width - 2
        group_data = _group_lines(group_id, group, width=content_width, unicode=unicode,
                                  elapsed_seconds=elapsed_seconds)
        coverage = _obj(group.get("coverage"))
        if coverage.get("read") == "partial" and not any("partial" in line.lower() or "truncated" in line.lower()
                                                            for line in group_data):
            group_data.append(f"Coverage: partial · {_clean(coverage.get('reason') or 'incomplete evidence')}")
        for content in group_data:
            fitted = _fit(content, content_width, unicode)
            if view == "rich" and unicode and width >= 32:
                lines.append("│ " + fitted.ljust(content_width) + " │")
            else:
                lines.append("  " + fitted)
        if view == "rich" and unicode and width >= 32:
            lines.append("╰" + "─" * (width - 2) + "╯")
    # Force ASCII on request even if data contains provider-controlled Unicode.
    if not unicode:
        lines = [line.encode("ascii", errors="replace").decode("ascii") for line in lines]
    return "\n".join(lines) + "\n"
