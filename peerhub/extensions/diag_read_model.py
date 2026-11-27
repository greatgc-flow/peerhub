"""Dashboard and quota-plan projections of committed diagnostic snapshots."""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from peerhub.core.text import strip_controls
from peerhub.core.quota_policy import PLAN_REST_PACE_PT, PLAN_SPEND_PACE_PT, QUOTA_TTL_SECONDS, format_pace
from peerhub.extensions.billing_port import billing_consumer
from peerhub.extensions.observation_model import BILLING_KINDS
from peerhub.extensions.quota_projection import ProjectionState, observation_projection
from peerhub.extensions.quota_read_model import PoolReading, PoolRow, project_pool_rows
from peerhub.extensions.quota_windows import (
    FAMILIES,
    WEEK_SECONDS,
    omitted_plan_windows,
    parse_window,
    pool_family,
)

if TYPE_CHECKING:
    from peerhub.extensions.diag import DiagnosticReport, Section
    from peerhub.extensions.pool_health import PaceGaps, PoolHealth

Obj = Mapping[str, object]


def as_object(value: object) -> Obj:
    return cast(Obj, value) if isinstance(value, Mapping) else {}


def object_items(value: object) -> list[Obj]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    seq = cast("Sequence[object]", value)
    return [cast(Obj, v) for v in seq if isinstance(v, Mapping)]


def report_number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def section_data(rep: DiagnosticReport, name: str) -> Obj:
    sec = rep.sections.get(name)
    return as_object(sec.data) if sec is not None else {}


class QuotaRow:
    def __init__(self, item: Obj) -> None:
        self.item = item
        payload = as_object(item.get("payload"))
        subject = item.get("subject_ref")
        self.subject_ref = subject if isinstance(subject, str) else None
        self.peer = strip_controls(subject or "?")
        self.ref = strip_controls(item.get("resource_pool_ref") or "?")
        self.pool = self.ref.rsplit(":", 1)[-1]
        self.state = strip_controls(item.get("state") or "UNKNOWN")
        self.age = report_number(item.get("age_seconds"))
        self.ttl = report_number(item.get("ttl_seconds"))
        self.reason = strip_controls(payload.get("reason") or "")
        rf = report_number(payload.get("remaining_fraction"))
        self.left_fraction = rf if rf is not None and 0 <= rf <= 1 and self.state in ("MEASURED", "STALE") else None
        self.used = 1 - rf if self.left_fraction is not None and rf is not None else None
        self.na = False
        self.limited_by: str | None = None
        self.forecast: ProjectionState = "unknown"
        self.exhaustion_at: float | None = None
        self.diff: float | None = None
        self.left: float | None = None
        start, reset = report_number(payload.get("window_started_at")), report_number(payload.get("resets_at"))
        self.span = reset - start if reset is not None and start is not None and reset > start else float("inf")
        # A declared suffix supplies a display/sort window for a registered
        # unmeasured pool, never an anchor for projection arithmetic.
        suffix = re.search(r"(\d+)([HD])$", self.pool.upper())
        if self.span == float("inf") and suffix and int(suffix[1]) > 0:
            self.span = float(int(suffix[1]) * (3600 if suffix[2] == "H" else 86400))


def _select_quota_rows(items: list[Obj], pools: list[Obj]) -> list[QuotaRow]:
    # A probe without a pool belongs to F. Collapse shared pool evidence to one row.
    selected: dict[str, Obj] = {}
    for item in items:
        ref = item.get("resource_pool_ref")
        if item.get("kind") in ("quota", "rate_limit") and ref:
            key = str(ref)
            old = selected.get(key)
            if old is None or str(item.get("observed_at") or "") > str(old.get("observed_at") or ""):
                selected[key] = item
    for pool in pools:
        ref = pool.get("resource_pool_id")
        if ref and pool.get("kind") in ("QUOTA", "RATE_LIMIT"):
            selected.setdefault(str(ref), {"subject_ref": pool.get("provider"), "resource_pool_ref": ref, "state": "UNKNOWN", "payload": {}})
    return sorted((QuotaRow(i) for i in selected.values()), key=lambda r: (r.peer, _family(r.pool), r.span, r.ref))


