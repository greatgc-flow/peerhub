"""Pure human presentation for the schema 2.0 grouped monitor frame."""
from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, cast

from peerhub._console import ANSI_ESCAPE, clean_text, finite_number
from peerhub.cli.monitor_format import (
    PoolEntry,
    fraction_bar,
    compact_pool,
    fold_pool_markers,
    display_min_quota,
    na_plan,
    na_pool_note,
    na_reason,
    ordered_pools,
    pct,
    pool_gap,
    pool_order,
    pool_table,
    quota_layout,
)
from peerhub.cli.monitor_layout import (
    PILL_SGR,
    RICH_GLYPHS,
    RICH_SGR,
    FILE_HYPERLINK,
    RULE_MIN_TAIL_WIDTH,
    RULE_PREFIX_WIDTH,
    ascii_text,
    cell_text,
    column_painter,
    table_columns,
    compact_age,
    wrap_text,
    format_count,
    fit_text,
    fit_bytes,
    fit_fields,
    fit_live_quota_title,
    fit_live_title,
    format_duration,
    heading_rule,
    hidden_lines,
    dict_items,
    as_dict,
    omitted_rows,
    overflow,
    pack_fields,
    paint_text,
    peer_label,
    section_rule,
    short_text,
    format_size,
    fit_table,
    utc_text,
    visible_fields,
    cell_width,
)
from peerhub.cli.monitor_legend import MONITOR_LEGEND, POOLS_HEADING, POOL_LEGEND, QUOTA_NA
from peerhub.cli.monitor_links import (
    GUIDE_TEXT,
    STATE_WORDS_ANCHOR,
    GUIDE_MAIN_URL,
    guide_released,
    link_profiles,
    location_lines,
    link_query,
    query_painter,
    file_link_url,
    guide_link_url,
    guide_text_url,
    monitor_links_enabled,
    osc_link,
)
from peerhub.cli.monitor_preferences import SHOW_QUOTA_NOTES_DEFAULT
from peerhub.core.monitor_groups import GROUPS
from peerhub.extensions.diag_quota import quota_stale_line
from peerhub.extensions.monitor_queries import TEXT_NEWEST_ROWS
from peerhub.extensions.monitor_read_model import context_estimate_age
from peerhub.extensions.observation_model import (
    ABSENT_FACT_REASONS,
    NOT_REPORTED_REASON,
    QUOTA_MAX_AGE_SECONDS,
    oldest_measured_quota_age,
    quota_probe_timed_out,
)
from peerhub.extensions.pool_health import PoolHealth
from peerhub.extensions.quota_windows import omitted_plan_windows, scoped_weekly_parents


# The top-to-bottom order below the title (#430). Compact ends with LEADS.
# Detail section keys are monitor group IDs.
FRAME_SECTIONS = {
    "pools": POOLS_HEADING,
    "suggest": "suggest:",
    "alerts": "ALERTS",
    "running": "RUNNING",
    "recent": "RECENT",
    "leads": "LEADS",
    "capacity": "QUOTA",
    "peers": "PEERS",
    "execution": "EXECUTION",
    "sessions": "SESSIONS",
    "usage": "USAGE",
    "context": "CONTEXT",
}
_FRAME_SECTION_KEYS = tuple(FRAME_SECTIONS)
FRAME_SECTION_ORDER = _FRAME_SECTION_KEYS[:_FRAME_SECTION_KEYS.index("leads") + 1]
FRAME_SECTION_HEADS = {key: FRAME_SECTIONS[key] for key in FRAME_SECTION_ORDER}
FRAME_HEADS = tuple(FRAME_SECTION_HEADS.values())
_FRAME_DETAIL_ORDER = _FRAME_SECTION_KEYS[len(FRAME_SECTION_ORDER):]

_HIDDEN_QUOTA_WINDOWS = "Unavailable quota windows hidden (use --show-quota-notes)"
COMPACT_WIDTH = 100
CONTEXT_ESTIMATE_LABEL = "est"
_LEAD_MARK_PCT = 80  # mirrors monitor_read_model.LEAD_CONTEXT_MARK_PCT: the `!` of a lead's ctx cell


def _quota_label(status: str, group: dict[str, Any]) -> str:
    label = f"{FRAME_SECTIONS['capacity']} [{status}] | measured remaining"
    candidates: list[tuple[float, str]] = []
    for row in as_dict(as_dict(group.get("data")).get("_pool_rows")).values():
        facts: PoolHealth = row["health"]
        left, exhaust = facts["left_pct"], facts["exhaust_in_seconds"]
        if facts["stale"] or left is None:
            continue
        if facts["reserve"] == "out":
            when = "exhausted"
        elif exhaust is not None and facts["exhaust_before_reset"]:
            when = "past forecast" if exhaust <= 0 else f"in {format_duration(exhaust)}"
        else:
            continue
        candidates.append((exhaust or 0, f"{row['peer']} {row['pool']} {pct(left / 100)} {when}"))
    return f"{label} | soonest exhaust: {min(candidates)[1]}" if candidates else label


def _sample_note(gid: str, data: dict[str, Any]) -> str:
    """`sample N/M` for a section that read a capped sample (M = the total, when the read model counted it)."""
    if gid not in {"execution", "sessions", "usage", "context"} or not data.get("truncated"):
        return ""
    read = finite_number(data.get("sample_size"))
    if read is None:
        read = next((len(cast(list[object], value)) for value in data.values() if isinstance(value, list)), None)
    total = finite_number(data.get("sample_total"))
    return f"sample {format_count(read)}" + (f"/{format_count(total)}" if total is not None and total >= (read or 0) else "")


def _group_label(gid: str, group: dict[str, Any], status: str, read_at: float | None, elapsed: float) -> str:
    if gid == "capacity":
        return _quota_label(status, group)
    if gid == "alerts":
        return f"{FRAME_SECTIONS[gid]} {len(dict_items(as_dict(group.get('data')).get('alerts')))}"
    head = FRAME_SECTIONS[gid] if gid in _FRAME_DETAIL_ORDER else gid.upper()
    label = f"{head} [{status}]"
    note = _sample_note(gid, as_dict(group.get("data")))
    label += f" | {note}" if note else ""
    return label if gid in {str(item["id"]) for item in GROUPS} else label + " [unrecognized group]"


def _coverage(data: dict[str, Any], unicode: bool = True) -> str:
    coverage = as_dict(data.get("coverage"))
    if coverage.get("truncated") or data.get("truncated"):
        return f"partial {'·' if unicode else '|'} sample truncated"
    return ""


