"""Render the monitor's grouped detail rows and labels."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any, cast

from peerhub.core.text import clean_text
from peerhub.core.scalars import finite_converted_number as finite_number
from peerhub.cli.monitor_alert_view import alert_label, alert_rows
from peerhub.cli.monitor_layout import (
    as_dict,
    compact_age,
    dict_items,
    fit_text,
    format_count,
    format_size,
    omitted_rows,
    peer_label,
    visible_fields,
    wrap_text,
)
from peerhub.cli.monitor_legend import CONTEXT_ESTIMATE_LABEL, FRAME_DETAIL_ORDER, FRAME_SECTIONS
from peerhub.cli.monitor_quota_view import quota_label, quota_rows
from peerhub.cli.quota_format import format_percent, fraction_bar
from peerhub.core.monitor_groups import GROUPS
from peerhub.extensions.monitor_sessions import context_estimate_age


def _sample_note(gid: str, data: dict[str, Any]) -> str:
    """`sample N/M` for a section that read a capped sample (M = the total, when the read model counted it)."""
    if gid not in {"execution", "sessions", "usage", "context"} or not data.get("truncated"):
        return ""
    read = finite_number(data.get("sample_size"))
    if read is None:
        read = next((len(cast(list[object], value)) for value in data.values() if isinstance(value, list)), None)
    total = finite_number(data.get("sample_total"))
    return f"sample {format_count(read)}" + (f"/{format_count(total)}" if total is not None and total >= (read or 0) else "")


def group_label(gid: str, group: dict[str, Any], status: str, read_at: float | None, elapsed: float) -> str:
    if gid == "capacity":
        return quota_label(status, group)
    if gid == "alerts":
        return alert_label(as_dict(group.get("data")))
    head = FRAME_SECTIONS[gid] if gid in FRAME_DETAIL_ORDER else gid.upper()
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


def billing_summary(peer: dict[str, Any]) -> str | None:
    billing = as_dict(peer.get("billing"))
    if not billing:
        return None
    facts = dict_items(billing.get("observations"))
    usage = next((fact for fact in facts if fact.get("kind") == "billing_usage"), None)
    if usage is not None and usage.get("status") == "unreconciled":
        return "billing unreconciled"
    if billing.get("status") == "ERROR":
        return "billing read error"
    return "billing evidence" if facts else None


def group_lines(group_id: str, group: dict[str, Any], *, width: int, unicode: bool,
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
                    q_wide = f"{format_percent(q_rem)}{pool_str}{age_str}" + (f" [{plan_note}]" if plan_note else "")
                    q_narr = f"{format_percent(q_rem)}{pool_str}"

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

            if (summary := billing_summary(item)) is not None:
                wide_quota = narrow_quota = summary
            else:
                wide_quota, narrow_quota = f"quota: {q_wide}", f"quota {q_narr}"
            if width < 48:
                narrow_peers.append([f"{pid}: {narrow_quota}", p_narr, s_narr, f"ctx: {c_narr}" if c_narr != "unk" else ""])
            else:
                rows.append(f"{pid:<4}{wide_quota} -> {p_wide}")
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
        rows, table_size = quota_rows(data, width=width, unicode=unicode, elapsed_seconds=elapsed_seconds,
                                      read_at=read_at, color=color, keep=keep, rich=rich, bar=bar,
                                      guide_url=guide_url, profile_urls=profile_urls)
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
        rows.extend(alert_rows(data, unicode, keep, bar))

    else:
        # Keep future fields visible without exposing a JSON-shaped dump.
        rows.extend(_fallback_rows(data))

    return [row for row in rows if not row.startswith("Coverage:")] or ["No data"], table_size


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