def _quota_consumers(rows: list[QuotaRow], profiles_by_pool: Mapping[str, list[str]] | None) -> dict[str, list[str]]:
    return {r.ref: [profile for profile in (profiles_by_pool or {}).get(r.pool, [])
                    if profile.partition(".")[0] == r.subject_ref] for r in rows}


def _project_quota_rows(rows: list[QuotaRow], read_at: float, consumers: Mapping[str, list[str]], *,
                        require_consumers: bool) -> list[PoolRow]:
    readings = [PoolReading(peer=r.peer, pool=r.pool, pool_ref=r.ref, reading=r.item,
                            profiles=tuple(consumers[r.ref]), retired=bool(r.item.get("display_retired")))
                for r in rows]
    return project_pool_rows(readings, read_at, require_consumers=require_consumers)


def quota_rows(items: list[Obj], pools: list[Obj], read_at: float, *,
               profiles_by_pool: Mapping[str, list[str]] | None = None) -> list[QuotaRow]:
    rows = _select_quota_rows(items, pools)
    projected = _project_quota_rows(rows, read_at, _quota_consumers(rows, profiles_by_pool),
                                    require_consumers=profiles_by_pool is not None)
    for row, pool in zip(rows, projected):
        health = pool.health
        row.na = health["state"] == "n/a"
        row.left = health["reset_in_seconds"]
        row.diff = health["pace_pt"]
        row.forecast = "expired" if health["state"] == "reset" else health["forecast"]
        exhaust = health["exhaust_in_seconds"]
        row.exhaustion_at = None if exhaust is None else read_at + exhaust
        if health["limited_by"] is not None:
            row.limited_by = strip_controls(health["limited_by"]).rsplit(":", 1)[-1]
            left_pct = health["left_pct"]
            row.left_fraction = None if left_pct is None else left_pct / 100
    return rows


def dashboard_quota_rows(report: DiagnosticReport, *,
                         profiles_by_pool: Mapping[str, list[str]] | None = None) -> list[QuotaRow]:
    items = _plan_observations(report)
    items.extend(item for item in object_items(section_data(report, "observations").get("items"))
                 if item.get("kind") == "rate_limit")
    pools = object_items(section_data(report, "resource_pools").get("pools"))
    return quota_rows(items, pools, report.read_at, profiles_by_pool=profiles_by_pool)


def _family(pool: str) -> str:
    """A pool's family without its window suffix: G-5H and G-7D are both G."""
    return re.sub(r"-?\d+[HD]$", "", pool.upper())


def threat_summary(rows: list[QuotaRow]) -> dict[str, object]:
    fresh = sum(r.forecast in ("threat", "on_pace", "insufficient", "exhausted") for r in rows)
    stale = [r for r in rows if r.state == "STALE"]
    na = sum(1 for r in rows if r.na)
    threats = [r for r in rows if r.forecast == "threat"]
    exhausted = [r for r in rows if r.forecast == "exhausted"]
    oldest = max((r.age for r in stale if r.age is not None), default=None)
    state = "threat" if threats or exhausted else "clear" if fresh else "unknown"
    reason = None if fresh else "evidence is stale" if stale else "evidence is missing or invalid"
    return {"state": state, "reason": reason, "fresh_pools": fresh, "stale_pools": len(stale),
            "unknown_pools": max(0, len(rows) - fresh - len(stale) - na), "oldest_stale_seconds": oldest,
            "threatened_pools": [r.ref for r in threats], "exhausted_pools": [r.ref for r in exhausted]}


def _payload_fields(payload: Obj, keys: tuple[str, ...]) -> dict[str, object]:
    """Allow only scalar values; permitted keys cannot carry nested secrets."""
    return {k: payload[k] for k in keys if k in payload
            and (payload[k] is None or isinstance(payload[k], (str, int, float, bool)))}