def _usage_values(telemetry: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    normalized, reported = as_dict(telemetry.get("normalized")), as_dict(telemetry.get("reported"))
    tokens = finite_number(normalized.get("provider_total_tokens", reported.get("total_tokens")))
    inputs = finite_number(normalized.get("provider_input_tokens", reported.get("input_tokens")))
    outputs = finite_number(normalized.get("output_tokens", reported.get("output_tokens")))
    cached = finite_number(normalized.get("cached_input_tokens", reported.get("cached_input_tokens")))
    if tokens is None and inputs is not None and outputs is not None and inputs >= 0 and outputs >= 0:
        tokens = inputs + outputs
    if inputs is None or inputs <= 0 or cached is None or not 0 <= cached <= inputs:
        inputs = cached = None
    return tokens if tokens is not None and tokens >= 0 else None, inputs, cached


def _estimate(context: dict[str, Any], now: float | None) -> str | None:
    """An estimated context (#376 P2) as text: `2% est, 3m ago`. The read model's ESTIMATED state is the cc adapter's
    reading at the end of an ask, so it always carries the word and its age (`age ?` without a time) and is never shown
    as measured. None for every other state or an invalid percent."""
    percent = finite_number(context.get("percent"))
    if context.get("state") != "ESTIMATED" or percent is None or not 0 <= percent <= 100:
        return None
    age = context_estimate_age(finite_number(context.get("observed_at")), now)
    return f"{percent:.0f}% {CONTEXT_ESTIMATE_LABEL}, " + (
        "age ?" if age is None else f"{compact_age(age)} ago")


def _group_lines(group_id: str, group: dict[str, Any], *, width: int, unicode: bool,
                 elapsed_seconds: float = 0.0, read_at: float | None = None,
                 expand: bool = False, color: bool = False, bounded: bool = False,
                 keep: str | None = None, rich: bool = False, guide_url: str | None = None,
                 profile_urls: Mapping[str, Any] | None = None) -> tuple[list[str], int]:
    """Return rendered data rows and the length of the QUOTA table prefix."""
    data = as_dict(group.get("data"))
    if data == {} and group.get("data") is None:
        return ["data unavailable"], 0
    rows: list[str] = []
    table_size = 0
    sep = " · " if unicode else " | "
    dot = "·" if unicode else "|"
    bar = sep if rich else " | "  # joins the fields of a row that the plain view joins with a pipe in both glyph sets
    now = None if read_at is None else read_at + elapsed_seconds  # the time of this frame, for the age of an estimate

    if group_id == "overview":
        peers = dict_items(data.get("peers"))
        if peers:
            names = [f"{peer_label(item)} ({clean_text(item.get('display_name') or item.get('adapter_ref') or 'peer')})"
                     for item in peers]
            rows.append(f"Peers: {', '.join(names)}")
        else:
            rows.append(f"Peers: {format_count(data.get('sample_size'))} registered")
        if data.get("truncated") or data.get("session_sample_truncated") or data.get("peer_sample_truncated"):
            rows.append(f"Coverage: partial {dot} sample truncated")

    elif group_id == "peers":
        peer_items = dict_items(data.get("peers"))
        narrow_peers: list[list[str]] = []
        if not peer_items:
            rows.append("No peer evidence")
        for item in peer_items:
            pid = clean_text(item.get("peer_id") or "?")
            q_obj = as_dict(item.get("quota")) if isinstance(item.get("quota"), Mapping) else None
            p_obj = as_dict(item.get("profile")) if isinstance(item.get("profile"), Mapping) else None
            s_obj = as_dict(item.get("session")) if isinstance(item.get("session"), Mapping) else None
            c_obj = as_dict(item.get("context")) if isinstance(item.get("context"), Mapping) else None

            if q_obj is None:
                q_wide = "unknown"
                q_narr = "unk"
            else:
                q_state = clean_text(q_obj.get("state") or "").upper()
                q_pool = clean_text(q_obj.get("pool_ref") or "")
                if ":" in q_pool:
                    q_pool = q_pool.split(":")[-1]
                q_rem = finite_number(q_obj.get("remaining_fraction"))
                if q_rem is None:
                    q_pct = finite_number(q_obj.get("percent"))
                    q_rem = q_pct / 100 if q_pct is not None else None
                q_age = finite_number(q_obj.get("age_seconds"))

                q_scope = clean_text(q_obj.get("scope") or "")

                if q_pool == "not_reported":  # every configured pool omitted by the provider: unknown, never 100% free
                    q_wide = "? (not reported)" if data.get("_show_quota_notes") else "unknown"
                    q_narr = "? not reported" if data.get("_show_quota_notes") else "unk"
                elif q_state == "INCOMPLETE" or q_pool == "incomplete":
                    q_wide = "incomplete"
                    q_narr = "incomplete"
                elif q_state == "STALE":
                    q_wide = "STALE"
                    q_narr = "STALE"
                elif q_state != "MEASURED" or q_rem is None or not 0 <= q_rem <= 1:
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
                    plan_note = clean_text(as_dict(data.get("plan_notes")).get(pid) or "") if data.get("_show_quota_notes") else ""
                    q_wide = f"{pct(q_rem)}{pool_str}{age_str}" + (f" [{plan_note}]" if plan_note else "")
                    q_narr = f"{pct(q_rem)}{pool_str}"

            if p_obj is None:
                p_wide = "unknown [unknown]"
                p_narr = "unk [unk]"
            else:
                p_name = clean_text(p_obj.get("name") or "")
                if not p_name:
                    prof = clean_text(p_obj.get("profile") or "unknown")
                    mod = clean_text(p_obj.get("model") or "")
                    p_name = f"{prof}/{mod}" if mod else prof
                layer = clean_text(p_obj.get("source") or p_obj.get("config_layer") or "unknown")
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
                mode = clean_text(s_obj.get("execution_mode") or s_obj.get("mode") or "").lower()
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
                    gen_part = f"gen {format_count(gen)}" if gen is not None else "gen unknown"
                    s_wide = f"{mode_label} {gen_part}"
                    s_narr = s_wide

            if c_obj is None:
                c_wide = "unknown"
                c_narr = "unk"
            else:
                c_state = clean_text(c_obj.get("state") or "").upper()
                c_pct = finite_number(c_obj.get("percent"))
                c_tok = c_obj.get("tokens") or c_obj.get("occupancy_tokens")
                if isinstance(c_tok, (int, float)) and c_tok >= 1000:
                    tok_str = f"{int(c_tok) // 1000}k"
                elif c_tok is not None:
                    tok_str = clean_text(c_tok)
                else:
                    tok_str = ""

                if c_state == "MEASURED" and c_pct is not None:
                    pct_str = f"({c_pct:.0f}%)" if tok_str else f"{c_pct:.0f}%"
                    c_wide = f"{tok_str} {pct_str}".strip()
                    c_narr = f"{tok_str} {pct_str}".strip()
                else:  # an estimate (#376 P2) says so and gives its age; anything else is unknown
                    estimate = _estimate(c_obj, now)
                    c_wide, c_narr = estimate or "unknown", estimate or "unk"

            if width < 48:
                narrow_peers.append([f"{pid}: quota {q_narr}", p_narr, s_narr, f"ctx: {c_narr}" if c_narr != "unk" else ""])
            else:
                rows.append(f"{pid:<4}quota: {q_wide} -> {p_wide}")
                prefix = f"{pid} " if bounded else "    "
                rows.append(f"{prefix}-> latest session: {s_wide}{' -> context: ' + c_wide if c_wide != 'unknown' else ''}")

        if narrow_peers:  # one field set for all peers: drop trailing fields whole, never cut one with `~`
            shown = visible_fields(narrow_peers, bar, width - 2, keep=1)
            rows.extend(bar.join(field for field in fields[:shown] if field) for fields in narrow_peers)
            if shown < 4:
                rows.extend(omitted_rows([name for name, i in (("profile", 1), ("session", 2), ("ctx", 3))
                                           if i >= shown and any(fields[i] for fields in narrow_peers)], width - 2))

        if bounded:
            rows = [row for row in rows if "-> latest session:" not in row] + [
                row for row in rows if "-> latest session:" in row]
        if data.get("peer_sample_truncated"):  # #394: a capped list says so
            total = finite_number(data.get("peers_total"))
            rows.append(f"showing {len(peer_items)} of {format_count(total)} peers" if total is not None
                        else f"showing {len(peer_items)} peers; more are not shown")
        defaults_rev: object = data.get("defaults_revision")
        observed_at: object = data.get("observed_at")

        # One label per date (#394): the packaged defaults revision and its date here; "version check" is the facts report's.
        rev_str = f"profile defaults rev {clean_text(defaults_rev)}" if defaults_rev else "profile defaults rev unknown"
        if observed_at:
            obs_clean = clean_text(observed_at)
            age_str = "age unknown"
            if read_at is not None:
                try:
                    read_date = datetime.fromtimestamp(read_at, timezone.utc).date()
                    obs_date = date.fromisoformat(obs_clean)
                    days = (read_date - obs_date).days
                    age_str = "future" if days < 0 else f"{days}d ago"
                except (ValueError, OSError, OverflowError):
                    pass
            obs_str = f"({obs_clean}, {age_str})"
        else:
            obs_str = "(date unknown)"

        footer = (
            f"Model facts: {rev_str} {obs_str}{sep}"
            f"sources: codex debug models{bar}agy models{bar}claude: known ids"
        )
        # The rich --expand frame wraps a long line (nothing is cut); the plain view fits it to one line.
        rows.extend(wrap_text(footer, max(1, width)) if rich else [fit_text(footer, max(0, width - 1), unicode)])

        facts_rep: object = data.get("facts_report")
        if isinstance(facts_rep, dict):
            rep_dict = cast(dict[str, Any], facts_rep)
            status = clean_text(rep_dict.get("status") or "")
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
                chk_date = clean_text(str(rep_dict.get("checked_at") or "")[:10])
                facts_line = (f"Facts: needs_recheck{reason_str} - refresh-model-profiles"
                              + (f" (version check {chk_date})" if chk_date else ""))
            elif status == "ok":
                chk_date = str(rep_dict.get("checked_at") or "")
                chk_date_str = f" (version check {chk_date[:10]})" if chk_date else ""
                facts_line = f"Facts: ok{chk_date_str}"
            elif status == "unverified":
                facts_line = "Facts: unverified"
            else:
                facts_line = f"Facts: {status}" if status else "Facts: unknown"
            rows.extend(wrap_text(facts_line, max(1, width)) if rich
                        else [fit_text(facts_line, max(0, width - 1), unicode)])

    elif group_id == "execution":
        scopes = dict_items(data.get("scopes"))
        totals = {key: 0 for key in ("not_started", "may_have_started", "started", "terminal")}
        latency_ns = latency_count = 0
        for scope in scopes:
            for key in totals:
                totals[key] += int(finite_number(as_dict(scope.get("certainty")).get(key)) or 0)
            latency = as_dict(scope.get("latency_30d"))
            latency_ns += int(finite_number(latency.get("sum_ns")) or 0)
            latency_count += int(finite_number(latency.get("count")) or 0)
        if scopes:
            sampled = bool(data.get("truncated"))  # #394: the counts of a sample are not the store's totals
            rows.append("Delivery certainty" + (" (sample only)" if sampled else "") + ": " + ", ".join(
                f"{key.replace('_', ' ')} {format_count(value)}" for key, value in totals.items()))
            if sampled and data.get("_running_asks") is not None:  # the leases, which the sample does not decide
                rows.append(f"Running asks now: {format_count(data['_running_asks'])} (leases, not sampled)")
            avg = latency_ns / latency_count / 1_000_000_000 if latency_count else None
            rows.append(f"Measured delivery latency: {avg:.2f}s avg{sep}{latency_count} samples" if avg is not None
                        else "Measured delivery latency: unknown")
        else:
            rows.append("No delivery evidence")
        note = _coverage(data, unicode)
        if note:
            rows.append(f"Coverage: {note}")

    elif group_id == "sessions":
        sessions = dict_items(data.get("items"))
        if sessions:
            limit = len(sessions) if expand else min(2, len(sessions))
            field_rows: list[list[str]] = []
            for session in sessions[:limit]:
                binding = as_dict(session.get("configured_binding"))
                profile = clean_text(binding.get("profile") or "profile unknown")
                model = clean_text(binding.get("model") or "model unknown")
                effort = clean_text(binding.get("effort") or "effort unknown")
                resume = "resumable" if session.get("resumable") else "not resumable"
                handle = clean_text(session.get("handle") or "") if expand else ""
                uses = f"uses {format_count(session.get('uses'))}" if expand else ""
                estimate = _estimate(as_dict(as_dict(session.get("telemetry")).get("context")), now) if expand else None
                fields = [f"{peer_label(session)} {clean_text(session.get('state') or 'UNKNOWN')}", handle,
                          f"gen {format_count(session.get('generation'))}", uses, f"{profile} / {model} ({effort})", resume,
                          f"ctx {estimate}" if estimate else ""]
                if bounded and expand:
                    tokens, inputs, cached = _usage_values(as_dict(as_dict(session.get("telemetry")).get("usage")))
                    usage_text = f"tok {format_count(tokens)}"
                    if inputs is not None and cached is not None:
                        usage_text += f"{sep}cache {cached / inputs * 100:.0f}%"
                    fields.insert(2, usage_text)
                field_rows.append(fields)
            # One field set for the whole table: drop trailing fields whole (peer+state and handle always stay).
            shown = visible_fields(field_rows, sep, width - 2, keep=2)
            rows.extend(sep.join(field for field in fields[:shown] if field) for fields in field_rows)
            names = ["peer", "handle", "gen", "uses", "profile", "resumable", "ctx"]
            if bounded and expand:
                names.insert(2, "usage")
            if shown < len(names):
                rows.extend(omitted_rows([name for i, name in enumerate(names) if i >= shown
                                           and any(fields[i] for fields in field_rows)], width - 2))
            if not expand and len(sessions) > 2:
                rows.append(f"+{len(sessions) - 2} sessions not shown")
        else:
            rows.append(f"Retained sessions: {format_count(data.get('sample_size', 0))}")
        note = _coverage(data, unicode)
        if note:
            rows.append(f"Coverage: {note}")

    elif group_id == "usage":
        sessions = dict_items(data.get("sessions"))
        if sessions and bounded:
            for peer in sorted({peer_label(item) for item in sessions}):
                values = [_usage_values(as_dict(item.get("telemetry"))) for item in sessions if peer_label(item) == peer]
                measured = [tokens for tokens, _, _ in values if tokens is not None]
                inputs = sum(inputs or 0 for _, inputs, _ in values)
                cached = sum(cached or 0 for _, _, cached in values)
                cache_part = f"{sep}cache {cached / inputs * 100:.0f}%" if inputs > 0 else ""
                rows.append(f"{peer} provider-reported tokens: {format_count(sum(measured) if measured else None)}"
                            f"{sep}sessions {len(measured)} with/{len(values) - len(measured)} without usage{cache_part}")
        elif sessions:
            limit = len(sessions) if expand else min(2, len(sessions))
            for session in sessions[:limit]:
                telemetry = as_dict(session.get("telemetry"))
                normalized = as_dict(telemetry.get("normalized"))
                reported = as_dict(telemetry.get("reported"))
                tokens = normalized.get("provider_total_tokens", reported.get("total_tokens"))
                in_tok = finite_number(normalized.get("provider_input_tokens", reported.get("input_tokens")))
                out_tok = finite_number(normalized.get("output_tokens", reported.get("output_tokens")))
                cached_tok = finite_number(normalized.get("cached_input_tokens", reported.get("cached_input_tokens")))
                if tokens is None and in_tok is not None and out_tok is not None and in_tok >= 0 and out_tok >= 0:
                    tokens = int(in_tok + out_tok)
                cache_part = ""
                if cached_tok is not None and in_tok is not None and in_tok > 0 and 0 <= cached_tok <= in_tok:
                    cache_part = f"{sep}cache {cached_tok / in_tok * 100:.0f}%"
                rows.append(f"{peer_label(session)} provider-reported tokens: {format_count(tokens)}{cache_part}")
            if not expand and len(sessions) > 2:
                rows.append(f"+{len(sessions) - 2} sessions not shown")
        else:
            rows.append("Provider-reported usage: unknown")
        coverage = dict_items(data.get("coverage_by_scope"))
        complete = sum(1 for item in coverage if item.get("complete") is True)
        if coverage:
            rows.append(f"Telemetry coverage: {complete}/{len(coverage)} scopes complete")

    elif group_id == "context":
        sessions = dict_items(data.get("sessions"))
        if sessions:
            limit = len(sessions) if expand else min(2, len(sessions))
            for session in sessions[:limit]:
                context = as_dict(session.get("context"))
                percent = finite_number(context.get("percent"))
                state = clean_text(context.get("state") or "UNKNOWN").upper()
                estimate = _estimate(context, now)
                cells = max(3, min(12, width // 12))
                if estimate and percent is not None:  # #376 P2: an estimate with its age and its numbers, never MEASURED
                    rows.append(f"{peer_label(session)} {estimate} {fraction_bar(percent / 100, cells, unicode)}{sep}"
                                f"{format_count(context.get('occupancy_tokens'))} of {format_count(context.get('window_tokens'))} tokens")
                elif percent is not None and 0 <= percent <= 100 and state != "ESTIMATED":
                    rows.append(f"{peer_label(session)} {percent:.0f}% {fraction_bar(percent / 100, cells, unicode)}{sep}{state}")
                else:
                    rows.append(f"{peer_label(session)} UNKNOWN{sep}{clean_text(context.get('reason') or 'context occupancy unreported')}")
            if not expand and len(sessions) > 2:
                rows.append(f"+{len(sessions) - 2} sessions not shown")
        else:
            rows.append("Runtime context occupancy: unknown")

    elif group_id == "capacity":
        observations = dict_items(data.get("observations"))
        if observations:
            pool_rows = cast(dict[tuple[str, str], dict[str, Any]], data.get("_pool_rows") or {})
            probe_notes: dict[str, set[str]] = {}
            evidence_notes: list[str] = []
            na_notes: list[str] = []
            available: list[dict[str, Any]] = []
            show_notes = bool(data.get("_show_quota_notes", SHOW_QUOTA_NOTES_DEFAULT))
            for item in sorted(observations, key=lambda item: pool_order(
                    peer_label(item), str(item.get("pool_ref") or item.get("pool_kind") or "").rsplit(":", 1)[-1])):
                peer = peer_label(item)
                pool_ref = str(item.get("pool_ref") or "")
                state = clean_text(item.get("state") or "UNKNOWN")
                age = finite_number(item.get("age_seconds"))
                if not pool_ref or pool_ref == "quota" and state != "MEASURED":
                    code = f"PROBE:{compact_age(age + elapsed_seconds) if age is not None else 'unknown'}"
                    probe_notes.setdefault(peer, set()).add(code)
                    continue
                row = pool_rows.get((peer, clean_text(pool_ref))) or compact_pool(item, read_at or 0, elapsed_seconds, {})
                pool, reason = row["pool"], row["reason"]
                if reason.startswith(ABSENT_FACT_REASONS) and state in {"ABSENT", "STALE"} and not row["retired"]:
                    if row["state"] == QUOTA_NA and show_notes:
                        na_notes.append(na_pool_note(row))
                    elif row["aged"]:
                        why = f"{na_reason(row, reason)}, " if show_notes else ""
                        na_notes.append(f"{peer} {pool} ? {why}aged; next: peerhub observation refresh --peers {peer}")
                elif row["aged"]:
                    probe_notes.setdefault(peer, set()).add("STALE:window")
                elif state != "MEASURED" and not row["retired"]:
                    evidence_notes.append(f"{peer} {pool} evidence {state}; "
                                          f"next: peerhub observation refresh --peers {peer}")
                available.append(row)
            position = {(str(row["peer"]), str(row["pool"])): index
                        for index, (row, _) in enumerate(ordered_pools(available, every=True, for_quota=True))}
            layout, retired_notes = quota_layout(available, unicode, show_quota_notes=show_notes, order=position)
            entries = [
                (f"{row['display_peer'] or ' ' * cell_width(row['peer'])} {row['display_pool']}",
                 row, row["display_pool"] != row["pool"], str(row["suffix"]))
                for row in layout
            ]
            table_rows = list(pool_rows.values()) or available
            if not show_notes:
                table_rows = [row for row in table_rows if row["state"] != QUOTA_NA]
            omitted: list[str] = []
            if table_rows or entries or not available:
                header, rendered, omitted = pool_table(entries, table_rows, width - 1,
                                                        unicode, keep, rich=rich, paint=color, details=True,
                                                        guide_url=guide_url, profile_urls=profile_urls)
                rows.extend(header)
                previous_peer = ""
                for row, rendered_entry in zip(layout, rendered):
                    if previous_peer and previous_peer != row["peer"] and not rich:
                        rows.append("")
                    previous_peer = row["peer"]
                    rows.extend(rendered_entry)
            else:
                rows.append(_HIDDEN_QUOTA_WINDOWS)
            table_size = len(rows)
            notes = [*retired_notes, *na_notes, *evidence_notes,
                     *(f"{peer} {' '.join(sorted(codes))}; next: peerhub observation refresh --peers {peer}"
                       for peer, codes in probe_notes.items())]
            dim = RICH_SGR["dim"] if color else None
            rows.extend(paint_text(part, dim) for note in notes for part in wrap_text(note, max(1, width - 1)))
            rows.extend(omitted_rows(omitted, width - 1))
        else:
            rows.append("Quota capacity: no observations")
            table_size = len(rows)
        for credit in dict_items(data.get("reset_credits")):
            count = format_count(credit.get("available_count"))
            expires = finite_number(credit.get("nearest_expires_at"))
            left = None if expires is None or read_at is None else expires - read_at - elapsed_seconds
            expiry = "-" if expires is None else f"in {format_duration(left)}" if left and left > 0 else datetime.fromtimestamp(expires, timezone.utc).strftime("%Y-%m-%d %H:%MZ")
            state = clean_text(credit.get("state") or "UNKNOWN")
            rows.append(f"{peer_label(credit)} reset credits: {count}{bar}soonest {expiry} [{state}]")
        account_items = dict_items(data.get("accounts"))
        if account_items:
            rows.extend(_account_rows(account_items, elapsed_seconds, bar, max(1, width) if rich else None))
        note = _coverage(data, unicode)
        if note:
            rows.append(f"Coverage: {note}")

    elif group_id == "runtime":
        sessions = dict_items(data.get("sessions"))
        if sessions:
            limit = len(sessions) if expand else min(2, len(sessions))
            for session in sessions[:limit]:
                binding = as_dict(session.get("configured_binding"))
                current = as_dict(session.get("current_profile"))
                rows.append(f"{peer_label(session)} profile {clean_text(binding.get('profile') or 'unknown')}{sep}current {clean_text(current.get('source') or 'unknown')} / {clean_text(current.get('status') or 'UNKNOWN')}")
            if not expand and len(sessions) > 2:
                rows.append(f"+{len(sessions) - 2} runtime sessions not shown")
        else:
            rows.append("Runtime profile provenance: no sessions")

    elif group_id == "coverage":
        scopes = dict_items(data.get("scopes"))
        complete = sum(1 for scope in scopes if scope.get("delivery_complete") is True
                       and as_dict(scope.get("usage_coverage")).get("complete") is True)
        rows.append(f"Scopes complete: {complete}/{len(scopes)}" if scopes else "No scope evidence")
        if data.get("truncated"):
            rows.append(f"Coverage: partial {dot} sample truncated")

    elif group_id == "store":
        rows.append(f"Schema {format_count(data.get('schema_version'))}{sep}database {format_size(data.get('database_size_bytes'))}")

    elif group_id == "alerts":
        rows.extend(_alert_row(alert, unicode, keep, bar) for alert in dict_items(data.get("alerts")))
        rows.extend(cell_text(row, unicode, "-", keep) for row in cast(list[object], data.get("unread") or []))  # A7
        if not rows:
            rows.append("none")

    else:
        # Keep future fields visible without exposing a JSON-shaped dump.
        rows.extend(_fallback_rows(data))

    return [row for row in rows if not row.startswith("Coverage:")] or ["No data"], table_size


def _alert_row(alert: dict[str, Any], unicode: bool = True, keep: str | None = None, sep: str = " | ") -> str:
    age = finite_number(alert.get("age_s"))
    return sep.join((f"{cell_text(alert.get('kind'), unicode, '?', keep)}: {cell_text(alert.get('subject'), unicode, '?', keep)}",
                     f"age {'-' if age is None else compact_age(age)}", cell_text(alert.get("severity"), unicode, "?", keep),
                     cell_text(alert.get("code"), unicode, "?", keep)))


def _stamp(value: object) -> str:
    number = finite_number(value)
    return "?" if number is None else utc_text(number, "%Y-%m-%d %H:%M:%SZ") or "?"


def _ask_fields(row: dict[str, Any], unicode: bool = True, keep: str | None = None) -> list[str]:
    """Every field of one ask row (#374 C): the expand view hides nothing the read model gives. Text is made plain-view
    safe here, before packing, so a replaced character can never widen a packed line (A5)."""

    def txt(value: object, empty: str = "?") -> str:
        return cell_text(value, unicode, empty, keep)

    state = txt(row.get("state"))
    open_row = _is_open(row)
    fields: list[str] = []
    if open_row:
        flag = _flag(row)
        left = finite_number(row.get("lease_left_s"))
        reason = (f"lease expired {compact_age(-left)} ago" if flag == "!overdue" and left is not None and left < 0
                  else "lease expired" if flag == "!overdue" else "caller process gone" if flag == "!gone"
                  else txt(row.get("alive_reason"), "liveness unknown") if flag == "?" else "caller alive")
        fields.append(f"flag {flag or 'none'} ({reason})")
    fields.append(f"state {state}" + (f" {SUPERSEDED_MARK}" if row.get("superseded") else ""))
    if open_row:
        left = finite_number(row.get("lease_left_s"))
        fields += [f"elapsed {compact_age(finite_number(row.get('elapsed_s')))}", f"started {_stamp(row.get('started_at'))}",
                   "lease ?" if left is None else f"lease left {compact_age(left)}" if left >= 0
                   else f"lease expired {compact_age(-left)} ago"]
        if "_lease_age_s" in row:
            age = finite_number(row["_lease_age_s"])
            fields.append("lease confirmed -" if age is None else f"lease confirmed {compact_age(age)} ago")
    else:
        exact = row.get("ended_exact")
        ended = _stamp(row.get("ended_at")) + ("" if row.get("ended_at") is None
                                               else " (exact)" if exact else " (latest possible)")
        fields += [f"age {compact_age(finite_number(row.get('age_s'))) if exact else '?'}",
                   f"elapsed {compact_age(finite_number(row.get('elapsed_s')))}", f"started {_stamp(row.get('started_at'))}",
                   f"ended {ended}"]
    lead = as_dict(row.get("lead"))
    tokens = as_dict(row.get("tokens"))
    fields += [f"profile {txt(row.get('profile'))}", f"peer {txt(row.get('peer'))}",
               f"model {txt(row.get('model'))}", f"effort {txt(row.get('effort'))}",
               f"lead {txt(lead.get('label'))} ({txt(lead.get('tool'))} {txt(lead.get('id'))})" if lead else "lead none",
               f"name {txt(row.get('name'))}", f"stream {txt(row.get('stream'))}",
               f"tokens in {format_count(tokens.get('input'))} out {format_count(tokens.get('output'))}" if tokens else "tokens ?"]
    if row.get("note"):
        fields.append(f"note {txt(row['note'])}")
    reasons = [txt(reason) for reason in cast(list[object], row.get("reasons") or []) if reason]
    if reasons:
        fields.append("reasons " + ", ".join(reasons))
    return fields


def _ask_lines(row: dict[str, Any], fields_width: int, text_width: int, unicode: bool = True, keep: str | None = None,
               sep: str = " | ", *, links: bool = False) -> list[str]:
    """One ask without a mark: its fields packed to `fields_width`, then its reply (an ended ask) and its ask text
    wrapped to `text_width`."""
    lines = pack_fields(_ask_fields(row, unicode, keep), max(1, fields_width), sep)
    texts = [] if _is_open(row) else [("reply", row.get("reply_line_long"))]
    for label, text in [*texts, ("ask", row.get("ask_line_long"))]:
        wrapped = wrap_text(f"{label}: {cell_text(text, unicode, '-', keep)}", max(1, text_width))
        if links and label == "ask" and wrapped[0].startswith("ask:"):
            wrapped[0] = link_query(row, "ask") + wrapped[0][3:]
        lines.extend(wrapped)
    return lines


def _ask_rows(row: dict[str, Any], width: int, unicode: bool = True, keep: str | None = None) -> list[str]:
    lines = _ask_lines(row, width - 2, width, unicode, keep)
    lines[0] = "- " + lines[0]  # marks where each ask starts; continuation lines carry no mark
    return lines


def _clock(value: object, fmt: str) -> str:
    number = finite_number(value)
    return "?" if number is None else utc_text(number, fmt) or "?"


def _lead_rows(lead: dict[str, Any], now: float | None, width: int, unicode: bool = True,
               keep: str | None = None, *, sep: str = " | ", meter: bool = False, paint: bool = False) -> list[str]:
    """Every field of one lead (#376 3.4): its session (the lead and process, then model, context and asks, then name
    and cwd, then title, prompt and said), or why it has none. `meter` (the rich --expand frame, P23) puts the
    compact frame's context meter before the context numbers and its `!` at 80% or more; `paint` colours the meter
    as the compact frame does (bad from 80%, dim without a percent)."""

    def txt(value: object) -> str:
        return cell_text(value, unicode, "?", keep)

    head = [txt(lead.get("label")), f"{txt(lead.get('tool'))} {txt(lead.get('id'))}"]
    asks = [f"running {format_count(lead.get('running'))}", f"asks 24h {format_count(lead.get('asks_24h'))}",
            f"last ask {_stamp(lead.get('last_ask_at'))}"]
    item = as_dict(lead.get("session"))
    if not item:
        why = ("no session (not a Claude Code lead)" if lead.get("tool") != "cc"
               else "no session found (ended, or its Claude folder is not in PEERHUB_LEAD_DIRS)")
        return pack_fields([*head, why, *asks], width, sep)
    active, tokens = finite_number(item.get("active_at")), finite_number(item.get("context_tokens"))
    window, pct = finite_number(item.get("context_window")), finite_number(item.get("context_pct"))
    shown_pct = pct if tokens is not None and window is not None else None  # the percent the text names
    mark = "!" if meter and shown_pct is not None and shown_pct >= _LEAD_MARK_PCT else ""
    ctx = ("ctx ?" if tokens is None else f"ctx {format_count(tokens)} of {format_count(window)} model max ({int(pct)}%{mark})"
           if window is not None and pct is not None else f"ctx {format_count(tokens)} (window unknown)")
    if meter:
        ctx = _ctx_meter(ctx, shown_pct, unicode)
    first = [*head, f"{txt(item.get('state'))} since {_stamp(item.get('state_since'))}",  # A-4: with the date
             "active ?" if active is None or now is None else f"active {compact_age(now - active)} ago",
             "alive" if item.get("alive") is True else "alive ?", f"pid {txt(item.get('pid'))}",
             f"entry {txt(item.get('entry'))}", f"claude {txt(item.get('version'))}",
             f"started {_clock(item.get('started_at'), '%Y-%m-%d %H:%MZ')}"]
    second = [f"model {txt(item.get('model'))}", ctx + ("" if tokens is None else f" at {_stamp(item.get('context_at'))}"),
              *asks]
    third = [*([f"name {txt(item['name'])}"] if item.get("name") else []), f"cwd {txt(item.get('cwd'))}"]
    packed = [pack_fields(part, width, sep) for part in (first, second, third)]
    code = (None if not (meter and paint) else RICH_SGR["dim"] if shown_pct is None
            else RICH_SGR["bad"] if shown_pct >= _LEAD_MARK_PCT else None)
    if code:  # painted after packing, so the colour never changes a width; a meter cut off its `ctx` stays plain
        cells = ctx.split(" ")[1]
        for index, line in enumerate(packed[1]):
            at = line.find(f"ctx {cells} ")
            if at >= 0:
                packed[1][index] = line[:at + 4] + paint_text(cells, code) + line[at + 4 + len(cells):]
                break
    rows = [line for part in packed for line in part]
    for label, key in (("title", "title"), ("prompt", "prompt_line"), ("said", "said_line")):
        if item.get(key):
            rows.extend(wrap_text(f"{label}: {txt(item[key])}", width))
    return rows


def _recent_ended(data: dict[str, Any], groups: dict[str, Any]) -> list[dict[str, Any]]:
    """Finished asks in end order, with live session facts only on the latest ask of each scope."""
    running, ended, _ = _ask_split(data)
    ended.sort(key=lambda row: (finite_number(row.get("ended_at")) is None, -(finite_number(row.get("ended_at")) or 0.0)))
    newest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted([*running, *ended], key=lambda row: finite_number(row.get("started_at")) or 0, reverse=True):
        if row.get("stream"):
            newest.setdefault((str(row["stream"]), str(row.get("peer"))), row)

    samples: dict[tuple[str, str], dict[str, Any]] = {}
    for item in dict_items(as_dict(as_dict(groups.get("sessions")).get("data")).get("items")):
        key = (str(item.get("stream_id")), str(item.get("peer_id")))
        if key not in samples or (finite_number(item.get("generation")) or 0) > (finite_number(samples[key].get("generation")) or 0):
            samples[key] = item

    result: list[dict[str, Any]] = []
    for row in ended:
        key = (str(row.get("stream")), str(row.get("peer")))
        if key in newest and newest[key] is not row:
            result.append({**row, "session": None})
            continue
        session = as_dict(row.get("session"))
        sample = samples.get(key, {})
        context = as_dict(as_dict(sample.get("telemetry")).get("context"))
        started, observed = finite_number(row.get("started_at")), finite_number(context.get("observed_at"))
        if (not _recent_context(as_dict(session.get("context"))) and _recent_context(context)
                and (sample.get("provider") or session.get("provider") or row.get("peer")) in {"cx", "ag"}
                and not sample.get("tombstoned_at") and sample.get("state") != "TOMBSTONED"
                and (session.get("generation") is None or session.get("generation") == sample.get("generation"))
                and started is not None and observed is not None and observed >= started):
            row = {**row, "_context_estimate": context}
        result.append(row)
    return result


def _recent_head(data: dict[str, Any], ended: list[dict[str, Any]], *, superseded: bool = False) -> str:
    """`RECENT 130 (2 running)`: the finished asks, and how many more run (they are listed under RUNNING). The detail
    frames also report the superseded sample."""
    head = f"{FRAME_SECTIONS['recent']} {len(ended)} ({_running_head(data, _ask_split(data)[0], True)[1]} running)"
    omitted = finite_number(data.get("superseded_omitted")) or 0
    replaced = len(_ask_split(data)[2])
    if superseded and (replaced or omitted):
        head += f" | superseded {replaced}" + (f" (+{int(omitted)} not read)" if omitted else "")
    return head


def _kilo(value: object) -> str | None:
    """A token count as `850`, `62k`, `1M`, `1.2M`; None when it is not a count."""
    number = finite_number(value)
    if number is None or number < 0:
        return None
    count = int(number)
    if count < 1000:
        return str(count)
    if count < 999_500:
        return f"{round(count / 1000)}k"
    return f"{count / 1_000_000:.1f}".removesuffix(".0") + "M"


def _whole(value: object, high: float | None = None) -> int | None:
    number = finite_number(value)
    return int(number) if number is not None and number >= 0 and (high is None or number <= high) else None


def _recent_context(context: dict[str, Any]) -> bool:
    return (context.get("state") in {"ESTIMATED", "MEASURED"}
            and (_whole(context.get("occupancy_tokens")) is not None or _whole(context.get("percent"), 100) is not None))


def _recent_facts(row: dict[str, Any]) -> dict[str, Any]:
    """The ask's own tokens and any eligible live session or sampled context values."""
    session = as_dict(row.get("session"))
    mode_raw = session.get("execution_mode")
    estimate = as_dict(row.get("_context_estimate"))
    context = estimate or as_dict(session.get("context"))
    usable = _recent_context(context)
    tokens = as_dict(row.get("tokens"))
    read, total = _whole(tokens.get("cache_read")), _whole(tokens.get("input"))
    return {"mode": "R" if mode_raw in {"reused", "reuse", "resume"} else "F" if mode_raw == "fresh" else None,
            "uses": _whole(session.get("uses")), "generation": _whole(session.get("generation")),
            "ctx_tokens": _whole(context.get("occupancy_tokens")) if usable else None,
            "ctx_window": _whole(context.get("window_tokens")) if usable else None,
            "ctx_pct": _whole(context.get("percent"), 100) if usable else None, "ctx_estimate": bool(estimate),
            "input": total, "output": _whole(tokens.get("output")), "cache_read": read,
            "cache_write": _whole(tokens.get("cache_write")),
            "cache_pct": (int(read / total * 100 + 0.5) if read is not None and total and read <= total else None)}


NAME_CAP = 24
SUPERSEDED_MARK = "(superseded)"
_RECENT_DROPS = (("cache",), ("lead",), ("mode", "uses"), ("ctx",))  # the order narrow widths drop columns in


def _recent_compact(ended: list[dict[str, Any]], width: int, unicode: bool, keep: str | None, *, rich: bool = False,
                    paint: bool = False, links: bool = False, limit: int | None = None) -> list[str]:
    """The compact RECENT rows (the first `limit`, RECENT_CAP by default, of `ended`):
    `ok 3m cc.effort lead7 s-ab12 R u3 62k/6% c91% "reply snippet"`: state, age since the end, profile, lead, name,
    R resume | F fresh, `u<uses>`, context, cache-read share, the reply. A narrow width drops the cache share, then the
    lead, then the mode with the uses, then the context; the name and the reply stay, and the reply is cut by cell width.
    The rich view puts a state dot before the state when that costs no column."""
    glyphs = RICH_GLYPHS[unicode]

    def cell(value: object) -> str:
        return cell_text(value, unicode, "-", keep)

    shown = ended[:limit or RECENT_CAP]
    table: list[dict[str, str]] = []
    for row in shown:
        facts = _recent_facts(row)
        tokens, pct = _kilo(facts["ctx_tokens"]), facts["ctx_pct"]
        ctx = f"{tokens}/{pct}%" if tokens and pct is not None else tokens or (f"{pct}%" if pct is not None else "-")
        if facts["ctx_estimate"]:
            ctx += f" {CONTEXT_ESTIMATE_LABEL}"
        table.append({
            "state": cell(row.get("state")),
            "age": compact_age(finite_number(row.get("age_s"))) if row.get("ended_exact") else "?",
            "profile": cell(row.get("profile") or row.get("peer")),
            "lead": short_text(cell(as_dict(row.get("lead")).get("label")), LEAD_LABEL_COLS, unicode),
            "name": short_text(cell(row.get("name")), NAME_CAP, unicode),
            "mode": facts["mode"] or "-", "uses": "-" if facts["uses"] is None else f"u{facts['uses']}",
            "ctx": ctx, "cache": "-" if facts["cache_pct"] is None else f"c{facts['cache_pct']}%"})
    replies = [(short_text(cell(_recent_text(row)), REQUEST_CAP, unicode), bool(row.get("reply_line"))) for row in shown]
    order = ["state", "age", "profile", "lead", "name", "mode", "uses", "ctx", "cache"]
    floor = min(24, max(cell_width(text) + (2 if quoted else 0) for text, quoted in replies))  # what the reply is given first

    def room(columns: list[str], dot: int) -> int:
        fixed = sum(max(cell_width(cells[name]) for cells in table) + (dot if name == "state" else 0) + 2 for name in columns)
        return width - 1 - fixed

    def select(dot: int) -> list[str]:
        gone: set[str] = set()
        for step in range(len(_RECENT_DROPS) + 1):
            columns = [name for name in order if name not in gone]
            if room(columns, dot) >= floor or step == len(_RECENT_DROPS):
                return columns
            gone.update(_RECENT_DROPS[step])
        return order  # pragma: no cover - the loop always returns

    columns = select(0)
    dotted = rich and len(select(2)) >= len(columns)
    if dotted:
        columns = select(2)
    left = room(columns, 2 if dotted else 0)
    need = min(10, max(cell_width(text) + (2 if quoted else 0) for text, quoted in replies))
    for name in ("name", "profile"):  # nothing is left to drop: the name, then the profile, give up cells to the reply
        if left >= need:
            break
        widest = max(cell_width(fields[name]) for fields in table)
        cap = max(8, widest - (need - left))
        if cap < widest:
            for fields in table:
                fields[name] = fit_text(fields[name], cap, unicode)
            left = room(columns, 2 if dotted else 0)
    left = max(1, left)
    cells: list[list[str]] = []
    for fields, (text, quoted) in zip(table, replies):
        reply = f'"{fit_text(text, max(1, left - 2), unicode)}"' if quoted and left >= 3 else fit_text(text, left, unicode)
        row = [fields[name] for name in columns]
        if dotted:
            row[0] = f"{glyphs['dot']} {row[0]}"
        cells.append([*row, reply])
    colours = [_RECENT_COLOURS.get(str(row.get("state")), "bad") for row in shown]
    painter = column_painter(0, colours) if dotted and paint else None
    if links:
        painter = query_painter(shown, columns.index("name"), painter)
    return [" " + line for line in fit_table(cells, width - 1, unicode, right=(1,), elastic=len(columns), paint=painter)]


def _duration(seconds: object) -> str:
    """An elapsed time with its seconds: `45s`, `3m12s`, `1h02m`, `2d03h`; `?` when unknown."""
    number = finite_number(seconds)
    if number is None or number < 0:
        return "?"
    total = int(number)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    if total < 86400:
        return f"{total // 3600}h{total % 3600 // 60:02d}m"
    return f"{total // 86400}d{total % 86400 // 3600:02d}h"


def _recent_entry(row: dict[str, Any], width: int, unicode: bool, keep: str | None, sep: str = " | ", *,
                  links: bool = False) -> list[str]:
    """One expanded ask, with wrapped metadata followed by a token and text line."""
    facts = _recent_facts(row)

    def cell(value: object) -> str:
        return cell_text(value, unicode, "-", keep)

    model = re.sub(r"^claude-", "", clean_text(row.get("model") or ""))
    peer = cell(row.get("peer"))
    who = f"{peer}/{cell(model or None)}" + (f" [{cell(row.get('effort'))}]" if row.get("effort") else "")
    tokens, window, pct = _kilo(facts["ctx_tokens"]), _kilo(facts["ctx_window"]), facts["ctx_pct"]
    ctx = "-" if tokens is None and pct is None else " ".join(
        part for part in (f"{tokens}/{window}" if tokens and window else tokens, f"{pct}%" if pct is not None else None)
        if part)
    joined = any(facts[key] is not None for key in ("mode", "uses", "generation")) or (ctx != "-" and not facts["ctx_estimate"])
    if facts["ctx_estimate"]:
        ctx += f" {CONTEXT_ESTIMATE_LABEL}"
    first = " ".join([
        cell(row.get("state")), *([SUPERSEDED_MARK] if row.get("superseded") else []),
        f"{_clock(row.get('started_at'), '%H:%M')}-{_clock(row.get('ended_at'), '%H:%M')}",
        f"({_duration(row.get('elapsed_s'))})", who, cell(as_dict(row.get("lead")).get("label")), cell(row.get("name")),
        "gen-" if facts["generation"] is None else f"gen{facts['generation']}", facts["mode"] or "-",
        "u-" if facts["uses"] is None else f"u{facts['uses']}", f"ctx {ctx}", *(["now"] if joined else [])])
    cache = [part for part in (None if facts["cache_read"] is None else f"r{_kilo(facts['cache_read'])}",
                               None if facts["cache_write"] is None else f"w{_kilo(facts['cache_write'])}",
                               None if facts["cache_pct"] is None else f"{facts['cache_pct']}%") if part]

    def count(key: str) -> str:
        return _kilo(facts[key]) or "-"

    prefix = f"in {count('input')} out {count('output')} cache {' '.join(cache) or '-'}{sep}"
    ask = row.get("ask_line_long") or row.get("ask_line")
    said = row.get("reply_line_long") or row.get("reply_line")  # a note stands in for a reply that was never written
    reply = said or (f"({clean_text(row['note'])})" if row.get("note") else None)
    texts = [cell(ask), cell(reply)]
    room = max(2, width - cell_width(prefix) - cell_width('ask "" -> reply ""'))
    if sum(cell_width(text) for text in texts) > room:  # the shorter text keeps what it needs; the other takes the rest
        half = room // 2
        short, long = sorted(range(2), key=lambda i: cell_width(texts[i]))
        if cell_width(texts[short]) <= half:
            texts[long] = fit_text(texts[long], max(1, room - cell_width(texts[short])), unicode)
        else:
            texts = [fit_text(texts[0], max(1, half), unicode), fit_text(texts[1], max(1, room - half), unicode)]
    quote = [f'"{text}"' if value else text for text, value in zip(texts, (ask, said))]
    second = fit_text(f"{prefix}ask {quote[0]} -> reply {quote[1]}", width, unicode)
    if links and second.startswith(prefix + "ask "):
        second = prefix + link_query(row, "ask") + second[len(prefix) + 3:]
    return [*wrap_text(first, width), second]


def _recent_expand_lines(ended: list[dict[str, Any]], width: int, unicode: bool, keep: str | None, *, rich: bool = False,
                         paint: bool = False, links: bool = False) -> list[str]:
    """Expanded RECENT entries, with the outcome command below the ask and reply."""
    glyphs = RICH_GLYPHS[unicode]
    lines: list[str] = []
    for row in ended:
        inner = max(1, width - 3)
        first, *details = _recent_entry(row, inner, unicode, keep, f" {glyphs['sep']} " if rich else " | ", links=links)
        mark = glyphs["dot"] if rich else ""
        if mark and paint:
            mark = paint_text(mark, RICH_SGR[_RECENT_COLOURS.get(str(row.get("state")), "bad")])
        lines.extend([" " + (f"{mark} " if mark else "") + first, *("   " + detail for detail in details)])
        if row.get("_ask_id"):
            lines.extend("   " + part for part in wrap_text(
                f"peerhub outcome {row['_ask_id']} <accepted|rejected|retried>", inner))
    return lines


def _activity_notes(data: dict[str, Any], width: int, sep: str = " | ") -> list[str]:
    """What the activity sample left out: a truncated sample, and why a workspace generation was not read."""
    rows: list[str] = []
    if data.get("truncated"):
        rows.extend(pack_fields([f"sample truncated: {format_count(data.get('scopes_total'))} scopes",
                           f"superseded not read {format_count(data.get('superseded_omitted'))}"], width, sep))
    if data.get("reason"):
        rows.extend(pack_fields([f"workspace generation: {clean_text(data['reason'])}"], width, sep))
    return rows


def _safe_error_reason(value: object) -> str:
    """Expose a bounded exception class only; never print raw exception text."""
    raw = clean_text(value)
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
        parts = [clean_text(part).strip() for part in item.split(":")]
        peer = parts[0].lower() if parts and parts[0].lower() in {"cc", "cx", "ag"} else ""
        reason = parts[1] if peer and len(parts) > 1 else (parts[0] if parts else "")
        code = reason.lower() if reason.lower() in safe_reasons else "probe_warning"
        labels.append(f"{peer} {code}".strip())
    if len(warnings) > 3:
        labels.append(f"+{len(warnings) - 3} warnings")
    return labels


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
        label = f"{prefix}{clean_text(key).replace('_', ' ')}"
        if isinstance(value, Mapping):
            rows.extend(_fallback_rows(as_dict(cast(Mapping[str, Any], value)), f"{label} · ", depth + 1))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            rows.append(f"{label}: {format_count(len(cast(Sequence[Any], value)))} items")
        elif value is None:
            rows.append(f"{label}: unknown")
        else:
            rows.append(f"{label}: {clean_text(value)}")
    return [row for row in rows if not row.startswith("Coverage:")] or ["No data"]


def _account_failed(item: dict[str, Any]) -> bool:
    return item.get("stored_state") in {"ERROR", "UNAVAILABLE", "UNKNOWN"}


def _account_plan(item: dict[str, Any]) -> str:
    """The plan label (display only), or n/a (provider does not expose it) / ? (could not be determined)."""
    status = item.get("plan_status")
    return clean_text(item.get("plan")) if status == "reported" and item.get("plan") else "n/a" if status == "n/a" else "?"


def _account_login(item: dict[str, Any]) -> str:
    login = item.get("logged_in")
    return "ok" if login is True else "out" if login is False else "?"


def _account_rows(accounts: list[dict[str, Any]], elapsed: float, sep: str = " | ", wrap: int | None = None) -> list[str]:
    """Expanded ACCOUNTS rows (--expand only since #374): the stored fields of JSON capacity.accounts, labels spelled out.
    `sep` joins the fields of a row; with `wrap` (the rich --expand frame) a row wider than that carries whole fields
    to the next line."""
    rows = ["ACCOUNTS (plan is display data; the expiry is the login token's, not billing)"]
    if wrap is not None:
        rows = wrap_text(rows[0], wrap)

    def add(fields: list[str]) -> None:
        rows.extend([sep.join(fields)] if wrap is None else pack_fields(fields, wrap, sep))

    for item in accounts:
        peer = clean_text(item.get("peer_id") or "?")
        age = finite_number(item.get("age_seconds"))
        tail = f"age {compact_age(age + elapsed if age is not None else None)} [{clean_text(item.get('state') or 'UNKNOWN')}]"
        if _account_failed(item):
            add([f"{peer} account ?", f"probe error: {clean_text(item.get('probe_error') or 'unknown')}", tail])
            continue
        parts = [f"{peer} login {_account_login(item)}", f"plan {_account_plan(item)}"]
        if item.get("plan_changed"):
            parts.append("plan changed")
        expires = finite_number(item.get("login_token_expires_at"))
        status = item.get("login_expiry_status")
        shown = utc_text(expires, "%Y-%m-%d") if status == "reported" and expires is not None else None
        parts.append("login token expiry " + (shown or ("n/a" if status == "n/a" else "?")))
        if item.get("endpoint_access") is not None:
            parts.append(f"credits endpoint {clean_text(item.get('endpoint_access'))}")
        if item.get("auth_method"):
            parts.append(f"auth {clean_text(item.get('auth_method'))}")
        if item.get("probe_error"):  # the last probe failed; the reading above is the last good one
            parts.append(f"probe error: {clean_text(item.get('probe_error'))}")
        add([*parts, tail])
    return rows


def _compact_suggestions(profiles: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    choices: dict[str, list[tuple[bool, float, str]]] = {tier: [] for tier in ("eco", "normal", "quality", "top")}
    by_pool = {f"{row['peer']}:{row['pool']}": row for row in rows}
    for profile in profiles:
        if profile.get("suggest") is False:
            continue
        name = clean_text(profile.get("profile") or "")
        peer, _, suffix = name.partition(".")
        pools = cast(list[str], as_dict(as_dict(profile.get("fields")).get("pools")).get("value") or [])
        scores: list[float] = []
        fast = False
        for pool in pools:
            row = by_pool.get(f"{peer}:{pool}")
            if row is None or row["aged"] or row["state"] in {"?", "reset", "aged"}:
                break
            if row["state"] == QUOTA_NA:
                continue
            remaining = finite_number(row["remaining"])
            reset = finite_number(row["reset_seconds"])
            if remaining is None or remaining < .05 or reset is None or reset <= 0:
                break
            scores.append(remaining * 100 / max(reset / 86400, .2))
            fast = fast or row["state"] == "fast"
        else:
            if scores:
                tier = {"standard": "eco", "effort": "normal", "deepthink": "quality"}.get(suffix, "top")
                choices[tier].append((fast, min(scores), name))
    if not any(choices.values()):
        return f"{FRAME_SECTIONS['suggest']} no eligible profiles; next: peerhub observation refresh"
    suggestions: list[str] = []
    for tier, items in choices.items():
        ranked = sorted(items, key=lambda item: (item[0], -item[1], item[2]))
        pick = ranked[0][2] if ranked else "-"
        peer = pick.partition(".")[0]
        alternative = next((name for _, _, name in ranked if name.partition(".")[0] != peer), None)
        if alternative is not None:
            pick += f"|{alternative}"
        suggestions.append(f"{tier}={pick}")
    return FRAME_SECTIONS["suggest"] + " " + " ".join(suggestions)


# ---- compact frame (#374, P21, #412) ---------------------------------------------------------------------------------

COMPACT_MAX_LINES = 24
COMPACT_MAX_BYTES = 8192
FRAME_TRAILING_ROWS = 1  # render_grouped_frame's height includes the line after its final newline
RUNNING_CAP = 5
LEADS_CAP = 3
LEAD_LABEL_COLS = 16
RECENT_CAP = 5
POOLS_CAP = 8
POOLS_USE_FIRST_HINT = "use first"
REQUEST_CAP = 60
RECENT_EXPAND = 8
SECTION_WITH_DETAIL = 2  # one heading and one detail line
_ALERT_SOURCES = (("activity", "asks"), ("capacity", "quota"), ("accounts", "accounts"), ("facts", "facts"))
_SOURCE_PHRASES = {"not_selected": "not read", "error": "read failed", "truncated": "partial", "stale": "stale",
                   "unknown": "unknown"}  # unknown (#376 P14): no 7-day pool the provider reports has a number

METER_CELLS = 5  # the context meter of a LEADS row


def frame_alerts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The read model's alerts plus the agy token-use stop, which only the live monitor knows (before the low ones)."""
    alerts = dict_items(payload.get("alerts"))
    if "STOPPED" in str(payload.get("refresh_state") or ""):
        stop: dict[str, Any] = {"code": "a7", "kind": "agy stop", "subject": "next: --allow-agy-token-use to continue",
                                "age_s": None, "severity": "high"}
        at = next((i for i, item in enumerate(alerts) if item.get("severity") != "high"), len(alerts))
        alerts = [*alerts[:at], stop, *alerts[at:]]
    return alerts


def _alert_text(alert: dict[str, Any]) -> str:
    age = finite_number(alert.get("age_s"))
    text = f"{clean_text(alert.get('kind') or '?')}: {clean_text(alert.get('subject') or '?')}"
    return text if age is None else f"{text} {compact_age(age)}"


_SOURCE_REASONS = {"not_selected": "not selected", "error": "read failed", "truncated": "partial (sample cut)",
                   "stale": "stale", "unknown": "unknown (no 7-day pool has a number)"}


def unread_sources(payload: dict[str, Any]) -> list[str]:
    """`source <id>: <reason>` for every alert source that was not read completely, then the hint (for --expand).
    A frame without `alert_sources` (a schema error, a hand-made payload) says so, as line 1 does."""
    raw: object = payload.get("alert_sources")
    if not isinstance(raw, Mapping):
        return ["source alerts: not read"]
    sources = as_dict(cast(object, raw))
    given = as_dict(sources.get("reasons"))  # the read model's own reason per source, when it has one (lane B fix 5)

    def reason(key: str) -> str:
        text = given.get(key)
        return clean_text(text) if isinstance(text, str) and text.strip() else \
            _SOURCE_REASONS.get(str(sources.get(key)), "not read")

    rows = [f"source {key}: {reason(key)}" for key, _ in _ALERT_SOURCES if sources.get(key) != "read"]
    return rows + ([f"hint: {clean_text(sources['hint'])}"] if rows and sources.get("hint") else [])


def _unread(payload: dict[str, Any]) -> str | None:
    """None when every alert source was selected and read completely; otherwise what was not, and the hint."""
    raw: object = payload.get("alert_sources")
    if not isinstance(raw, Mapping):
        return "alerts not read"
    sources = as_dict(cast(object, raw))
    phrases: dict[str, list[str]] = {}
    for key, label in _ALERT_SOURCES:
        status = sources.get(key)
        if status != "read":
            phrases.setdefault(_SOURCE_PHRASES.get(str(status), "not read"), []).append(label)
    if not phrases:
        return None
    parts = [f"{', '.join(labels)} {phrase}" for phrase, labels in phrases.items()]
    if sources.get("hint"):
        parts.append(clean_text(sources["hint"]))
    return "; ".join(parts)


def verdict_line(payload: dict[str, Any], width: int, unicode: bool, keep: str | None = None, sep: str = " | ") -> str:
    """Line 1: `OK`, `OK? (... not read)` or `ATTN n` with two named alerts; the right-hand fields drop first.
    `sep` joins the fields (the rich view uses ` · `)."""
    alerts = frame_alerts(payload)
    counts = as_dict(as_dict(as_dict(as_dict(payload.get("groups")).get("activity")).get("data")).get("counts"))
    right: list[str] = []
    if finite_number(counts.get("running")) is not None:
        right.append(f"{format_count(counts['running'])} running")
    leads = finite_number(counts.get("leads"))
    if leads is not None:
        right.append(f"{format_count(leads)} {'lead' if leads == 1 else 'leads'}")
    low = as_dict(payload.get("min_quota"))
    if low:
        state = clean_text(low.get("state") or "?")
        aged = " (aged)" if low.get("aged") is True and state != "aged" else ""  # A6: the pool's observation is stale
        if url := payload.get("_state_doc_url"):
            state = osc_link(f"{url.partition('#')[0]}#{STATE_WORDS_ANCHOR}", state)
        right.append(f"min quota {format_count(low.get('left_pct'))}% {clean_text(low.get('pool') or '?')} {state}{aged}")
    if not unicode:
        right = [ascii_text(item, keep) for item in right]
    if not alerts:
        unread = _unread(payload)
        head = "OK" if unread is None else f"OK? ({unread})"
        head = head if unicode else ascii_text(head, keep)
        for fields in range(len(right), -1, -1):
            line = sep.join([head, *right[:fields]])
            if cell_width(line) <= width:
                return line
        return fit_text(head, width, unicode)
    head = f"ATTN {len(alerts)}" + ("?" if _unread(payload) is not None else "")  # A7: a source was not read
    names = [_alert_text(alert) if unicode else ascii_text(_alert_text(alert), keep) for alert in alerts[:2]]

    def compose(shown: int, fields: int) -> str:
        rest = len(alerts) - shown
        return sep.join([head, *names[:shown], *([f"+{rest}"] if rest else []), *right[:fields]])

    for shown in range(len(names), 0, -1):
        for fields in (range(len(right), -1, -1) if shown == len(names) else (0,)):
            line = compose(shown, fields)
            if cell_width(line) <= width:
                return line
    tail = f"{sep}+{len(alerts) - 1}" if len(alerts) > 1 else ""
    room = width - cell_width(f"{head}{sep}{tail}")
    if room >= 4:
        return f"{head}{sep}{fit_text(names[0], room, unicode)}{tail}"
    return fit_text(compose(0, 0), width, unicode)


def _decorate_verdict(verdict: str, sep: str, clock: str, width: int, paint: bool) -> str:
    """Line 1 of the rich look (P21): the status word as a pill, the separators dim, and the time at the right edge when
    two cells of room remain before it. `verdict` was fitted for two cells less: the pill's padding."""
    match = re.match(r"OK\??|ATTN \d+\??", verdict)
    head = match[0] if match else ""
    gap = width - cell_width(verdict) - (2 if head else 0) - cell_width(clock)
    timed = bool(clock) and gap >= 2
    if not paint:
        return verdict + (" " * (gap + (2 if head else 0)) + clock if timed else "")
    dim = RICH_SGR["dim"]
    pill = paint_text(f" {head} ", PILL_SGR["ATTN" if head.startswith("ATTN") else head]) if head else ""
    return pill + verdict[len(head):].replace(sep, paint_text(sep, dim)) + (" " * gap + paint_text(clock, dim) if timed else "")


@dataclass
class CompactFrame:
    """The compact frame before the budget: each section as built, with the totals its overflow line discloses."""
    verdict: str
    header: list[str] = field(default_factory=lambda: list[str]())  # line 2, then the lines that belong to it
    alerts: list[str] | None = None
    running: list[str] | None = None  # [RUNNING header, *rows]; None when `activity` is not selected
    running_total: int = 0
    leads: list[str] | None = None  # [LEADS header, *rows]; None when there is no lead to show and the read was complete
    leads_total: int = 0
    recent: list[str] | None = None  # [RECENT header, *rows]; None when there is no ended ask
    recent_short: list[str] | None = None  # --expand: the rows of `recent` (entries of two lines) as one line each
    suggest: list[str] = field(default_factory=lambda: list[str]())
    pool_notes: list[str] = field(default_factory=lambda: list[str]())
    pool_gap: list[str] = field(default_factory=lambda: list[str]())
    pool_legend: list[str] = field(default_factory=lambda: list[str]())
    pools: list[str] | None = None  # [POOLS header, *rows]; None when `capacity` is not selected
    pools_total: int = 0
    pools_header_lines: int = 1


def fit_compact_frame(frame: CompactFrame, *, width: int, height: int | None, unicode: bool,
                      max_lines: int = COMPACT_MAX_LINES, max_bytes: int = COMPACT_MAX_BYTES,
                      keep: str | None = None, pool_width: int | None = None) -> str:
    """Fit the shared section order to the width, line and byte budgets."""
    limit = max_lines if height is None else min(max_lines, height - FRAME_TRAILING_ROWS)
    if limit <= 0 or width <= 0:
        return ""

    verdict = frame.verdict
    for lines in _frame_line_ladder(frame, width, unicode, keep, pool_width=pool_width):
        verdict = lines[0]
        if len(lines) <= limit:
            if sum(len(line.encode("utf-8")) + 1 for line in lines) <= max_bytes:
                return "\n".join(lines) + "\n"
            lines = [FILE_HYPERLINK.sub(r"\1", line) for line in lines]
            if sum(len(line.encode("utf-8")) + 1 for line in lines) <= max_bytes:
                return "\n".join(lines) + "\n"
    return fit_bytes(verdict, max_bytes - 1, unicode) + "\n"


def _frame_line_ladder(frame: CompactFrame, width: int, unicode: bool, keep: str | None,
                       *, pool_width: int | None = None) -> Iterator[list[str]]:
    """Yield the fitted frame, then shrink and drop each section from the bottom up."""

    def fit(line: str, columns: int = width) -> str:
        return fit_text(line if unicode else ascii_text(line.replace(" · ", " | ").replace("·", "|"), keep), columns, unicode,
                    painted=True, indent=True)

    def fitted(lines: list[str] | None, columns: int) -> list[str] | None:
        return None if lines is None else [fit(line, columns) for line in lines]

    verdict = fit(frame.verdict)
    header = [fit(line) for line in frame.header]
    parts = {key: fitted(getattr(frame, key), (pool_width or width) if key == "pools" else width)
             for key in FRAME_SECTION_ORDER}
    pool_notes = [fit(line, pool_width or width) for line in frame.pool_notes]
    pool_gap = [fit(line) for line in frame.pool_gap]
    pool_legend = [fit(line) for line in frame.pool_legend]
    totals = {"running": frame.running_total, "leads": frame.leads_total, "pools": frame.pools_total}
    nouns = {"running": "more", "leads": "leads", "pools": "pools"}
    header_lines = {"pools": frame.pools_header_lines}

    def section(key: str) -> list[str]:
        lines = parts[key]
        if lines is None:
            return []
        rows = len(lines) - header_lines.get(key, 1)
        hidden = totals.get(key, 0) - rows
        extra = [overflow(hidden, nouns[key], width, unicode)] if hidden > 0 and rows > 0 else []
        return [*lines, *extra, *pool_notes, *pool_gap, *pool_legend] if key == "pools" else [*lines, *extra]

    def assemble() -> list[str]:
        return [verdict, *header, *(line for key in FRAME_SECTION_ORDER for line in section(key))]

    def pop_rows(key: str, floor: int) -> bool:
        lines = parts[key]
        heading = header_lines.get(key, 1)
        if lines is None or len(lines) - heading <= floor:
            return False
        total = totals.get(key, 0)
        rows = len(lines) - heading
        created = total - rows <= 0 < total  # this removal makes the overflow line
        if created and floor >= 1 and rows <= 2:  # A-3: K5 would leave the header without a row; do nothing
            return False
        lines.pop()
        if created and len(lines) > heading:  # one row for one overflow line would save nothing (K5)
            lines.pop()
        return True

    yield assemble()
    for key in reversed(FRAME_SECTION_ORDER):
        rows = parts[key]
        if key == "pools" and pool_notes:
            pool_notes.clear()
            yield assemble()
        if rows and key == "recent" and frame.recent_short:
            rows[1:] = [fit(line) for line in frame.recent_short]
            yield assemble()
        if key in totals:
            while pop_rows(key, 1):
                yield assemble()
        elif rows:
            floor = 1 if key == "suggest" else SECTION_WITH_DETAIL
            while len(rows) > floor:
                rows.pop()
                yield assemble()
        if key == "pools":
            if pool_legend:
                pool_legend.clear()
                yield assemble()
            while len(header) > 1:  # title details give way before the final pool row
                header.pop()
                yield assemble()
        if rows:
            parts[key] = None
            yield assemble()
    if header:
        header.pop()
        yield assemble()


def _flag(row: dict[str, Any]) -> str:
    if row.get("state") == "overdue":
        return "!overdue"
    if row.get("alive") is False:
        return "!gone"
    return "?" if row.get("alive") is None else ""


def _is_open(row: dict[str, Any]) -> bool:
    """`run`, or `overdue` without an end: the read model ends an overdue row once the alert window has passed."""
    return row.get("state") == "run" or (row.get("state") == "overdue" and row.get("ended_at") is None)


def _ask_split(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Running, ended and superseded rows, each in the order the read model gives."""
    asks = dict_items(data.get("asks"))
    main = [row for row in asks if not row.get("superseded")]
    return ([row for row in main if _is_open(row)], [row for row in main if not _is_open(row)],
            [row for row in asks if row.get("superseded")])


def _running_head(data: dict[str, Any], running: list[dict[str, Any]], unicode: bool,
                  keep: str | None = None) -> tuple[str, int]:
    counts = as_dict(data.get("counts"))
    number = finite_number(counts.get("running"))
    total = int(number) if number is not None and number >= 0 else len(running)
    by_peer = as_dict(counts.get("by_peer"))
    peers = " ".join(f"{cell_text(peer, unicode, '-', keep)} {format_count(n)}" for peer, n in by_peer.items())
    return f"{FRAME_SECTIONS['running']} {total}" + (f" | {peers}" if peers and total else ""), total


def _recent_text(row: dict[str, Any]) -> object:
    if row.get("reply_line"):
        return row["reply_line"]
    if row.get("note"):
        return f"({clean_text(row['note'])})"
    return "-" if row.get("state") == "ok" else "(no reply)"


_RUN_MARKS = {"": ("run", "", "ok"), "!gone": ("gone", " gone", "bad"), "!overdue": ("", "! overdue", "warn"),
              "?": ("", "?", "warn")}  # a RUNNING row's flag: its glyph, the word beside it, its colour
_RECENT_COLOURS = {"ok": "ok", "unsure": "warn", "overdue": "warn"}  # every other ended state is bad


def _activity_sections(groups: dict[str, Any], width: int, unicode: bool, keep: str | None = None, *,
                       rich: bool = False, paint: bool = False, links: bool = False) -> tuple[list[str], int, list[str] | None]:
    """RUNNING (header and at most five rows, the total before the cap) and RECENT (`_recent_compact`: at most RECENT_CAP
    finished asks, newest first, or None). The rich view (P21) draws each head as a rule, starts a RUNNING row with its
    state glyph (and the word of a flag) and a RECENT row with a state dot; such a cell is used only when it costs no
    column."""
    def head_line(head: str) -> str:
        return heading_rule(head, width, unicode, paint) if rich else head

    group = as_dict(groups.get("activity"))
    if not isinstance(group.get("data"), Mapping):
        why = "read failed" if clean_text(group.get("status") or "").upper() in {"ERROR", "FAILED"} else "not read"
        return [head_line(f"{FRAME_SECTIONS['running']} ? | activity {why}")], 0, None
    data = as_dict(group.get("data"))
    glyphs = RICH_GLYPHS[unicode]

    def cell(value: object) -> str:  # text cells keep what the plain view's encoding can show (A5)
        return cell_text(value, unicode, "-", keep)

    running = _ask_split(data)[0]
    head, total = _running_head(data, running, unicode, keep)
    shown = running[:RUNNING_CAP]
    flags = [_flag(row) for row in shown]
    flagged = any(flags)
    table = [[*([flag] if flagged else []), compact_age(finite_number(row.get("elapsed_s"))),
              cell(row.get("profile") or row.get("peer")), cell(as_dict(row.get("lead")).get("label")),
              short_text(cell(row.get("ask_line")), REQUEST_CAP, unicode), cell(row.get("name"))]
             for row, flag in zip(shown, flags)]
    first = 1 if flagged else 0
    painter = None
    if rich and table:
        marks = [_RUN_MARKS[flag] for flag in flags]
        marked = [[glyphs.get(glyph, "") + word, *row[first:]] for (glyph, word, _), row in zip(marks, table)]
        if table_columns(marked, width - 1, 4)[0] - 1 >= table_columns(table, width - 1, first + 3)[0] - first:
            table, first = marked, 1
            painter = column_painter(0, [colour for _, _, colour in marks]) if paint else None
    if links:
        painter = query_painter(shown, first + 3, painter)
    rows = [" " + line for line in fit_table(table, width - 1, unicode, right=(first,), elastic=first + 3, paint=painter)]
    recent: list[str] | None = None
    ended = _recent_ended(data, groups)
    if ended:
        recent = [head_line(_recent_head(data, ended)),
                  *_recent_compact(ended, width, unicode, keep, rich=rich, paint=paint, links=links)]
    return [head_line(head), *rows], total, recent


def _leads_header(data: dict[str, Any], leads: list[dict[str, Any]]) -> str:
    """Count the displayed entries and matched sessions separately; a found session need not be confirmed alive.

    Keep read warnings before the session statistic so they survive a narrow frame. With no rows after an incomplete
    read the entry count stays `?`; an older payload with no read evidence must not claim zero sessions were found.
    """
    count = len(leads)
    read = as_dict(data.get("leads_read"))
    state = read.get("state")
    notes = [f"{format_count(read.get(key))} {words}" for key, words in (("unreadable", "unreadable"), ("not_read", "not read"))
             if (finite_number(read.get(key)) or 0) > 0]
    note = (" | partial" + (f" ({', '.join(notes)})" if notes else "") if state == "partial"
            else " | read failed" if state == "error" else "")
    if note and count == 0:
        return f"{FRAME_SECTIONS['leads']} ?{note}"
    header = f"{FRAME_SECTIONS['leads']} {count} {'entry' if count == 1 else 'entries'}{note}"
    sessions = sum(bool(as_dict(lead.get("session"))) for lead in leads)
    if sessions or state in {"read", "partial", "error"}:
        header += f" | {sessions} {'session' if sessions == 1 else 'sessions'} found"
    return header


def _ctx_cell(item: dict[str, Any]) -> str:
    pct, tokens = finite_number(item.get("context_pct")), finite_number(item.get("context_tokens"))
    if pct is not None:
        return f"ctx {int(pct)}%" + ("!" if pct >= _LEAD_MARK_PCT else "")
    if tokens is not None and tokens >= 0:
        return f"ctx {int(tokens) // 1000}k" if tokens >= 1000 else f"ctx {int(tokens)}"
    return "ctx ?"


def _lead_cells(lead: dict[str, Any], now: float, unicode: bool, keep: str | None) -> list[str]:
    """label, state with the flag, age of the last activity, ctx, running asks, model, title (empty when unknown)."""
    label = short_text(cell_text(lead.get("label"), unicode, "?", keep), LEAD_LABEL_COLS, unicode)
    run = f"{format_count(lead.get('running'))} run"
    item = as_dict(lead.get("session"))
    if not item:
        return [label, "-", "-", "-", run, "-", ""]
    known = cell_text(item.get("state"), unicode, "", keep)
    state = ("?" if item.get("alive") is None else "") + known if known else "?"  # the flag only on a known state
    active = finite_number(item.get("active_at"))
    model = re.sub(r"^claude-", "", clean_text(item.get("model") or ""))
    return [label, state, "-" if active is None else compact_age(now - active), _ctx_cell(item), run,
            short_text(cell_text(model or None, unicode, "-", keep), 16, unicode), cell_text(item.get("title"), unicode, "", keep)]


def _ctx_meter(cell: str, pct: float | None, unicode: bool) -> str:
    """The ctx cell with its meter (P21): `ctx ■■□□□ 46%`; a cell without a percent (`ctx 106k`, `ctx ?`) gets the
    meter without a value, and a lead without a session keeps its `-`."""
    if not cell.startswith("ctx "):
        return cell
    glyphs = RICH_GLYPHS[unicode]
    if pct is None:
        return f"ctx {glyphs['none'] * METER_CELLS} {cell[4:]}"
    filled = min(METER_CELLS, max(0, int(pct / 100 * METER_CELLS + 0.5)))
    return f"ctx {glyphs['on'] * filled}{glyphs['off'] * (METER_CELLS - filled)} {cell[4:]}"


def _leads_section(groups: dict[str, Any], now: float, width: int, unicode: bool, keep: str | None = None, *,
                   rich: bool = False, paint: bool = False) -> tuple[list[str] | None, int]:
    """LEADS (header and at most LEADS_CAP rows of the leads with a session or a running ask, the total before the cap),
    or None when there is no such lead and the lead sessions were read completely. The rich view (P21) draws the head
    as a rule and the context as a meter, unless the meter would cost a column."""
    data = as_dict(as_dict(groups.get("activity")).get("data"))
    leads = [lead for lead in dict_items(data.get("leads")) if lead.get("session") or (finite_number(lead.get("running")) or 0) > 0]
    header = _leads_header(data, leads)
    if not leads and as_dict(data.get("leads_read")).get("state") not in {"partial", "error"}:
        return None, 0
    table = [_lead_cells(lead, now, unicode, keep) for lead in leads[:LEADS_CAP]]
    titled = any(row[-1] for row in table)
    table = [row if titled else row[:-1] for row in table]
    elastic = 6 if titled else None
    pcts = [finite_number(as_dict(lead.get("session")).get("context_pct")) for lead in leads[:LEADS_CAP]]
    metered = [[*row[:3], _ctx_meter(row[3], pct, unicode), *row[4:]] for row, pct in zip(table, pcts)]
    meters = rich and table_columns(metered, width - 1, elastic)[0] >= table_columns(table, width - 1, elastic)[0]

    def painter(index: int, col: int, text: str) -> str:
        if col != 3 or not text.startswith("ctx "):
            return text
        pct = pcts[index]
        if pct is None:  # no value: the meter is structure
            meter, _, value = text[4:].partition(" ")
            return f"ctx {paint_text(meter, RICH_SGR['dim'])} {value}"
        return f"ctx {paint_text(text[4:], RICH_SGR['bad'])}" if pct >= _LEAD_MARK_PCT else text

    rows = fit_table(metered if meters else table, width - 1, unicode, right=(2,), elastic=elastic,
                  paint=painter if meters and paint else None)
    return [heading_rule(header, width, unicode, paint) if rich else header, *(" " + line for line in rows)], len(leads)


def _pools_heading(title: str, head: str, width: int, unicode: bool, rich: bool, paint: bool) -> str:
    hint = f"  {POOLS_USE_FIRST_HINT}"
    if rich and head.startswith(title + " " * RULE_PREFIX_WIDTH):
        # The rule adds three leading cells. Spend the title's padding to keep the columns aligned.
        head = title + head[len(title) + RULE_PREFIX_WIDTH:]
    room = width - (RULE_PREFIX_WIDTH + RULE_MIN_TAIL_WIDTH if rich else 0)
    if cell_width(head + hint) <= room:
        head += hint
    return heading_rule(head, width, unicode, paint) if rich else head


def _pool_section(rows: list[dict[str, Any]], width: int, unicode: bool, keep: str | None = None, *,
                  rich: bool = False, paint: bool = False, every: bool = False, guide_url: str | None = None,
                  profile_urls: Mapping[str, Any] | None = None) -> tuple[list[str], int, int]:
    """POOLS rows, including inline markers and counts for any unavailable windows passed in."""
    ordered = ordered_pools([row for row in rows if row["state"] != QUOTA_NA], every=every)
    unavailable = [row for row in rows if row["state"] == QUOTA_NA]
    suffixes, marker_rows = fold_pool_markers(ordered, unavailable, unicode, keep)
    shown = ordered if every else ordered[:POOLS_CAP]
    head = f"{FRAME_SECTIONS['pools']} {len(ordered) + len(unavailable) - len(marker_rows)}"
    entries: list[PoolEntry] = []
    for index, (row, depth) in enumerate(shown):
        peer, pool = cell_text(row["peer"], unicode, "-", keep), cell_text(row["pool"], unicode, "-", keep)
        prefix = ""
        for level in range(1, depth + 1):
            sibling = next((d == level for _, d in shown[index + 1:] if d <= level), False)
            if level == depth:
                prefix += ("├─ " if sibling else "└─ ") if unicode else ("|- " if sibling else "`- ")
            else:
                prefix += ("│  " if unicode else "|  ") if sibling else "   "
        name = f"{' ' * cell_width(peer)} {prefix}{pool}" if depth else f"{peer} {pool}"
        entries.append(((" " + peer if id(row) in marker_rows else " " + name),
                        {} if id(row) in marker_rows else row, depth > 0, suffixes.get(id(row), "")))
    headers, table, _ = pool_table(entries, rows, width - 1, unicode, keep, rich=rich, paint=paint, head=head,
                                    guide_url=guide_url, profile_urls=profile_urls)
    lines = [line for entry in table for line in entry]
    headings = [_pools_heading(head, headers[0], width, unicode, rich, paint), *headers[1:]]
    return [*headings, *lines], len(lines) + len(ordered) - len(shown), len(headings)


def _suggest_lines(text: str, width: int, unicode: bool) -> list[str]:
    lines = wrap_text(text, width)
    return lines if len(lines) <= 2 else [lines[0], fit_text(" ".join(lines[1:]), width, unicode)]


def _frame_consumers(payload: dict[str, Any]) -> dict[str, list[str]]:
    consumers: dict[str, list[str]] = {}
    for profile in dict_items(payload.get("configured_profiles")):
        name = clean_text(profile.get("profile") or "?")
        peer = name.partition(".")[0]
        for raw_pool in cast(list[object], as_dict(as_dict(profile.get("fields")).get("pools")).get("value") or []):
            consumers.setdefault(f"{peer}:{clean_text(raw_pool)}", []).append(name)
    return consumers


def _frame_pools(payload: dict[str, Any], consumers: dict[str, list[str]]
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(the frame's observations, its quota pools, their rows): each pool as it is at the time of this redraw (the read
    time plus the display's elapsed time). The human observations are used when the payload has them."""
    read_at = finite_number(payload.get("read_at"))
    elapsed = finite_number(payload.get("display_elapsed_seconds")) or 0
    observations = (dict_items(payload.get("_human_observations")) if "_human_observations" in payload else
                    dict_items(as_dict(as_dict(as_dict(payload.get("groups")).get("capacity")).get("data")).get("observations")))
    quota = [item for item in observations if item.get("kind", "quota") == "quota" and item.get("pool_ref")]
    now = read_at if read_at is not None else 0
    rows = [compact_pool(item, now, elapsed, consumers) for item in quota]
    parents = scoped_weekly_parents(rows)
    for index, row in enumerate(rows):
        if (parent := parents.get(id(row))) is not None:
            rows[index] = {**compact_pool(quota[index], now, elapsed, consumers, parent["reading"]),
                           "depends_on": parent["pool"]}
    rows.sort(key=lambda row: pool_order(str(row["peer"]), str(row["pool"])))
    return observations, quota, rows


def _with_min_quota(payload: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """P9, the one rule of both frames: the read model's `min_quota` is as of the read, and a redraw ages the pools,
    so line 1 takes its pool from the aged rows. A payload without the human observations cannot age a stale pool
    (its last value is not public): it keeps the read model's `min_quota`."""
    return {**payload, "min_quota": display_min_quota(rows)} if "_human_observations" in payload else payload


def _render_compact_frame(payload: dict[str, Any], *, view: str, color: bool, unicode: bool,
                          width: int, height: int | None, keep: str | None = None) -> str:
    pool_width = max(1, width - 1)  # leave the terminal's autowrap column unused
    width = min(COMPACT_WIDTH - 1, pool_width)
    frame = _compact_frame(payload, view=view, color=color, unicode=unicode, width=width, height=height, keep=keep,
                           pool_width=pool_width)
    return fit_compact_frame(frame, width=width, height=height, unicode=unicode, keep=keep, pool_width=pool_width)


def _compact_frame(payload: dict[str, Any], *, view: str, color: bool, unicode: bool, width: int,
                   height: int | None, keep: str | None = None, expand: bool = False,
                   pool_width: int | None = None) -> CompactFrame:
    """Build the sections shared by both layouts before height fitting."""
    rich = view == "rich"  # P21: the rich look; `unicode` picks its glyphs and `paint` adds the colour
    paint = color and rich and "NO_COLOR" not in os.environ
    sep = f" {RICH_GLYPHS[unicode]['sep']} " if rich else " | "
    groups = as_dict(payload.get("groups"))
    selected = payload.get("selected_groups")
    selected_ids = set(cast(list[str], selected)) if isinstance(selected, list) else set(groups)
    read_at = finite_number(payload.get("read_at"))
    now = read_at if read_at is not None else 0
    elapsed = finite_number(payload.get("display_elapsed_seconds")) or 0
    configured_profiles = dict_items(payload.get("configured_profiles"))
    consumers = _frame_consumers(payload)
    observations, quota, rows = _frame_pools(payload, consumers)
    payload = _with_min_quota(payload, rows)
    store = as_dict(payload.get("store_selection"))
    source = clean_text(store.get("source") or "unknown")
    try:
        stamp = datetime.fromtimestamp(now + elapsed, timezone.utc).strftime("%Y-%m-%d %H:%MZ") if read_at is not None else "time unknown"
    except (ValueError, OverflowError, OSError):
        stamp = "time unknown"
    oldest = (finite_number(payload.get("_quota_age_seconds")) if "_quota_age_seconds" in payload
              else oldest_measured_quota_age(quota))
    age = oldest + elapsed if oldest is not None else None
    age_label = compact_age(age)
    guide_url = payload.get("_guide_url")
    guide_room = cell_width(GUIDE_TEXT) + 1
    title_width = width - guide_room if guide_url and width > guide_room else width
    refresh_state = clean_text(payload.get("refresh_state") or "")
    next_refresh = finite_number(payload.get("next_refresh_in"))
    live_refresh = as_dict(payload.get("_live_refresh"))

    def fit_title(room: int) -> tuple[str, bool]:
        if live_refresh:
            return fit_live_quota_title(live_refresh, refresh_state, stamp, source, room, unicode)
        details = [f"DB:{source}", f"quota age {age_label}"]
        if refresh_state or next_refresh is not None:
            return fit_live_title(refresh_state, next_refresh, stamp, details, room, unicode, inline_note=False)
        title = " | ".join([f"PeerHub Monitor {stamp}", *details])
        return fit_fields(title, room, unicode, inline_note=False), cell_width(title) > room

    title, cut = fit_title(title_width)
    title = title.replace(" | ", sep)  # the rich view's separator; line 2 is structure, so its colour is dim
    if guide_url and title_width < width:
        if not unicode:
            title = ascii_text(title.replace(" · ", " | ").replace("·", "|"), keep)
        title += " " * (width - cell_width(title) - cell_width(GUIDE_TEXT)) + osc_link(guide_url, GUIDE_TEXT)
        if cut and not expand:
            # Reserving the Guide may shorten the title, but it must not consume another row in the viewport.
            _, cut = fit_title(width)
    # Fields dropped from line 2 are disclosed on a line of their own, so a narrow terminal keeps the timestamp.
    header = [paint_text(title, RICH_SGR["dim"]) if paint else title]
    if (selected_ids & {"capacity", "peers"} or "_quota_age_seconds" in payload) and (notice := quota_stale_line(age)):
        header.extend(wrap_text(notice, width))
    header.extend(part for note in cast(list[str], payload.get("_quota_probe_notes", []))
                  for part in wrap_text(note, width))
    if cut and not expand:
        header.append(f"Fields hidden{sep}--expand")
    errors = as_dict(payload.get("errors"))
    nearest = payload.get("_nearest_store_path")
    same_store = bool(nearest and store.get("path") and os.path.normcase(os.path.abspath(str(nearest))) == os.path.normcase(os.path.abspath(str(store["path"]))))
    read_errors = any(value != "truncated" for value in errors.values())
    if ((not same_store or payload.get("status") in {"FAILED", "ERROR"} or read_errors) and store.get("path")
            and not expand):  # expanded frames include the full path in their header
        header.extend(location_lines(f"DB: {clean_text(store['path'])}", width, payload.get("_db_url")))
    if payload.get("_store_note") and not expand:  # #338: --db overrides a PEERHUB_DB that points elsewhere
        header.extend(wrap_text(str(payload["_store_note"]), width))
    header.extend(fit_text(f"Warning: {warning}", width, unicode)
                  for warning in _safe_refresh_warning_labels(payload.get("refresh_warnings")))
    if selected_ids & {"capacity", "peers"}:  # #328: a peer's newest quota probe failed before it named a pool
        for peer in sorted({str(item.get("peer_id") or "?") for item in observations if item.get("kind", "quota") == "quota"}):
            probes = [item for item in observations if str(item.get("peer_id") or "?") == peer and item.get("kind", "quota") == "quota"]
            latest = max(probes, key=lambda item: finite_number(item.get("effective_at_us")) or
                         (now - (finite_number(item.get("age_seconds")) or 0)) * 1_000_000)
            if not latest.get("pool_ref") and (latest.get("state") in {"ERROR", "UNAVAILABLE", "UNKNOWN"}
                                               or latest.get("stored_state") in {"ERROR", "UNAVAILABLE", "UNKNOWN"}):
                age = finite_number(latest.get("age_seconds"))
                header.append(f"{clean_text(peer)} probe error ({compact_age(age + elapsed if age is not None else None)})")
    evidenced = {f"{row['peer']}:{row['pool']}" for row in rows}
    missing_pool = any(key not in evidenced for key in consumers)
    if (selected_ids & {"capacity", "peers"} and (missing_pool or any(row["needs_refresh"] for row in rows))
            and (rows or "capacity" not in selected_ids)):
        header.append("next: peerhub observation refresh")
    if rich:  # the pill's padding takes two cells; the time is that of line 2
        verdict = _decorate_verdict(verdict_line(payload, max(1, width - 2), unicode, keep, sep), sep,
                                    stamp[-6:] if read_at is not None and stamp.endswith("Z") else "", width, paint)
    else:
        verdict = verdict_line(payload, width, unicode, keep)
    frame = CompactFrame(verdict=verdict, header=header)
    frame.alerts = _alert_section(payload, width, unicode, keep, rich=rich, paint=paint, expand=expand)
    links = bool(payload.get("_file_links"))
    if "activity" in selected_ids and not expand:
        frame.running, frame.running_total, frame.recent = _activity_sections(groups, width, unicode, keep,
                                                                               rich=rich, paint=paint, links=links)
        frame.leads, frame.leads_total = _leads_section(groups, now + elapsed, width, unicode, keep, rich=rich, paint=paint)
    elif "activity" in selected_ids and isinstance(as_dict(groups.get("activity")).get("data"), Mapping):
        data = as_dict(as_dict(groups.get("activity")).get("data"))
        ended = _recent_ended(data, groups)
        recent = ended[:RECENT_EXPAND if height is not None else TEXT_NEWEST_ROWS]
        if height is None:
            recent.extend({**row, "session": None} for row in _ask_split(data)[2])
        if recent:
            frame.recent = [heading_rule(_recent_head(data, ended, superseded=True), width, unicode, paint) if rich
                            else _recent_head(data, ended, superseded=True),
                            *_recent_expand_lines(recent, width, unicode, keep, rich=rich, paint=paint, links=links)]
            if height is not None:
                frame.recent_short = _recent_compact(recent, width, unicode, keep, rich=rich, paint=paint, links=links,
                                                    limit=RECENT_EXPAND)
    if "capacity" in selected_ids:
        suggestion = _compact_suggestions(configured_profiles, rows)
        frame.suggest = wrap_text(suggestion, width) if expand else _suggest_lines(suggestion, width, unicode)
        profile_urls = as_dict(payload.get("_profile_urls"))
        if profile_urls:
            frame.suggest = [link_profiles(line, profile_urls) for line in frame.suggest]
        show_notes = bool(payload.get("_show_quota_notes", SHOW_QUOTA_NOTES_DEFAULT))
        visible_rows = rows if show_notes else [row for row in rows if row["state"] != QUOTA_NA]
        if visible_rows:
            frame.pool_gap = pool_gap(rows, width, unicode, show_quota_notes=show_notes)
            frame.pool_legend = wrap_text(POOL_LEGEND, width)
            frame.pools, frame.pools_total, frame.pools_header_lines = _pool_section(
                visible_rows, pool_width or width, unicode, keep, rich=rich, paint=paint, every=expand,
                guide_url=payload.get("_state_doc_url"), profile_urls=profile_urls)
            if not expand and show_notes:
                dim = RICH_SGR["dim"] if paint else None
                frame.pool_notes = [
                    paint_text(line, dim) for row in rows if row["state"] == QUOTA_NA and (note := na_pool_note(row))
                    for line in wrap_text(note, max(1, pool_width or width))
                ]
        else:
            if rows:
                notes = [] if expand else wrap_text(_HIDDEN_QUOTA_WINDOWS, width)
            else:
                notes = (["no quota evidence", "next: peerhub observation refresh"] if height is not None
                         else wrap_text("no quota evidence; next: peerhub observation refresh", width))
            head = f"{FRAME_SECTIONS['pools']} 0"
            frame.pools = [heading_rule(head, width, unicode, paint) if rich else head, *notes]
    return frame


def _display_plan_omissions(payload: dict[str, Any]) -> dict[str, Any]:
    """Mark omitted quota rows on a copy of the monitor payload."""
    groups = as_dict(payload.get("groups"))
    cap = as_dict(groups.get("capacity"))
    data = as_dict(cap.get("data"))
    observations = dict_items(payload.get("_human_observations")) if "_human_observations" in payload else dict_items(data.get("observations"))
    plans = {str(item.get("peer_id")): clean_text(item["plan"]) for item in dict_items(data.get("accounts"))
             if item.get("state") == "MEASURED" and item.get("plan") and not item.get("probe_error")}
    elapsed = finite_number(payload.get("display_elapsed_seconds")) or 0
    replacements = omitted_plan_windows(observations, plans, elapsed)
    if not replacements:
        return payload

    def replace(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [replacements.get((str(item.get("peer_id")), str(item.get("pool_ref"))), item) for item in rows]

    return {**payload, "groups": {**groups, "capacity": {**cap, "data": {
        **data, "observations": replace(dict_items(data.get("observations")))}}},
        **({"_human_observations": replace(observations)} if "_human_observations" in payload else {})}


def render_grouped_frame(payload: dict[str, Any], *, view: str = "rich", color: bool = False,
                         unicode: bool = True, width: int = 100, height: int | None = None,
                         expand: bool = False, text_encoding: str | None = None,
                         show_quota_notes: bool = SHOW_QUOTA_NOTES_DEFAULT,
                         links: bool = False, tty: bool = False) -> str:
    """Compact human default; the established renderer supplies expanded detail.

    `text_encoding` is the output's encoding: without `unicode`, text cells keep what it can encode (A5).
    `links` indicates a VT terminal for the rich view. The link opt-out applies to both views.
    The plain Guide trailer uses a spare line when the header has no Guide link."""
    enabled = view == "rich" and links and monitor_links_enabled(os.environ)
    guide_url = guide_link_url(os.environ) if enabled else None
    store = as_dict(payload.get("store_selection"))
    payload = {**_with_retained_quota(_display_plan_omissions(payload)),
               "_show_quota_notes": show_quota_notes, "_guide_url": guide_url,
               "_state_doc_url": (guide_released(None) or GUIDE_MAIN_URL) if enabled else None,
               "_db_url": file_link_url(store.get("path")) if enabled else None,
               "_profile_urls": payload.get("_profile_urls") if enabled else None,
               "_file_links": enabled and os.environ.get("PEERHUB_MONITOR_TEXT", "").strip().lower() != "off"}
    if expand:
        screen = _render_expanded_frame(payload, view=view, color=color, unicode=unicode,
                                        width=width, height=height, expand=True, keep=text_encoding)
        columns = max(0, width - 1)
    else:
        screen = _render_compact_frame(payload, view=view, color=color, unicode=unicode,
                                       width=width, height=height, keep=text_encoding)
        columns = max(1, min(COMPACT_WIDTH, width) - 1)
    guide_rendered = guide_url is not None and osc_link(guide_url, GUIDE_TEXT) in screen
    if screen and not guide_rendered:
        guide = f"{GUIDE_TEXT}: {guide_text_url(os.environ)}"
        line_count = screen.count("\n")
        room = height is None or line_count < height - FRAME_TRAILING_ROWS
        if not expand:
            room = (room and line_count < COMPACT_MAX_LINES
                    and len((screen + guide).encode("utf-8")) < COMPACT_MAX_BYTES)
        if room and cell_width(guide) <= columns:
            return screen + guide + "\n"
    return screen


def _with_retained_quota(payload: dict[str, Any]) -> dict[str, Any]:
    """Show one recent successful reading after a newer error; keep the public snapshot intact."""
    groups = as_dict(payload.get("groups"))
    capacity = as_dict(groups.get("capacity"))
    data = as_dict(capacity.get("data"))
    observations = dict_items(payload.get("_human_observations", data.get("observations")))
    elapsed = finite_number(payload.get("display_elapsed_seconds")) or 0
    now = finite_number(payload.get("read_at")) or 0
    by_peer: dict[str, list[dict[str, Any]]] = {}
    for item in observations:
        if item.get("kind", "quota") == "quota" and finite_number(item.get("age_seconds")) is not None:
            by_peer.setdefault(str(item.get("peer_id") or "?"), []).append(item)

    notes: list[str] = []
    replacements: dict[int, dict[str, Any] | None] = {}
    for peer, items in sorted(by_peer.items()):
        latest = min(items, key=lambda item: (float(item["age_seconds"]), -float(item.get("_capture_seq", 0))))
        if latest.get("state") != "ERROR":
            continue
        candidates = [(item, item) for item in items if item.get("pool_ref") and not item.get("display_retired")
                      and item.get("stored_state", item.get("state")) == "MEASURED"]
        prior_age = finite_number(latest.get("_prior_age"))
        if latest.get("pool_ref") and prior_age is not None and (prior := as_dict(latest.get("_prior_measurement"))):
            candidates.append((latest, {**latest, "age_seconds": prior_age, "last_measurements": prior,
                                         "resets_at": prior.get("resets_at"), "projection_samples": 0,
                                         "observed_at": utc_text(now - prior_age, "%Y-%m-%dT%H:%M:%S.%fZ"),
                                         "reason": None}))
        ages: list[float] = []
        for original, item in candidates:
            age = finite_number(item.get("age_seconds"))
            raw = as_dict(item.get("last_measurements")) or as_dict(item.get("measurements"))
            remaining = finite_number(raw.get("remaining_fraction"))
            reset = finite_number(item.get("resets_at"))
            if (age is not None and 0 <= age + elapsed < QUOTA_MAX_AGE_SECONDS and remaining is not None and 0 <= remaining <= 1
                    and (reset is None or reset > now + elapsed)):
                ages.append(age + elapsed)
                replacements[id(original)] = {**item, "state": "MEASURED", "stored_state": "MEASURED",
                                               "measurements": raw, "_retained_quota": True,
                                               "reset_in_seconds": reset - now if reset is not None else item.get("reset_in_seconds")}
        if ages:
            if not latest.get("pool_ref"):
                replacements[id(latest)] = None
            failure = "timeout" if quota_probe_timed_out(latest.get("reason")) else "error"
            notes.append(f"{clean_text(peer)} probe {failure} (value {int(min(ages) // 60)}m old)")

    if not notes:
        return payload
    shown = [item for original in observations if (item := replacements.get(id(original), original)) is not None]
    by_id = {item["observation_id"]: replacements[id(item)] for item in observations
             if id(item) in replacements and "observation_id" in item}
    details = ([item for original in dict_items(data.get("observations"))
                if (item := by_id.get(original.get("observation_id"), original)) is not None]
               if "_human_observations" in payload else shown)
    return {**payload, "_quota_probe_notes": notes,
            **({"_human_observations": shown} if "_human_observations" in payload else {}),
            "groups": {**groups, "capacity": {**capacity, "data": {**data, "observations": details}}}}


def _render_expanded_frame(payload: dict[str, Any], *, view: str = "rich", color: bool = False,
                         unicode: bool = True, width: int = 100, height: int | None = None,
                         expand: bool = False, keep: str | None = None) -> str:
    """The --expand frame (`_expanded_frame`): the compact frame's top and the detail sections, rich or plain."""
    groups = as_dict(payload.get("groups"))
    selected_raw = payload.get("selected_groups")
    selected = ([str(g) for g in cast(Sequence[Any], selected_raw)]
                if isinstance(selected_raw, Sequence) and not isinstance(selected_raw, str) else list(groups))
    selected = [g for g in selected if g not in {"overview", "coverage"}]
    context_sessions = dict_items(as_dict(as_dict(groups.get("context")).get("data")).get("sessions"))
    if not any(as_dict(item.get("context")).get("state") in {"MEASURED", "ESTIMATED"} for item in context_sessions):
        selected = [g for g in selected if g != "context"]
    coverage_partial = any(as_dict(group).get("status") not in {"OK", None}
                           or _group_has_uncertain_measurement(as_dict(group).get("data"), depth=0)
                           or as_dict(as_dict(group).get("data")).get("truncated")
                           or as_dict(as_dict(group).get("coverage")).get("read") == "partial"
                           for group in groups.values())
    coverage_data = as_dict(as_dict(groups.get("coverage")).get("data"))
    scope_note = "No scope evidence" if "scopes" in coverage_data and not coverage_data["scopes"] else ""
    footer = "Coverage: " + (scope_note or ("partial" if coverage_partial else "complete")) + "; peerhub explain <code>"
    store = as_dict(payload.get("store_selection"))
    db_line = f"DB:{clean_text(store.get('source'))} ({clean_text(store.get('path'))})" if store.get("path") else ""
    cap = as_dict(groups.get("capacity"))
    data = as_dict(cap.get("data"))
    pool_rows = {(str(row["peer"]), str(row["pool_ref"])): row
                 for row in _frame_pools(payload, _frame_consumers(payload))[2]}
    show_notes = bool(payload.get("_show_quota_notes", SHOW_QUOTA_NOTES_DEFAULT))
    display_groups = {**groups,
        "capacity": {**cap, "data": {**data, "_show_quota_notes": show_notes,
                                      "_pool_rows": pool_rows}}}
    activity = as_dict(groups.get("activity"))
    execution = as_dict(groups.get("execution"))
    if isinstance(activity.get("data"), Mapping) and isinstance(execution.get("data"), Mapping):
        # #394: the leases decide what runs; the sampled certainty counts of EXECUTION do not
        running_asks = _running_head(as_dict(activity.get("data")), _ask_split(as_dict(activity.get("data")))[0], True)[1]
        display_groups["execution"] = {**execution, "data": {**as_dict(execution.get("data")), "_running_asks": running_asks}}
    # X5: the plan the provider reports is display text only, shown beside the peer's quota in the PEERS block.
    plan_windows: dict[str, tuple[str, list[str]]] = {}
    for obs in dict_items(data.get("observations")) if show_notes else []:
        reason = clean_text(obs.get("reason") or "")
        if obs.get("state") == "ABSENT" and reason.startswith(NOT_REPORTED_REASON):
            pool = clean_text(obs.get("pool_ref") or "").split(":")[-1]
            plan = na_plan(obs, reason)
            window = pool.split("-", 1)[-1].lower() if "-" in pool else pool.lower()
            plan_windows.setdefault(clean_text(obs.get("peer_id") or "?"), (plan, []))[1].append(window)
    peers_group = as_dict(groups.get("peers"))
    if isinstance(peers_group.get("data"), Mapping):
        display_groups["peers"] = {**peers_group, "data": {**as_dict(peers_group.get("data")),
                                                          "_show_quota_notes": show_notes}}
        if plan_windows:
            display_groups["peers"]["data"]["plan_notes"] = {
                peer: f"plan {plan}: no {', '.join(windows)} window" for peer, (plan, windows) in plan_windows.items()}
    display = {**payload, "selected_groups": selected, "groups": display_groups}
    # #338: the store path and the --db/PEERHUB_DB note wrap; a truncated path cannot name the store.
    store_lines = [*(location_lines(db_line, max(1, width - 1), payload.get("_db_url")) if db_line else []),
                   *(wrap_text(str(payload["_store_note"]), max(1, width - 1)) if payload.get("_store_note") else [])]
    report = as_dict(as_dict(as_dict(groups.get("peers")).get("data")).get("facts_report"))
    # Every facts_report reason is a drift reason ("ag: unknown_pool", "cc: version_drift").
    drift_peers = sorted({str(reason).split(":")[0] for reason in cast(list[object], report.get("reasons") or [])})
    if drift_peers:
        footer += "; DRIFT:" + ",".join(drift_peers)
    return _expanded_frame(payload, display, footer, store_lines, view=view, color=color, unicode=unicode, width=width,
                           height=height, expand=expand, keep=keep)


_ERROR_SOURCES = {  # the reads a group is made of: a group without an error of its own names the first that failed
    "overview": ("sessions", "execution"), "peers": ("sessions", "capacity"), "sessions": ("sessions", "execution"),
    "usage": ("sessions", "execution"), "context": ("sessions", "execution"), "runtime": ("sessions", "execution"),
    "execution": ("execution",), "coverage": ("execution",), "capacity": ("capacity",), "store": ("store",),
}


def _group_error(payload: dict[str, Any], group_id: str) -> str:
    """The row a failed group starts with: the class of its error only (`_safe_error_reason`)."""
    errors = as_dict(payload.get("errors"))
    error = errors.get(group_id)
    if error is None:
        error = next((errors[key] for key in _ERROR_SOURCES.get(group_id, (group_id,)) if key in errors), None)
    return _safe_error_reason(error)


def _is_head(line: str) -> bool:
    """A section head of the rich frame (a rule), colour or not."""
    return ANSI_ESCAPE.sub("", line).startswith(("── ", "-- "))


def _rich_section(label: str, rows: Sequence[str], width: int, unicode: bool, paint: bool) -> list[str]:
    """One section of the rich --expand frame (P23): its head as a rule, like a head of the compact frame, then its
    rows from the second cell. A head keeps the leading fields that fit on its line; the others are the section's first
    row, so a narrow terminal loses none. A row wider than the line wraps, with its next lines further in (colour
    never decides that: a painted row that has to wrap is wrapped as text)."""
    sep = f" {RICH_GLYPHS[unicode]['sep']} "
    fields = label.split(" | ")
    shown = len(fields)
    while shown > 1 and cell_width(" | ".join(fields[:shown])) > width - 3:
        shown -= 1
    lines = [heading_rule(" | ".join(fields[:shown]), width, unicode, paint)]
    for row in [*pack_fields(fields[shown:], max(1, width - 1), sep), *rows]:
        if cell_width(row) <= width - 1:
            lines.append(" " + row)
            continue
        text = ANSI_ESCAPE.sub("", row)
        indent = len(text) - len(text.lstrip(" "))
        parts = wrap_text(text, max(1, width - 3 - indent))
        lines.extend(" " * (indent + (1 if index == 0 else 3)) + part for index, part in enumerate(parts))
    return lines


def _rich_entry(mark: str, lines: Sequence[str]) -> list[str]:
    """One entry of RUNNING, LEADS or RECENT: its first line from the second cell (after its mark, if it has one),
    its other lines three cells in."""
    return [("" if index == 0 else "  ") + (f"{mark} " if mark and index == 0 else "") + line
            for index, line in enumerate(lines)]


def _alert_section(payload: dict[str, Any], width: int, unicode: bool, keep: str | None, *,
                   rich: bool, paint: bool, expand: bool) -> list[str] | None:
    alerts = frame_alerts(payload)
    unread = unread_sources(payload) if expand else []
    if not alerts and not unread:
        return None
    group = {"data": {"alerts": alerts, "unread": unread}}
    head = _group_label("alerts", group, "OK", None, 0)
    rows, _ = _group_lines("alerts", group, width=width, unicode=unicode, keep=keep, rich=rich)
    if rich and expand:
        return _rich_section(head, rows, width, unicode, paint)
    if rich:
        return [heading_rule(head, width, unicode, paint), *(" " + row for row in rows)]
    return [section_rule(head, width, unicode), *rows]


def _rich_activity(group: dict[str, Any], payload: dict[str, Any], now: float | None, width: int, unicode: bool,
                   paint: bool, keep: str | None) -> tuple[list[str], list[str]]:
    """(RUNNING, LEADS) of the rich --expand frame (P23): every running ask and every lead with every field, as the
    plain view lists them, under the compact frame's rules and with its markers: a RUNNING entry starts with the
    glyph of its flag (► ! × ?), and a lead's context has its meter. RECENT is not here: it follows POOLS in the top of
    the frame (`_compact_frame`, #412). What the activity sample left out ends LEADS."""
    glyphs = RICH_GLYPHS[unicode]
    sep = f" {glyphs['sep']} "
    if not isinstance(group.get("data"), Mapping):
        failed = clean_text(group.get("status") or "").upper() in {"ERROR", "FAILED"}
        why = _group_error(payload, "activity") if failed else "data unavailable"
        head = f"{FRAME_SECTIONS['running']} ? | activity {'read failed' if failed else 'not read'}"
        return _rich_section(head, [why], width, unicode, paint), []
    data = as_dict(group.get("data"))
    inner = max(1, width - 3)  # an entry's text starts in the fourth cell: the indent, the mark and a space

    def marked(glyph: str, colour: str, lines: list[str]) -> list[str]:
        return _rich_entry(paint_text(glyph, RICH_SGR[colour]) if paint else glyph, lines)

    running, _, _ = _ask_split(data)
    rows: list[str] = []
    for row in running:
        glyph, word, colour = _RUN_MARKS[_flag(row)]
        rows.extend(marked(glyphs.get(glyph) or word.strip()[:1], colour,
                           _ask_lines(row, inner, inner, unicode, keep, sep, links=bool(payload.get("_file_links")))))
    front = _rich_section(_running_head(data, running, unicode, keep)[0], rows, width, unicode, paint)
    leads = dict_items(data.get("leads"))
    rows = []
    for lead in leads:  # a lead has no mark: its label starts the entry
        rows.extend(_rich_entry("", _lead_rows(lead, now, max(1, width - 3), unicode, keep, sep=sep, meter=True,
                                               paint=paint)))
    rows.extend(_activity_notes(data, max(1, width - 1), sep))
    return front, _rich_section(_leads_header(data, leads), rows, width, unicode, paint)


def _plain_activity(group: dict[str, Any], payload: dict[str, Any], now: float | None, width: int, unicode: bool,
                    keep: str | None) -> tuple[list[str], list[str]]:
    """(RUNNING, LEADS) of the plain --expand frame: the heads are the compact frame's words, every running ask and every
    lead has every field (`- ` starts an ask). RECENT is in the top (`_compact_frame`)."""
    if not isinstance(group.get("data"), Mapping):
        failed = clean_text(group.get("status") or "").upper() in {"ERROR", "FAILED"}
        why = _group_error(payload, "activity") if failed else "data unavailable"
        return [f"{FRAME_SECTIONS['running']} ? | activity {'read failed' if failed else 'not read'}", why], []
    data = as_dict(group.get("data"))
    running = _ask_split(data)[0]
    rows = [_running_head(data, running, unicode, keep)[0]]
    for row in running:
        rows.extend(_ask_rows(row, width, unicode, keep))
    leads = dict_items(data.get("leads"))
    lead_rows = [_leads_header(data, leads)]
    for lead in leads:
        lead_rows.extend(_lead_rows(lead, now, width, unicode, keep))
    lead_rows.extend(_activity_notes(data, width))
    return rows, lead_rows


_PROFILE_DETAIL_GROUPS = {"peers", "execution", "sessions", "usage", "runtime"}


def _expanded_frame(payload: dict[str, Any], display: dict[str, Any], footer: str, store_lines: list[str], *,
                    view: str, color: bool, unicode: bool, width: int, height: int | None, expand: bool,
                    keep: str | None) -> str:
    """Build the header and priority sections first, then fit the expanded details."""
    rich = view == "rich"
    target = max(0, width - 1)
    if target <= 0 or (height is not None and height <= FRAME_TRAILING_ROWS):
        return ""
    paint = color and rich and "NO_COLOR" not in os.environ
    sep = f" {RICH_GLYPHS[unicode]['sep']} " if rich else " | "
    frame = _compact_frame(payload, view=view, color=color, unicode=unicode, width=target, height=height, keep=keep,
                           expand=True)
    frame.header[1:1] = store_lines

    lines: list[str] = []
    groups = as_dict(display.get("groups"))
    selected = cast(list[str], display.get("selected_groups") or [])
    read_at = finite_number(payload.get("read_at"))
    elapsed = finite_number(payload.get("display_elapsed_seconds")) or 0.0
    now = None if read_at is None else read_at + elapsed
    if "activity" in selected:
        if rich:
            frame.running, frame.leads = _rich_activity(as_dict(groups.get("activity")), payload, now, target, unicode, paint, keep)
        else:
            frame.running, frame.leads = _plain_activity(as_dict(groups.get("activity")), payload, now, target, unicode, keep)
    frame.pools_total = 0  # the expanded frame's final hidden-row hint counts the trim
    ladder = _frame_line_ladder(frame, target, unicode, keep)
    top = next(ladder)
    detail_groups = [gid for gid in _FRAME_DETAIL_ORDER if gid in selected]
    detail_groups.extend(gid for gid in selected if gid != "activity" and gid not in _FRAME_DETAIL_ORDER)
    profile_urls = as_dict(payload.get("_profile_urls"))
    for group_id in detail_groups:
        group = as_dict(groups.get(group_id))
        status = clean_text(group.get("status") or "UNKNOWN").upper()
        if group_id == "capacity":  # a group that was not read has no data: it says so, as every other group does
            read = as_dict(as_dict(payload.get("groups")).get("capacity"))
            group = read if read.get("data") is None else group
        group_width = max(1, target - 1) if rich and group_id != "capacity" else target
        rows, _ = _group_lines(group_id, group, width=group_width, unicode=unicode,
                                elapsed_seconds=elapsed, read_at=read_at, expand=expand, color=paint, keep=keep, rich=rich,
                                bounded=height is not None and not rich, guide_url=payload.get("_state_doc_url"),
                                profile_urls=profile_urls)
        if status in {"ERROR", "FAILED"}:
            rows.insert(0, _group_error(payload, group_id))
        label = _group_label(group_id, group, status, read_at, elapsed)
        if rich:
            section = _rich_section(label, rows, target, unicode, paint)
        else:
            # The append-only frame cuts a long row; a viewport drops whole trailing fields and says so.
            fit_row = fit_text if height is None else fit_fields
            section = [section_rule(label, target, unicode),
                       *(fit_row(row, target, unicode, indent=group_id == "capacity") for row in rows)]
        if profile_urls and group_id in _PROFILE_DETAIL_GROUPS:
            section = [link_profiles(line, profile_urls) for line in section]
        lines.extend(section)

    def fit(line: str) -> str:  # the one exit of every line: the glyph set of the stream, and never past the width
        if not unicode:  # A5: text the output encoding can show stays; the decoration is ASCII
            line = ascii_text(line.replace(" · ", " | ").replace("·", "|"), keep)
        return fit_text(line, target, unicode, painted=True, indent=True)

    rest = [fit(line) for line in lines]  # the top was fitted by its owner
    footer = fit(footer)
    if height is None:
        explanations = [text.replace(" | ", sep) for text in MONITOR_LEGEND] if rich else MONITOR_LEGEND
        legend = [fit(part) for text in explanations for part in wrap_text(text, max(1, target))]
        return "\n".join([*top, *rest, *legend, footer]) + "\n"
    limit = height - FRAME_TRAILING_ROWS

    every = [*top, *rest]
    total = len(every)
    if total + 1 <= limit:
        return "\n".join([*every, footer]) + "\n"

    def hint(kept: int, *, single_line: bool = False) -> list[str]:
        return [fit(line) for line in hidden_lines(total - kept, 0, target, unicode, expand=True,
                                                    single_line=single_line)]

    for tail in ([footer], []):  # coverage gives way before the top does
        for kept in range(min(total, limit - len(tail) - 1), len(top) - 1, -1):
            if kept > len(top) and _is_head(every[kept - 1]):
                continue  # never keep a detail heading without a row
            hidden = hint(kept)
            if kept + len(hidden) + len(tail) <= limit:
                return "\n".join([*every[:kept], *hidden, *tail]) + "\n"

    # Details have given way. Keep the verdict, title and final pool row with its gap ahead of the hint.
    protected = 2 + (frame.pools_header_lines + 1 + len(frame.pool_gap) if frame.pools else 0)
    if len(top) <= limit <= protected:
        return "\n".join(top) + "\n"
    for head in ladder:
        kept = len(head)
        if kept == limit and limit <= protected:
            return "\n".join(head) + "\n"
        if kept < limit:
            hidden = hint(kept, single_line=limit - kept == 1)
            tail = [footer] if kept + len(hidden) < limit else []
            return "\n".join([*head, *hidden, *tail]) + "\n"
    return top[0] + "\n"
