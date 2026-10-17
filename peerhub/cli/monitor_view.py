"""Pure human presentation for the schema 2.0 grouped monitor frame."""
from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any, cast

from peerhub.cli.monitor_preferences import GROUPS

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_BIDI = {"\u061c", "\u200e", "\u200f", *[chr(code) for code in range(0x202A, 0x202F)],
         *[chr(code) for code in range(0x2066, 0x206A)]}


def _obj(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, Mapping) else {}


def _items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    items = cast(Sequence[Any], value)
    return [cast(dict[str, Any], item) for item in items if isinstance(item, Mapping)]


def _clean(value: object) -> str:
    text = str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    text = _CONTROL.sub("�", text)
    return "".join("�" if char in _BIDI or unicodedata.category(char) in {"Cf", "Cn", "Cs", "Zl", "Zp"}
                   and char != "\u200d" else char
                   for char in text).strip()


def _width(text: str) -> int:
    return sum(0 if unicodedata.combining(char) or char == "\u200d"
               or "\ufe00" <= char <= "\ufe0f" or "\U000e0100" <= char <= "\U000e01ef"
               else 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
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


def _coverage(data: dict[str, Any], unicode: bool = True) -> str:
    coverage = _obj(data.get("coverage"))
    if coverage.get("truncated") or data.get("truncated"):
        return f"partial {'·' if unicode else '|'} sample truncated"
    return ""


def _peer(item: dict[str, Any]) -> str:
    return _fit(_clean(item.get("peer_id") or "?"), 20, False)


def _group_lines(group_id: str, group: dict[str, Any], *, width: int, unicode: bool,
                 elapsed_seconds: float = 0.0, read_at: float | None = None) -> list[str]:
    data = _obj(group.get("data"))
    if data == {} and group.get("data") is None:
        return ["data unavailable"]
    rows: list[str] = []
    sep = " · " if unicode else " | "
    dot = "·" if unicode else "|"

    if group_id == "overview":
        peers = _items(data.get("peers"))
        if peers:
            names = [f"{_peer(item)} ({_clean(item.get('display_name') or item.get('adapter_ref') or 'peer')})"
                     for item in peers]
            rows.append(f"Peers: {', '.join(names)}")
        else:
            rows.append(f"Peers: {_count(data.get('sample_size'))} registered")
        if data.get("truncated") or data.get("session_sample_truncated") or data.get("peer_sample_truncated"):
            rows.append(f"Coverage: partial {dot} sample truncated")

    elif group_id == "peers":
        peer_items = _items(data.get("peers"))
        if not peer_items:
            rows.append("No peer evidence")
        for item in peer_items:
            pid = _clean(item.get("peer_id") or "?")
            q_obj = _obj(item.get("quota")) if isinstance(item.get("quota"), Mapping) else None
            p_obj = _obj(item.get("profile")) if isinstance(item.get("profile"), Mapping) else None
            s_obj = _obj(item.get("session")) if isinstance(item.get("session"), Mapping) else None
            c_obj = _obj(item.get("context")) if isinstance(item.get("context"), Mapping) else None

            if q_obj is None:
                q_wide = "unknown"
                q_narr = "unk"
            else:
                q_state = _clean(q_obj.get("state") or "").upper()
                q_pool = _clean(q_obj.get("pool_ref") or "")
                if ":" in q_pool:
                    q_pool = q_pool.split(":")[-1]
                q_pct = _number(q_obj.get("percent"))
                if q_pct is None and _number(q_obj.get("remaining_fraction")) is not None:
                    q_pct = float(cast(float, _number(q_obj.get("remaining_fraction")))) * 100
                q_age = _number(q_obj.get("age_seconds"))

                q_scope = _clean(q_obj.get("scope") or "")

                if q_state == "INCOMPLETE" or q_pool == "incomplete":
                    q_wide = "incomplete"
                    q_narr = "incomplete"
                elif q_state == "STALE":
                    q_wide = "STALE"
                    q_narr = "STALE"
                elif q_state != "MEASURED" or q_pct is None:
                    q_wide = "unknown"
                    q_narr = "unk"
                else:
                    age_val = max(0.0, (q_age or 0.0) + elapsed_seconds)
                    age_str = f" ({int(age_val // 60)}m ago)" if q_age is not None and age_val >= 60 else ""
                    if q_scope in ("unmapped", "account-wide"):
                        pool_label = f"{q_pool} unmapped" if q_pool else "unmapped"
                    else:
                        pool_label = q_pool
                    pool_str = f" ({pool_label})" if pool_label else ""
                    q_wide = f"{q_pct:.0f}%{pool_str}{age_str}"
                    q_narr = f"{q_pct:.0f}%{pool_str}"

            if p_obj is None:
                p_wide = "unknown [unknown]"
                p_narr = "unk [unk]"
            else:
                p_name = _clean(p_obj.get("name") or "")
                if not p_name:
                    prof = _clean(p_obj.get("profile") or "unknown")
                    mod = _clean(p_obj.get("model") or "")
                    p_name = f"{prof}/{mod}" if mod else prof
                layer = _clean(p_obj.get("source") or p_obj.get("config_layer") or "unknown")
                layer_map = {"workspace": "ws", "global": "glob", "packaged": "pkg"}
                p_wide = f"{p_name} [{layer}]"
                p_narr = f"{p_name} [{layer_map.get(layer, layer[:4])}]"

            if s_obj is None and item.get("session") is None:
                if data.get("session_sample_truncated") or data.get("truncated"):
                    s_wide = "sample truncated"
                    s_narr = "sample truncated"
                else:
                    s_wide = "none"
                    s_narr = "none"
            elif s_obj is None:
                s_wide = "unknown"
                s_narr = "unk"
            else:
                mode = _clean(s_obj.get("execution_mode") or s_obj.get("mode") or "").lower()
                gen = s_obj.get("generation")
                if mode in ("reuse", "reused"):
                    mode_label = "reused"
                elif mode == "fresh":
                    mode_label = "fresh"
                else:
                    mode_label = "unknown"
                if mode_label == "unknown" and gen is None:
                    s_wide = "unknown"
                    s_narr = "unk"
                else:
                    gen_part = f"gen {_count(gen)}" if gen is not None else "gen unknown"
                    s_wide = f"{mode_label} {gen_part}"
                    s_narr = s_wide

            if c_obj is None:
                c_wide = "unknown"
                c_narr = "unk"
            else:
                c_state = _clean(c_obj.get("state") or "").upper()
                c_pct = _number(c_obj.get("percent"))
                c_tok = c_obj.get("tokens") or c_obj.get("occupancy_tokens")
                if isinstance(c_tok, (int, float)) and c_tok >= 1000:
                    tok_str = f"{int(c_tok) // 1000}k"
                elif c_tok is not None:
                    tok_str = _clean(c_tok)
                else:
                    tok_str = ""

                if c_state == "MEASURED" and c_pct is not None:
                    pct_str = f"({c_pct:.0f}%)" if tok_str else f"{c_pct:.0f}%"
                    c_wide = f"{tok_str} {pct_str}".strip()
                    c_narr = f"{tok_str} {pct_str}".strip()
                else:
                    c_wide = "unknown"
                    c_narr = "unk"

            if width < 48:
                rows.append(_fit(f"{pid}: quota {q_narr} | {p_narr} | {s_narr} | ctx: {c_narr}", max(0, width - 1), unicode))
            else:
                rows.append(f"{pid:<4}quota: {q_wide} -> {p_wide}")
                rows.append(f"    -> latest session: {s_wide} -> context: {c_wide}")

        defaults_rev: object = data.get("defaults_revision")
        observed_at: object = data.get("observed_at")

        rev_str = f"rev {_clean(defaults_rev)}" if defaults_rev else "rev unknown"
        if observed_at:
            obs_clean = _clean(observed_at)
            age_str = "(unknown)"
            if read_at is not None:
                try:
                    read_date = datetime.fromtimestamp(read_at, timezone.utc).date()
                    obs_date = date.fromisoformat(obs_clean)
                    days = (read_date - obs_date).days
                    age_str = "(future)" if days < 0 else f"({days}d ago)"
                except (ValueError, OSError, OverflowError):
                    pass
            obs_str = f"checked {obs_clean} {age_str}"
        else:
            obs_str = "checked unknown"

        footer = (
            f"Model facts: {rev_str}{sep}{obs_str}{sep}"
            "sources: codex debug models | agy models | claude: known ids"
        )
        rows.append(_fit(footer, max(0, width - 1), unicode))

        facts_rep: object = data.get("facts_report")
        if isinstance(facts_rep, dict):
            rep_dict = cast(dict[str, Any], facts_rep)
            status = _clean(rep_dict.get("status") or "")
            reasons_raw = rep_dict.get("reasons")
            reasons = [str(r) for r in cast(list[object], reasons_raw)] if isinstance(reasons_raw, list) else []
            if status == "needs_recheck":
                raw_unk: object = rep_dict.get("unknown_pools")
                unk_pools: list[str] = [str(p) for p in cast(list[object], raw_unk)] if isinstance(raw_unk, list) else []
                display_reasons: list[str] = []
                for r in reasons:
                    if r == "ag: unknown_pool" and unk_pools:
                        display_reasons.append(f"ag: unknown_pool: {', '.join(unk_pools)}")
                    else:
                        display_reasons.append(r)
                reason_str = f" ({', '.join(display_reasons)})" if display_reasons else ""
                facts_line = f"Facts: needs_recheck{reason_str} - refresh-model-profiles"
            elif status == "ok":
                chk_date = str(rep_dict.get("checked_at") or "")
                chk_date_str = f" (checked {chk_date[:10]})" if chk_date else ""
                facts_line = f"Facts: ok{chk_date_str}"
            elif status == "unverified":
                facts_line = "Facts: unverified"
            else:
                facts_line = f"Facts: {status}" if status else "Facts: unknown"
            rows.append(_fit(facts_line, max(0, width - 1), unicode))

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
            rows.append(f"Measured delivery latency: {avg:.2f}s avg{sep}{latency_count} samples" if avg is not None
                        else "Measured delivery latency: unknown")
        else:
            rows.append("No delivery evidence")
        note = _coverage(data, unicode)
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
                rows.append(f"{_peer(session)} {_clean(session.get('state') or 'UNKNOWN')}{sep}gen {_count(session.get('generation'))}{sep}{profile} / {model} ({effort}){sep}{resume}")
            if len(sessions) > 2:
                rows.append(f"+{len(sessions) - 2} sessions not shown")
        else:
            rows.append(f"Retained sessions: {_count(data.get('sample_size', 0))}")
        note = _coverage(data, unicode)
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
                    rows.append(f"{_peer(session)} {percent:.0f}% {_bar(percent / 100, cells, unicode)}{sep}{state}")
                else:
                    rows.append(f"{_peer(session)} UNKNOWN{sep}{_clean(context.get('reason') or 'context occupancy unreported')}")
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
                pace_text = f"{sep}pace {pace:+.1f}pp" if pace is not None else ""
                projection_state = _clean(projection.get("state") or "unknown").upper()
                exhaustion = _number(projection.get("exhaustion_at"))
                eta_text = ""
                if exhaustion is not None:
                    try:
                        eta_text = f"{sep}ETA " + datetime.fromtimestamp(exhaustion, timezone.utc).strftime("%m-%d %H:%MZ")
                    except (OverflowError, OSError, ValueError):
                        eta_text = f"{sep}ETA unknown"
                age_text = f"{sep}age {max(0.0, age + elapsed_seconds):.0f}s" if age is not None else ""
                projection_text = f"{sep}{projection_state}{eta_text}"
                rows.append(f"{_peer(item)} {pool}: {detail}{sep}{state}{projection_text}{pace_text}{age_text}")
            if len(observations) > 2:
                rows.append(f"+{len(observations) - 2} quota observations not shown")
        else:
            rows.append("Quota capacity: no observations")
        note = _coverage(data, unicode)
        if note:
            rows.append(f"Coverage: {note}")

    elif group_id == "runtime":
        sessions = _items(data.get("sessions"))
        if sessions:
            for session in sessions[:2]:
                binding = _obj(session.get("configured_binding"))
                current = _obj(session.get("current_profile"))
                rows.append(f"{_peer(session)} profile {_clean(binding.get('profile') or 'unknown')}{sep}current {_clean(current.get('source') or 'unknown')} / {_clean(current.get('status') or 'UNKNOWN')}")
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
            rows.append(f"Coverage: partial {dot} sample truncated")

    elif group_id == "store":
        rows.append(f"Schema {_count(data.get('schema_version'))}{sep}database {_size(data.get('database_size_bytes'))}")

    else:
        # Keep future fields visible without exposing a JSON-shaped dump.
        rows.extend(_fallback_rows(data))

    return rows or ["No data"]


def _safe_error_reason(value: object) -> str:
    """Expose a bounded exception class only; never print raw exception text."""
    raw = _clean(value)
    code = raw.split(":", 1)[0].strip().split(".")[-1]
    allowed = {"OperationalError", "DatabaseError", "DataError", "IntegrityError", "ProgrammingError",
               "InterfaceError", "SchemaVersionError", "PermissionError", "FileNotFoundError", "OSError"}
    return f"{code}: database read failed" if code in allowed else "database read failed"


def _safe_refresh_warning_labels(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    warnings = cast(Sequence[Any], value)
    labels: list[str] = []
    safe_reasons = {"probe_warning", "probe_timeout", "claude_usage_timeout", "gemini_usage_timeout",
                    "codex_usage_timeout", "provider_probe_error", "provider_probe_unavailable",
                    "auth_error", "quota_exhausted", "invalid_model", "usage_limit", "timeout",
                    "unavailable", "error"}
    for item in warnings[:3]:
        if not isinstance(item, str):
            labels.append("probe_warning")
            continue
        parts = [_clean(part).strip() for part in item.split(":")]
        peer = parts[0].lower() if parts and parts[0].lower() in {"cc", "cx", "ag"} else ""
        reason = parts[1] if peer and len(parts) > 1 else (parts[0] if parts else "")
        code = reason.lower() if reason.lower() in safe_reasons else "probe_warning"
        labels.append(f"{peer} {code}".strip())
    if len(warnings) > 3:
        labels.append(f"+{len(warnings) - 3} warnings")
    return labels


def _priority(group: dict[str, Any]) -> int:
    status = str(group.get("status") or "UNKNOWN").upper()
    if status in {"ERROR", "FAILED"}:
        return 0
    if status in {"UNAVAILABLE", "NOT_AVAILABLE"}:
        return 1
    if status in {"STALE", "PARTIAL", "UNKNOWN"}:
        return 2
    if _group_has_uncertain_measurement(_obj(group.get("data")), depth=0):
        return 2
    if status == "OK":
        return 3
    return 2


def _group_has_uncertain_measurement(value: object, *, depth: int) -> bool:
    if depth >= 4:
        return False
    if isinstance(value, Mapping):
        for key, child in list(cast(Mapping[str, Any], value).items())[:100]:
            if str(key).lower() in {"status", "state"} and isinstance(child, str):
                if child.upper() in {"ERROR", "FAILED", "UNAVAILABLE", "STALE", "PARTIAL", "UNKNOWN"}:
                    return True
            if _group_has_uncertain_measurement(child, depth=depth + 1):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_group_has_uncertain_measurement(item, depth=depth + 1)
                   for item in cast(Sequence[Any], value)[:100])
    return False


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


def _render_unbounded_frame(payload: dict[str, Any], *, view: str, color: bool,
                            unicode: bool, width: int) -> str:
    """Keep the append-only pipe/plain layout stable; it is not a TTY viewport."""
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
                                  elapsed_seconds=elapsed_seconds, read_at=read_at)
        coverage = _obj(group.get("coverage"))
        if coverage.get("read") == "partial" and not any("partial" in line.lower() or "truncated" in line.lower()
                                                            for line in group_data):
            group_data.append(f"Coverage: partial {'·' if unicode else '|'} {_clean(coverage.get('reason') or 'incomplete evidence')}")
        for content in group_data:
            fitted = _fit(content, content_width, unicode)
            if view == "rich" and unicode and width >= 32:
                lines.append("│ " + fitted.ljust(content_width) + " │")
            else:
                lines.append("  " + fitted)
        if view == "rich" and unicode and width >= 32:
            lines.append("╰" + "─" * (width - 2) + "╯")
    if not unicode:
        lines = [line.replace(" · ", " | ").replace("·", "|").encode("ascii", errors="replace").decode("ascii") for line in lines]
    return "\n".join(lines) + "\n"