def _scrub_payload(kind: object, payload: object) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        return {}
    p = cast(Mapping[str, object], payload)
    if isinstance(kind, str) and kind in BILLING_KINDS:
        billing = billing_consumer()
        return billing.billing_payload(kind, p) if billing is not None else {}
    if kind == "reset_credit":
        out = _payload_fields(p, ("available_count", "nearest_expires_at", "condition", "reason", "evidence_ref"))
        raw_credits = p.get("credits")
        if isinstance(raw_credits, Sequence) and not isinstance(raw_credits, (str, bytes)):
            creds: list[dict[str, object]] = []
            for c in object_items(cast(Sequence[object], raw_credits)):
                cred: dict[str, object] = {}
                id_val = c.get("id_ref")
                if id_val is not None:
                    cred["id_hash"] = hashlib.sha256(str(id_val).encode()).hexdigest()[:12]
                else:
                    id_hash = c.get("id_hash")
                    if isinstance(id_hash, str) and re.fullmatch(r"[0-9a-f]{12}", id_hash):
                        cred["id_hash"] = id_hash
                cred.update(_payload_fields(c, ("reset_type", "status", "granted_at", "expires_at", "title")))
                creds.append(cred)
            out["credits"] = creds
        return out
    if kind == "activity":
        return _payload_fields(p, ("operation_elapsed_seconds", "status", "certainty", "record_id", "reason", "condition"))
    if kind in ("quota", "rate_limit"):
        return _payload_fields(p, ("remaining_fraction", "window_started_at", "resets_at", "evidence_ref", "condition", "reason", "consumed_tokens", "num_turns", "error_type", "requests_per_minute", "tokens_per_minute", "window_seconds"))
    if kind in ("reachability", "cli_version", "runtime_capability", "session", "execution_failure"):
        return _payload_fields(p, ("condition", "reason", "evidence_ref", "ok", "version", "status", "error_type"))
    return {}


def _serialize_obs_item(item: object, read_at: float) -> dict[str, object]:
    if not isinstance(item, Mapping):
        return {}
    it = cast(Mapping[str, object], item)
    kind = it.get("kind")
    obs: dict[str, object] = {
        k: it[k]
        for k in (
            "observation_id",
            "subject_ref",
            "kind",
            "resource_pool_ref",
            "source",
            "stored_state",
            "state",
            "age_seconds",
            "ttl_seconds",
            "basis",
            "skew_seconds",
            "capture_seq",
            "observed_at",
            "captured_at",
        )
        if k in it
    }
    obs["payload"] = _scrub_payload(kind, it.get("payload"))
    if kind in ("quota", "rate_limit"):
        basis = {**obs, "reset_in_seconds": it["reset_in_seconds"]} if "reset_in_seconds" in it else obs
        obs["projection"] = observation_projection(basis, read_at)  # relative resets count, as in the summary
    return obs


def _serialize_section(name: str, sec: Section, read_at: float, *, include_billing: bool) -> dict[str, object]:
    errors: list[dict[str, object]] = [
        _payload_fields(e, ("error_type", "observation_id", "capture_seq", "status", "condition"))
        for e in sec.errors
    ]
    # Raw exception messages and log lines can contain provider payloads.
    for error in errors:
        error["error"] = "diagnostic section unavailable" if sec.status == "UNAVAILABLE" else "diagnostic section error"
    raw_data: Obj = sec.data
    data: dict[str, object] = {}
    if name == "peers":
        keys = ("peer_id", "display_name", "adapter_ref", "created_at") + (("billing",) if include_billing else ())
        data["peers"] = [
            {k: p[k] for k in keys if k in p}
            for p in object_items(raw_data.get("peers"))
        ]
    elif name == "streams":
        data["streams"] = [
            {k: s[k] for k in ("stream_id", "title", "state", "revision", "head_position", "record_count", "members", "offsets") if k in s}
            for s in object_items(raw_data.get("streams"))
        ]
    elif name == "resource_pools":
        data["pools"] = [
            {k: p[k] for k in ("resource_pool_id", "provider", "kind", "observation_count") if k in p}
            for p in object_items(raw_data.get("pools"))
        ]
    elif name == "activity":
        if "window" in raw_data:
            data["window"] = raw_data["window"]
        data["peers"] = [
            _payload_fields(p, ("peer", "asks", "ok", "uncertain", "failed", "median_seconds", "last_status", "last_seconds", "last_age_seconds"))
            for p in object_items(raw_data.get("peers"))
        ]
    elif name == "log":
        data = {k: raw_data[k] for k in ("size_bytes",) if k in raw_data}
    elif name == "observations":
        data["items"] = [_serialize_obs_item(it, read_at) for it in object_items(raw_data.get("items"))
                         if include_billing or it.get("kind") not in BILLING_KINDS]
    return {"name": sec.name, "status": sec.status, "data": data, "errors": errors}


