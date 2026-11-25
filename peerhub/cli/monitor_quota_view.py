"""Prepare monitor quota facts and render pools, accounts, and suggestions."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, cast

from peerhub.core.text import clean_text
from peerhub.core.scalars import finite_converted_number as finite_number
from peerhub.cli.monitor_format import (
    PoolEntry,
    display_min_quota,
    fold_pool_markers,
    na_pool_note,
    na_reason,
    ordered_pools,
    pool_order,
    pool_table,
    quota_layout,
)
from peerhub.cli.monitor_layout import (
    RICH_SGR,
    RULE_MIN_TAIL_WIDTH,
    RULE_PREFIX_WIDTH,
    as_dict,
    cell_text,
    cell_width,
    compact_age,
    dict_items,
    format_count,
    heading_rule,
    omitted_rows,
    pack_fields,
    paint_text,
    peer_label,
    utc_text,
    wrap_text,
)
from peerhub.cli.monitor_legend import FRAME_SECTIONS, QUOTA_NA
from peerhub.cli.monitor_preferences import SHOW_QUOTA_NOTES_DEFAULT
from peerhub.cli.quota_format import format_duration, format_percent
from peerhub.core.peer_kinds import ALIASES
from peerhub.extensions.monitor_alerts import pool_out_alerts
from peerhub.extensions.monitor_quota import peer_quota
from peerhub.extensions.observation_model import (
    ABSENT_FACT_REASONS,
    QUOTA_MAX_AGE_SECONDS,
    QUOTA_PROBE_LOAD_DELAY,
    QUOTA_PROBE_LOAD_LABEL,
    quota_probe_timed_out,
)
from peerhub.extensions.pool_health import PoolHealth
from peerhub.extensions.quota_read_model import PoolReading, PoolRow, monitor_reading, project_pool_rows
from peerhub.extensions.quota_windows import EXPIRED_WINDOW_REASON, FAMILIES, omitted_plan_windows, pool_family, quota_window_remaining


HIDDEN_QUOTA_WINDOWS = "Unavailable quota windows hidden (use --show-quota-notes)"
POOLS_CAP = 8
POOLS_USE_FIRST_HINT = "use first"


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


def compact_suggestions(profiles: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
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
            if (row is None or row["aged"] or row["state"] in {"?", "reset", "aged"}
                    or row.get("reason") == EXPIRED_WINDOW_REASON):
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


def _pools_heading(title: str, head: str, width: int, unicode: bool, rich: bool, paint: bool) -> str:
    hint = f"  {POOLS_USE_FIRST_HINT}"
    if rich and head.startswith(title + " " * RULE_PREFIX_WIDTH):
        # The rule adds three leading cells. Spend the title's padding to keep the columns aligned.
        head = title + head[len(title) + RULE_PREFIX_WIDTH:]
    room = width - (RULE_PREFIX_WIDTH + RULE_MIN_TAIL_WIDTH if rich else 0)
    if cell_width(head + hint) <= room:
        head += hint
    return heading_rule(head, width, unicode, paint) if rich else head


def pool_section(rows: list[dict[str, Any]], width: int, unicode: bool, keep: str | None = None, *,
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


def frame_consumers(payload: dict[str, Any]) -> dict[str, list[str]]:
    consumers: dict[str, list[str]] = {}
    for profile in dict_items(payload.get("configured_profiles")):
        name = clean_text(profile.get("profile") or "?")
        peer = name.partition(".")[0]
        for raw_pool in cast(list[object], as_dict(as_dict(profile.get("fields")).get("pools")).get("value") or []):
            consumers.setdefault(f"{peer}:{clean_text(raw_pool)}", []).append(name)
    return consumers


def _pool_source(item: dict[str, Any], now: float, elapsed: float, consumers: dict[str, list[str]]) -> PoolReading:
    peer = peer_label(item)
    ref = clean_text(item.get("pool_ref") or item.get("pool_kind") or "?")
    pool = ref.split(":")[-1]
    return PoolReading(peer=peer, pool=pool, pool_ref=ref, reading=monitor_reading(item, now, elapsed),
                       profiles=tuple(sorted(set(consumers.get(f"{peer}:{pool}", [])))),
                       retired=bool(item.get("display_retired")))


def _compact_pool(row: PoolRow) -> dict[str, Any]:
    source, facts = row.source, row.health
    reading = source["reading"]
    age = reading["age_seconds"]
    state, left, stale = facts["state"], facts["left_pct"], facts["stale"]
    family = pool_family(source["pool"])
    return {"peer": source["peer"], "pool": source["pool"], "pool_ref": source["pool_ref"],
            "reading": reading, "health": facts, "family": family, "meaning": FAMILIES.get(family, "?"),
            "remaining": None if left is None else left / 100,
            "pace_pt": facts["pace_pt"], "reset_seconds": facts["reset_in_seconds"],
            "state": state, "age_seconds": age, "aged": stale,
            "needs_refresh": state != QUOTA_NA and (stale or age is None or state in {"?", "reset"}),
            "reason": clean_text(reading.get("reason") or ""), "display_plan": reading.get("display_plan"),
            "retired": source["retired"], "profiles": source["profiles"],
            **({"depends_on": row.parent["pool"]} if row.parent is not None else {})}


def frame_pools(payload: dict[str, Any], consumers: dict[str, list[str]]
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(the frame's observations, its quota pools, their rows): each pool as it is at the time of this redraw (the read
    time plus the display's elapsed time). The human observations are used when the payload has them."""
    read_at = finite_number(payload.get("read_at"))
    elapsed = finite_number(payload.get("display_elapsed_seconds")) or 0
    observations = (dict_items(payload.get("_human_observations")) if "_human_observations" in payload else
                    dict_items(as_dict(as_dict(as_dict(payload.get("groups")).get("capacity")).get("data")).get("observations")))
    quota = [item for item in observations if item.get("kind", "quota") == "quota" and item.get("pool_ref")]
    now = read_at if read_at is not None else 0
    readings = [_pool_source(item, now, elapsed, consumers) for item in quota]
    rows = [_compact_pool(row) for row in project_pool_rows(readings, now + elapsed, exclude_unavailable=True)]
    rows.sort(key=lambda row: pool_order(str(row["peer"]), str(row["pool"])))
    return observations, quota, rows