def render_grouped_frame(payload: dict[str, Any], *, view: str = "rich", color: bool = False,
                         unicode: bool = True, width: int = 100, height: int | None = None) -> str:
    """Render a cached schema-2 frame; height enables bounded interactive layout."""
    if height is None:
        return _render_unbounded_frame(payload, view=view, color=color, unicode=unicode, width=width)
    width = max(1, width)
    height = max(1, height)
    selected = payload.get("selected_groups")
    group_ids = [str(group_id) for group_id in cast(Sequence[Any], selected)] if isinstance(selected, Sequence) and not isinstance(selected, str) else []
    groups = _obj(payload.get("groups"))
    if not group_ids:
        group_ids = [str(group_id) for group_id in groups.keys()]
    if height <= 1:
        return ""
    if height == 2:
        if width <= 1:
            return ""
        return _fit(f"+{len(group_ids)} --group all", width - 1, unicode) + "\n"
    status_label = _clean(payload.get("status") or "UNKNOWN")
    groups = _obj(payload.get("groups"))
    statuses = [str(_obj(groups.get(group_id)).get("status") or "UNKNOWN").upper() for group_id in group_ids]
    errors = sum(status in {"ERROR", "FAILED"} for status in statuses)
    partial = sum(status in {"PARTIAL", "STALE", "UNKNOWN", "UNAVAILABLE"} for status in statuses)
    refresh_state = _clean(payload.get("refresh_state") or "").upper()
    title = f"PeerHub Monitor | {status_label}"
    if errors or partial:
        title += f" | {errors} err {'·' if unicode else '|'} {partial} partial"
    if refresh_state:
        title += f" | {refresh_state}"
    read_at = _number(payload.get("read_at"))
    next_refresh = _number(payload.get("next_refresh_in"))
    elapsed_seconds = _number(payload.get("display_elapsed_seconds")) or 0.0
    if width < 48:
        title = f"Monitor {status_label}"
        if errors or partial:
            title += f" E{errors}/P{partial}"
        if refresh_state:
            title += f" {refresh_state}"
    if next_refresh is not None:
        title += f" | refresh in {max(0, math.ceil(next_refresh))}s"
    if width >= 120 and read_at is not None:
        try:
            title += " | " + datetime.fromtimestamp(read_at, timezone.utc).strftime("%H:%M:%SZ")
        except (OverflowError, OSError, ValueError):
            pass
    store = _obj(payload.get("store_selection"))
    db_path = _clean(store.get("path") or "")
    db_source = _clean(store.get("source") or "")
    show_db_path = width >= 64 and height > 6 and bool(db_path)
    if db_source and not show_db_path:
        if width <= 24:
            title = f"{status_label} DB:{db_source}"
        else:
            title = f"{title} DB:{db_source}" if width < 48 else f"{title}{' · ' if unicode else ' | '}DB:{db_source}"
    lines = [_fit(title, width - 1, unicode)]
    if show_db_path:
        lines.append(_fit(f"DB [{db_source}]: {db_path}", width - 1, unicode))
    descriptions = {str(item["id"]): str(item["description"]) for item in GROUPS}
    blocks: list[tuple[int, int, str, list[str]]] = []
    for order, group_id in enumerate(group_ids):
        group = _obj(groups.get(group_id))
        status = _clean(group.get("status") or "UNKNOWN").upper()
        label = f"{group_id.upper()} [{status}]"
        if group_id not in descriptions:
            label += " [unrecognized group]"
        if view == "rich" and unicode and width >= 48:
            border = "─" * max(0, width - _width(label) - 6)
            heading = _fit(f"╭─ {label} {border}╮", width - 1, unicode)
            if color:
                heading = f"\x1b[1;36m{heading}\x1b[0m"
        else:
            heading = _fit(label, width - 1, unicode)
        indent = "  " if width >= 4 else ""
        content_width = max(0, width - 1 - _width(indent))
        group_data = _group_lines(group_id, group, width=content_width, unicode=unicode,
                                  elapsed_seconds=elapsed_seconds, read_at=read_at)
        error_map = _obj(payload.get("errors"))
        error_value = error_map.get(group_id)
        if error_value is None:
            dependencies = {
                "overview": ("sessions", "execution"), "peers": ("sessions", "capacity"),
                "sessions": ("sessions", "execution"),
                "usage": ("sessions", "execution"), "context": ("sessions", "execution"),
                "runtime": ("sessions", "execution"), "execution": ("execution",),
                "coverage": ("execution",), "capacity": ("capacity",), "store": ("store",),
            }
            error_value = next((error_map[key] for key in dependencies.get(group_id, (group_id,))
                                if key in error_map), None)
        if status in {"ERROR", "FAILED"}:
            group_data.insert(0, _safe_error_reason(error_value))
        coverage = _obj(group.get("coverage"))
        if coverage.get("read") == "partial" and not any("partial" in line.lower() or "truncated" in line.lower()
                                                            for line in group_data):
            group_data.append(f"Coverage: partial {'·' if unicode else '|'} {_clean(coverage.get('reason') or 'incomplete evidence')}")
        summary = _fit(group_data.pop(0) if group_data else "No data", content_width, unicode)
        blocks.append((_priority(group), order, heading, [summary, *group_data]))
    # Severity controls which summaries survive a short viewport; catalog order is stable.
    ranked = sorted(blocks, key=lambda item: (item[0], item[1]))
    budget = max(0, height - 1 - len(lines))
    all_summaries_fit = len(ranked) * 2 <= budget
    visible_count = len(ranked) if all_summaries_fit else max(0, (budget - 1) // 2)
    visible = (blocks if all_summaries_fit else ranked[:visible_count])
    hidden = len(ranked) - len(visible)
    for _priority_value, _order, heading, rows in visible:
        lines.append(heading)
        indent = "  " if width >= 4 else ""
        lines.append(indent + rows[0])
    if hidden:
        sep = " · " if unicode else " | "
        if width >= 64:
            overflow = f"{hidden} groups omitted{sep}peerhub monitor --snapshot --group all"
        elif width >= 32:
            overflow = f"{hidden} groups omitted{sep}--group all"
        else:
            overflow = f"+{hidden} --group all"
        lines.append(_fit(overflow, width - 1, unicode))
    refresh_clean = str(payload.get("refresh_state") or "")
    if refresh_clean == "STOPPED · AGY TOKEN USE" and len(lines) < height - 1:
        lines.append(_fit("Next: --allow-agy-token-use to continue", width - 1, unicode))
    for warning in _safe_refresh_warning_labels(payload.get("refresh_warnings")):
        if len(lines) >= height - 1:
            break
        lines.append(_fit(f"Warning: {warning}", width - 1, unicode))
    # Extra rows reveal detail without changing selected summaries or their order.
    for _priority_value, _order, _heading, rows in visible:
        for detail in rows[1:]:
            if len(lines) >= height - 1:
                break
            detail_indent = "  " if width >= 4 else ""
            lines.append(_fit(f"{detail_indent}{detail}", width - 1, unicode))
        if len(lines) >= height - 1:
            break
    lines = lines[:height - 1]
    # Force ASCII on request even if data contains provider-controlled Unicode.
    if not unicode:
        lines = [line.replace(" · ", " | ").replace("·", "|").encode("ascii", errors="replace").decode("ascii") for line in lines]
    return "\n".join(lines) + "\n"