def dashboard_data(report: DiagnosticReport, *, include_billing: bool = True,
                   profiles_by_pool: Mapping[str, list[str]] | None = None) -> dict[str, object]:
    """Dashboard JSON uses the same evidence coverage and projections as text."""
    sections_dict = {
        name: _serialize_section(name, sec, report.read_at, include_billing=include_billing)
        for name, sec in report.sections.items()
    }
    data: dict[str, object] = {
        "schema_version": "1.0",
        "status": report.status,
        "read_at": report.read_at,
        "snapshot": _payload_fields(report.snapshot, ("records_total", "observations_total")),
        "sections": sections_dict,
        "error": "diagnostic snapshot failed" if report.error is not None else None,
    }
    rows = dashboard_quota_rows(report, profiles_by_pool=profiles_by_pool)
    data["threats"] = threat_summary(rows)
    data["forecast_basis"] = "conditional window-average (cond)"
    return data


PLAN_STALE_TAG = "(stale)"  # one spelling, shown first in the reason of a stale window
PLAN_PACE_LEGEND = f"spend PACE<=-{PLAN_SPEND_PACE_PT:.0f} | hold | rest PACE>+{PLAN_REST_PACE_PT:.0f}"
PLAN_LEGEND = PLAN_PACE_LEGEND + "/exhausts early | blocked 0%/unknown | - n/a | TARGET=left%/h"
PLAN_GATE_TAG = "gate"
_PLAN_ZERO_REASONS = {"out": "out", "aged": "stale zero; refresh"}
_PLAN_RESET_ADVICE = ("hold", "window reset; refresh")
_PLAN_RANK = {"spend": 0, "hold": 1, "rest": 2, "blocked": 3}
_PlanWindow = tuple[QuotaRow, float | None, str, str]
_PlanPool = tuple[str | None, dict[str, object], "PoolHealth"]


def plan_gate_reason(pool: object, reason: object) -> str:
    return f"{PLAN_GATE_TAG}: {strip_controls(pool)} {strip_controls(reason)}"


def plan_budget(left: float | None, reset: float | None) -> float | None:
    hours = reset / 3600 if reset is not None and reset > 0 else None
    return left / hours if left is not None and hours else None


def plan_action(left_pct: float | None, pace_pt: float | None, forecast: str) -> tuple[str, str]:
    """#339: one window's action from stored evidence and the existing PACE/forecast; never re-derived."""
    stale = PLAN_STALE_TAG + " " if forecast == "stale" else ""  # leads the reason: a narrow terminal cuts the end
    if forecast == "expired":
        return _PLAN_RESET_ADVICE
    if left_pct is None:
        return "blocked", "left unknown; refresh"
    if left_pct <= 0 or forecast == "exhausted":
        return "blocked", _PLAN_ZERO_REASONS["out"]
    if forecast == "threat":
        return "rest", stale + "forecast exhausts before reset"
    if pace_pt is None:
        return "hold", stale + "PACE unknown"
    from peerhub.extensions.pool_health import pace_band

    band = pace_band(pace_pt)
    if band == "rest":
        return "rest", stale + f"PACE {format_pace(pace_pt)} > {PLAN_REST_PACE_PT:+.0f}"
    if band == "spend":
        return "spend", stale + f"{-pace_pt:.0f}pp above even share"
    return "hold", stale + "on pace"