def with_quota_verdict(payload: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Use the current POOLS facts for the human minimum and fresh exhaustion alerts."""
    if "_human_observations" not in payload:
        return payload
    alerts = [alert for alert in dict_items(payload.get("alerts")) if alert.get("kind") != "pool_out"]
    out = pool_out_alerts(f"{row['peer']} {row['pool']}" for row in rows if row["state"] == "out")
    at = next((i for i, alert in enumerate(alerts) if alert.get("severity") != "high"), len(alerts))
    return {**payload, "min_quota": display_min_quota(rows), "alerts": [*alerts[:at], *out, *alerts[at:]]}


def selected_quota_readings(readings: list[dict[str, Any]],
                            selected: Mapping[str, set[str]]) -> list[dict[str, Any]]:
    """Scope the live title to the peers and pools selected for watch."""
    by_peer: dict[str, list[dict[str, Any]]] = {}
    for item in readings:
        by_peer.setdefault(str(item.get("peer_id")), []).append(item)
    return [item for peer, pools in selected.items()
            for item in by_peer.get(peer, by_peer.get(ALIASES.get(peer, peer), []))
            if not pools or item.get("pool_ref") in pools]


def display_peer_quotas(payload: dict[str, Any], peers: list[dict[str, Any]],
                        observations: list[dict[str, Any]],
                        pool_rows: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize each peer from the quota windows still usable at this redraw."""
    if "_human_observations" not in payload:
        return peers
    by_peer: dict[str, list[dict[str, Any]]] = {}
    for item in observations:
        if item.get("kind", "quota") != "quota":
            continue
        peer = str(item.get("peer_id"))
        row = pool_rows.get((peer_label(item), clean_text(item.get("pool_ref") or "")))
        if item.get("reason") == EXPIRED_WINDOW_REASON or (row and row["state"] == "reset"):
            continue
        if row and row["aged"] and item.get("state") == "MEASURED":
            item = {**item, "state": "STALE"}
        by_peer.setdefault(peer, []).append(item)
    mapped_pools = as_dict(payload.get("_human_peer_pools"))
    return [{**peer, "quota": peer_quota(by_peer.get(str(peer["peer_id"]), []), mapped_pools.get(str(peer["peer_id"])),
                                        has_session=peer.get("session") is not None)} for peer in peers]


def display_retired_windows(payload: dict[str, Any]) -> dict[str, Any]:
    """Mark omitted or expired quota windows on a copy of the monitor payload."""
    groups = as_dict(payload.get("groups"))
    cap = as_dict(groups.get("capacity"))
    data = as_dict(cap.get("data"))
    age_only = "_quota_age_readings" in payload
    observations = dict_items(payload.get("_quota_age_readings") if age_only else
                              payload.get("_human_observations", data.get("observations")))
    accounts = dict_items(payload.get("_human_quota_accounts", data.get("accounts")))
    plans = {str(item.get("peer_id")): clean_text(item["plan"]) for item in accounts
             if item.get("state") == "MEASURED" and item.get("plan") and not item.get("probe_error")}
    elapsed = finite_number(payload.get("display_elapsed_seconds")) or 0
    replacements = omitted_plan_windows(observations, plans, elapsed)
    read_at = finite_number(payload.get("read_at"))
    if read_at is not None:
        for item in observations:
            remaining = quota_window_remaining(item, read_at)
            if remaining is not None and remaining < elapsed:
                key = (str(item.get("peer_id")), str(item["pool_ref"]))
                replacements.setdefault(key, {**item, "state": "ABSENT", "stored_state": "ABSENT",
                    "reason": EXPIRED_WINDOW_REASON, "display_retired": True, "age_seconds": None, "ttl_seconds": None,
                    "measurements": None, "last_measurements": None, "projection": {}, "resets_at": None, "reset_in_seconds": None})
    if not replacements:
        return payload

    def replace(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [replacements.get((str(item.get("peer_id")), str(item.get("pool_ref"))), item) for item in rows]

    if age_only:
        return {**payload, "_quota_age_readings": replace(observations)}
    display = dict(payload)
    if "capacity" in groups:
        display["groups"] = {**groups, "capacity": {**cap, "data": {
            **data, "observations": replace(dict_items(data.get("observations")))}}}
    if "_human_observations" in payload:
        display["_human_observations"] = replace(observations)
    return display


def with_retained_quota(payload: dict[str, Any]) -> dict[str, Any]:
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
                                         "observed_at": latest.get("_prior_observed_at"),
                                         "reason": None}))
        ages: list[float] = []
        for original, item in candidates:
            age = finite_number(item.get("age_seconds"))
            raw = as_dict(item.get("last_measurements")) or as_dict(item.get("measurements"))
            fraction = finite_number(raw.get("remaining_fraction"))
            retained = {**item, "state": "MEASURED", "stored_state": "MEASURED",
                        "measurements": raw, "_retained_quota": True}
            remaining = quota_window_remaining(retained, now)
            if (age is not None and 0 <= age + elapsed < QUOTA_MAX_AGE_SECONDS and fraction is not None and 0 <= fraction <= 1
                    and (remaining is None or remaining > elapsed)):
                ages.append(age + elapsed)
                replacements[id(original)] = retained
        if ages:
            if not latest.get("pool_ref"):
                replacements[id(latest)] = None
            reason = latest.get("reason")
            failure = (QUOTA_PROBE_LOAD_LABEL if reason == QUOTA_PROBE_LOAD_DELAY
                       else "timeout" if quota_probe_timed_out(reason) else "error")
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