def _plan_scope(row: QuotaRow) -> tuple[str | None, str]:
    return row.subject_ref, row.ref.rpartition(":")[0]


def _plan_family(row: QuotaRow) -> tuple[str | None, str, str]:
    return (*_plan_scope(row), _family(row.pool))


def _plan_pool_facts(own: list[_PlanWindow], out: list[dict[str, object]],
                     projected: list[PoolRow]) -> None:
    families: dict[tuple[str | None, str, str], list[_PlanWindow]] = {}
    for entry in own:
        families.setdefault(_plan_family(entry[0]), []).append(entry)
    for (r, _, _, _), row, pool in zip(own, out, projected):
        siblings = families[_plan_family(r)]
        family = pool_family(r.pool)
        window = parse_window(r.pool)[0]
        parent = None
        if pool.parent is not None:
            parent = pool.parent["pool"]
        elif window < WEEK_SECONDS and family != "?":
            parent = next((s.pool for s, _, action, _ in siblings
                           if action != "n/a" and parse_window(s.pool)[0] == WEEK_SECONDS), None)
        row.update(age_seconds=r.age, ttl_seconds=r.ttl if r.ttl is not None else QUOTA_TTL_SECONDS,
                   family=FAMILIES.get(family, "?"), parent=parent)


def _plan_observations(report: DiagnosticReport) -> list[Obj]:
    from peerhub.extensions.observation_model import account_text

    items = object_items(section_data(report, "observations").get("items"))
    plans: dict[str, str] = {}
    quota: list[dict[str, Any]] = []
    for item in items:
        payload = as_object(item.get("payload"))
        if item.get("kind") == "quota":
            quota.append({**item, "peer_id": item.get("subject_ref"), "pool_ref": item.get("resource_pool_ref"),
                          "reason": payload.get("reason")})
        elif (item.get("kind") == "account" and item.get("state") == "MEASURED" and not item.get("plan_changed")
              and payload.get("plan_status") == "reported" and (plan := account_text(payload.get("plan")))):
            plans[str(item.get("subject_ref"))] = plan
    omitted = omitted_plan_windows(quota, plans)
    return [{**item, **retired, "payload": {"reason": retired["reason"]}}
            if (retired := omitted.get((str(item["peer_id"]), str(item["pool_ref"])))) is not None else item
            for item in quota]


def _plan_gate_windows(own: list[_PlanWindow], consumers: Mapping[str, list[str]],
                       exhausted: Mapping[str, _PlanWindow],
                       profiles_by_pool: Mapping[str, list[str]] | None) -> dict[str, list[_PlanWindow]]:
    accounts: dict[str, list[_PlanWindow]] = {}
    for entry in own:
        row = entry[0]
        gate = exhausted.get(row.ref, entry)
        if gate[2] != "n/a" and gate[2:] != _PLAN_RESET_ADVICE and gate[0].subject_ref is not None:
            accounts.setdefault(_plan_scope(row)[1], []).append(gate)
    gates: dict[str, list[_PlanWindow]] = {}
    for r, _, action, _ in own:
        if action == "n/a":
            continue
        reporter, account = _plan_scope(r)
        profiles = set(consumers[r.ref])
        gates[r.ref] = [
            entry for entry in accounts.get(account, []) if (other := entry[0]) is not r
            and (other.subject_ref == reporter or other.ref in exhausted)
            and (_family(r.pool) == _family(other.pool) if profiles_by_pool is None
                 else profiles.intersection(profiles_by_pool.get(other.pool, [])))
        ]
    return gates