def quota_label(status: str, group: dict[str, Any]) -> str:
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
        candidates.append((exhaust or 0, f"{row['peer']} {row['pool']} {format_percent(left / 100)} {when}"))
    return f"{label} | soonest exhaust: {min(candidates)[1]}" if candidates else label


def quota_rows(data: dict[str, Any], *, width: int, unicode: bool, elapsed_seconds: float, read_at: float | None,
               color: bool, keep: str | None, rich: bool, bar: str, guide_url: str | None,
               profile_urls: Mapping[str, Any] | None) -> tuple[list[str], int]:
    """Return QUOTA rows and the length of their table prefix."""
    rows: list[str] = []
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
                code = (f"probe {QUOTA_PROBE_LOAD_LABEL}" if item.get("reason") == QUOTA_PROBE_LOAD_DELAY
                        else f"PROBE:{compact_age(age + elapsed_seconds) if age is not None else 'unknown'}")
                probe_notes.setdefault(peer, set()).add(code)
                continue
            row = pool_rows.get((peer, clean_text(pool_ref)))
            if row is None:
                now = read_at or 0
                source = _pool_source(item, now, elapsed_seconds, {})
                row = _compact_pool(project_pool_rows([source], now + elapsed_seconds)[0])
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
        visible = [row for row in available if row["state"] != QUOTA_NA]
        position = {(str(row["peer"]), str(row["pool"])): index
                    for index, (row, _) in enumerate(ordered_pools(visible, every=True, for_quota=True))}
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
            rows.append(HIDDEN_QUOTA_WINDOWS)
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
    return rows, table_size