def _plan_advice(window: _PlanWindow, parent: QuotaRow | None, facts: PoolHealth, own_health: PoolHealth,
                 windows: list[_PlanWindow], exhausted: Mapping[str, _PlanWindow], *,
                 use_family: bool) -> dict[str, object]:
    r, left, own_action, own_reason = window
    if own_action == "n/a":
        return {"action": own_action, "reason": own_reason, "binding": None, "gates": []}
    if (own_action, own_reason) == _PLAN_RESET_ADVICE:
        return {"action": own_action, "reason": own_reason, "binding": r.pool, "gates": []}

    # When the parent only changes the state, the effective pace is still the child's.
    # The parent's own reading votes through the gate list in that case.
    effective_parent_ref = facts["limited_by"] if facts["left_pct"] != left else None
    applicable = [entry for entry in windows if entry[0].ref in exhausted
                  or (entry[0].ref != effective_parent_ref and entry[2] != "blocked")]
    gate = max(applicable, key=lambda entry: (_PLAN_RANK[entry[2]], entry[0].span), default=None)
    parent_vote = None
    if parent is not None and effective_parent_ref and parent.ref not in exhausted:
        parent_vote = (parent, facts["left_pct"], *plan_action(facts["left_pct"], facts["pace_pt"], facts["forecast"]))

    # Equal actions prefer the effective parent, then the pool's own evidence.
    # In particular, another exhausted gate cannot replace a zero's refresh reason.
    decided = max((vote for vote in (parent_vote, window, gate) if vote is not None),
                  key=lambda vote: _PLAN_RANK[vote[2]])
    source, _, action, why = decided
    if source is r:
        reason = why.removeprefix(f"{PLAN_STALE_TAG} ")
    elif decided is parent_vote or (use_family and action != "blocked"):
        reason = f"{source.pool}: {why}"
    else:
        reason = plan_gate_reason(source.pool, why)
    if left != 0 and own_health["stale"] and r.used is not None:
        reason = f"{PLAN_STALE_TAG} {reason}"

    shown = [entry for entry in applicable if not use_family or entry[0].ref in exhausted]
    if parse_window(r.pool)[0] == WEEK_SECONDS:
        # Unknown windows remain weekly evidence. Their block applies only to their own row.
        shown.extend(entry for entry in windows if entry not in shown
                     and _plan_family(r) == _plan_family(entry[0]) and entry[2] in {"rest", "blocked"})
    if parent_vote is not None:
        shown.append(parent_vote)
    if decided is gate and decided not in shown:
        shown.append(decided)
    return {"action": action, "reason": reason, "binding": source.pool,
            "gates": [{"pool": other.pool, "action": action, "reason": why}
                      for other, _, action, why in shown]}


def plan_pools(report: DiagnosticReport, *,
                profiles_by_pool: Mapping[str, list[str]] | None = None) -> list[_PlanPool]:
    from peerhub.extensions.observation_model import DISABLED_REASON
    from peerhub.extensions.pool_health import pool_health

    items = _plan_observations(report)
    pools = [p for p in object_items(section_data(report, "resource_pools").get("pools")) if p.get("kind") == "QUOTA"]
    rows = _select_quota_rows(items, pools)
    consumers = _quota_consumers(rows, profiles_by_pool)
    projected = _project_quota_rows(rows, report.read_at, consumers, require_consumers=profiles_by_pool is not None)
    healths = {r.ref: row.own_health for r, row in zip(rows, projected)}
    rows_by_ref = {r.ref: r for r in rows}
    parents = {r.ref: rows_by_ref[row.parent["pool_ref"]] for r, row in zip(rows, projected) if row.parent is not None}
    use_family = profiles_by_pool is None  # library callers without profile metadata
    own: list[_PlanWindow] = []
    for r in rows:
        facts = healths[r.ref]
        left, state = facts["left_pct"], facts["state"]
        if state == "n/a":
            # The monitor's n/a: the plan omits the window or the provider disabled it; not a budget, never a gate.
            own.append((r, None, "n/a", "n/a (disabled by provider)" if r.reason.startswith(DISABLED_REASON) else "n/a (plan omits)"))
        elif state == "reset":
            own.append((r, left, *_PLAN_RESET_ADVICE))
        elif left == 0:
            own.append((r, left, "blocked", _PLAN_ZERO_REASONS[state]))
        else:
            own.append((r, left, *plan_action(left, facts["pace_pt"], facts["forecast"])))
    out: list[dict[str, object]] = []
    for r, left, _, _ in own:
        facts = healths[r.ref]
        reset = None if facts["state"] == "n/a" else facts["reset_in_seconds"]
        out.append({"peer": r.peer, "pool": r.pool, "resource_pool_ref": r.ref, "left_pct": left,
                    "resets_in_seconds": reset, "target_pct_per_hour": plan_budget(left, reset),
                    "pace_pt": facts["pace_pt"], "forecast": facts["forecast"]})
    # Display chooses one reading per pool. A fresh zero from any reporter can still gate that account.
    exhausted = {entry[0].ref: entry for entry in own if healths[entry[0].ref]["state"] == "out"}
    for item in items:
        ref = strip_controls(item.get("resource_pool_ref") or "")
        if ref and ref not in exhausted and as_object(item.get("payload")).get("remaining_fraction") == 0:
            facts = pool_health(item, None, report.read_at)
            if facts["state"] == "out":
                exhausted[ref] = (QuotaRow(item), facts["left_pct"], "blocked", _PLAN_ZERO_REASONS["out"])
    _plan_pool_facts(own, out, projected)
    gates = _plan_gate_windows(own, consumers, exhausted, profiles_by_pool)
    result: list[_PlanPool] = []
    for window, row, pool in zip(own, out, projected):
        r = window[0]
        own_health = healths[r.ref]
        parent = parents.get(r.ref)
        facts = own_health if parent is None else pool.health
        if parent is not None and (pool.parent is None or pool.parent["reading"] is not parent.item):
            # Stored refs that differ only in stripped controls can collide in the plan's display index.
            facts = pool_health(r.item, parent.item, report.read_at)
        row.update(_plan_advice(window, parent, facts, own_health, gates.get(r.ref, []), exhausted,
                                use_family=use_family))
        row.update(
            fast=own_health["state"] == "fast",
            even_left_pct=facts["even_left_pct"], exhaust_in_seconds=facts["exhaust_in_seconds"],
            exhaust_margin_seconds=facts["exhaust_margin_seconds"],
            exhaust_before_reset=facts["exhaust_before_reset"], stale=facts["stale"],
            limited_by=facts["limited_by"], own_left_pct=facts["own_left_pct"],
            health={"state": facts["state"], "pace_band": facts["pace_band"], "reserve": facts["reserve"],
                    "effective_pace_pt": facts["pace_pt"], "effective_reset_in_seconds": facts["reset_in_seconds"]},
        )
        if profiles_by_pool is not None:
            row["profiles"] = consumers[r.ref]
        result.append((r.subject_ref, row, facts))
    return result


def plan_gaps(pools: list[_PlanPool]) -> PaceGaps:
    from peerhub.extensions.pool_health import pace_gaps

    by_name: dict[str, PoolHealth | None] = {}
    for _, row, health in pools:
        name = str(row["pool"])
        by_name[name] = None if name in by_name else health  # duplicate account names are ambiguous
    return pace_gaps({name: health for name, health in by_name.items() if health is not None})


def _round_plan_floats(value: object) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, Mapping):
        return {k: _round_plan_floats(v) for k, v in cast(Mapping[str, object], value).items()}
    if isinstance(value, list):
        return [_round_plan_floats(v) for v in cast(list[object], value)]
    return value


def plan_data(report: DiagnosticReport, store_selection: Mapping[str, object],
              load: Mapping[str, object] | None = None,
              profiles_by_pool: Mapping[str, list[str]] | None = None, *, peers: frozenset[str] = frozenset()) -> dict[str, object]:
    """Project the JSON plan from the stored snapshot."""
    pools = [pool for pool in plan_pools(report, profiles_by_pool=profiles_by_pool)
             if not peers or pool[0] in peers]
    data: dict[str, object] = {"schema_version": "1.0", "status": report.status, "read_at": report.read_at,
                               "store_selection": dict(store_selection)}
    data.update(legend=PLAN_LEGEND, pools=[_round_plan_floats(row) for _, row, _ in pools],
                gaps=_round_plan_floats(plan_gaps(pools)))
    if load is not None:
        data["load"] = _round_plan_floats(dict(load))  # additive: sampled now, unlike the stored pools
    return data
