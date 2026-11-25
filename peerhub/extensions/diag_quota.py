"""PeerHub first-party extension: read-only quota / rate-limit / resource-pool evidence view (CLI `diag quota`, `diag health` observations summary).

Pure reader over `ReadonlyDiag.render` (read-only handle, one snapshot): it never probes, refreshes or writes evidence.
Honesty rules: states are reported exactly as Diag evaluates them (MEASURED/STALE/UNKNOWN/UNAVAILABLE/...); missing evidence is
an explicit UNKNOWN entry, never "unlimited"; only keys of MEASUREMENT_KEYS are passed through, numbers unconverted.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, cast

from peerhub.extensions.diag import DiagnosticReport, ReadonlyDiag
from peerhub.extensions.observation_model import (
    FreshnessPolicy,
    MEASUREMENT_KEYS,
    QUOTA_NOTICE_AGE_SECONDS,
    QUOTA_PROBE_LOAD_DELAY,
    QUOTA_PROBE_LOAD_LABEL,
    instant_us,
    oldest_measured_quota_age,
)
from peerhub.extensions.quota_projection import ExhaustionProjection, limit_by_parent, project_exhaustion, scoped_parent_ref

SCHEMA_VERSION = "1.0"
QUOTA_KINDS = ("quota", "rate_limit")
RESET_CREDIT_KIND = "reset_credit"
_SECONDS_PER_HOUR = 60 * 60
_SECONDS_PER_DAY = 24 * _SECONDS_PER_HOUR
RESET_CREDIT_WARN_SECONDS = 72 * _SECONDS_PER_HOUR  # an available credit expiring in strictly less than this is flagged WARN
POOL_KINDS = ("QUOTA", "RATE_LIMIT")  # registered pools of these kinds without evidence are listed as UNKNOWN


def _classify(rep: DiagnosticReport, db: str) -> tuple[str, str | None]:
    """('OK'|'UNAVAILABLE'|'FAILED'|'SCHEMA_VERSION', error)."""
    if not Path(db).is_file():
        return "UNAVAILABLE", f"database not found: {db}"
    if rep.status == "FAILED":
        err = rep.error or "unreadable"
        return ("SCHEMA_VERSION" if err.startswith("SchemaVersionError") else "FAILED"), err
    for sec in rep.sections.values():
        for e in sec.errors:
            if "no such table" in str(e.get("error", "")):
                return "UNAVAILABLE", f"observation tables missing in {db}: {e['error']}"
    return "OK", None


def _measurements(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: payload[k] for k in sorted(payload) if k in MEASUREMENT_KEYS}


def _derived(meas: dict[str, Any]) -> dict[str, Any]:
    rf = meas.get("remaining_fraction")
    if isinstance(rf, (int, float)) and not isinstance(rf, bool):
        return {"used_fraction": 1 - rf}  # derived: 1 - remaining_fraction
    return {}


def projection(item: Mapping[str, Any], now: float) -> ExhaustionProjection:
    """Pass evaluated freshness and measurement time to the shared engine."""
    payload = item["payload"]
    measured_at = None
    if item.get("observed_at"):
        try:
            measured_at = instant_us(str(item["observed_at"])) / 1_000_000
        except (ValueError, OverflowError):
            pass
    return project_exhaustion(
        _derived(_measurements(payload)).get("used_fraction"),
        payload.get("window_started_at"), measured_at, payload.get("resets_at"), now,
        evidence_state=item["state"], anchor_basis=payload.get("anchor_basis"),
    )





def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 253402300800 else None


def _reset_credit_view(it: dict[str, Any], now: float) -> dict[str, Any]:
    """One reset_credit observation -> display dict. Hours are derived at READ time from `now`; ids are shown only as a short hash.
    Counts/credits are shown only for MEASURED/STALE evidence (UNKNOWN/ERROR never display a count)."""
    p: dict[str, Any] = it["payload"]
    count: object = p.get("available_count")
    shown = it["state"] in ("MEASURED", "STALE") and isinstance(count, int) and not isinstance(count, bool)
    credits: list[dict[str, Any]] = []
    raw_credits: object = p.get("credits")
    if shown and isinstance(raw_credits, list):
        for raw in cast("list[Any]", raw_credits):
            if not isinstance(raw, dict):
                continue
            c = cast("dict[str, Any]", raw)
            exp = _ts(c.get("expires_at"))
            if exp is None:
                continue
            left = exp - now
            credits.append({"id_hash": hashlib.sha256(str(c.get("id_ref")).encode()).hexdigest()[:12], "reset_type": c.get("reset_type"),
                            "status": c.get("status"), "granted_at": c.get("granted_at"), "expires_at": _iso(exp),
                            "hours_until_expiry": left / _SECONDS_PER_HOUR, "title": c.get("title"),
                            "warn": c.get("status") == "available" and left < RESET_CREDIT_WARN_SECONDS})
    nearest = _ts(p.get("nearest_expires_at")) if shown else None
    left_n = None if nearest is None else nearest - now
    return {"subject_ref": it["subject_ref"], "resource_pool_ref": it["resource_pool_ref"], "state": it["state"], "age_seconds": it["age_seconds"],
            "source": it["source"], "condition": p.get("condition"), "available_count": count if shown else None,
            "nearest_expires_at": None if nearest is None else _iso(nearest),
            "hours_until_nearest_expiry": None if left_n is None else left_n / _SECONDS_PER_HOUR,
            "warn": left_n is not None and left_n < RESET_CREDIT_WARN_SECONDS, "credits": credits}


def read_quota_snapshot(db_path: str | Path, *, read_at: float | None = None, policy: FreshnessPolicy | None = None,
                        include_quota_samples: bool = False) -> DiagnosticReport:
    """Read pools and observations together on the diagnostic read-only connection."""
    reader = ReadonlyDiag(db_path, policy=policy)
    sections = ["resource_pools", "observations"]
    if include_quota_samples:
        read_options = {"read_at": read_at} if read_at is not None else {}
        return reader.render(sections, include_quota_samples=True, **read_options)
    return reader.render(sections, read_at=read_at)


def quota_report(db_path: str | Path, *, peer: str | None = None, pool: str | None = None, max_age_seconds: int | None = None,
                 read_at: float | None = None, policy: FreshnessPolicy | None = None) -> dict[str, Any]:
    now = time.time() if read_at is None else read_at
    db = str(db_path)
    out: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "status": "OK", "source_db": db, "read_at": now,
                           "filters": {"peer": peer, "pool": pool}, "overall": "UNKNOWN", "pools": [], "reset_credits": [], "error": None}
    if max_age_seconds is not None:
        out["hidden_count"] = 0
    if not Path(db).is_file():  # never open (and thereby never create) a missing database
        out.update(status="UNAVAILABLE", error=f"database not found: {db}")
        return out
    rep = read_quota_snapshot(db, policy=policy, read_at=now)
    status, err = _classify(rep, db)
    if status != "OK":
        out.update(status=status, error=err)
        return out
    errors: list[str] = []
    groups: dict[str | None, list[dict[str, Any]]] = {}
    resets: list[dict[str, Any]] = []
    obs = rep.sections["observations"]
    errors += [str(e.get("error")) for e in obs.errors]
    for it in obs.data.get("items", []):
        if it["kind"] == RESET_CREDIT_KIND:
            if (peer is None or it["subject_ref"] == peer) and (pool is None or it["resource_pool_ref"] == pool):
                resets.append(_reset_credit_view(it, now))
            continue
        if it["kind"] not in QUOTA_KINDS:
            continue
        if (peer is not None and it["subject_ref"] != peer) or (pool is not None and it["resource_pool_ref"] != pool):
            continue
        items = groups.setdefault(it["resource_pool_ref"], [])
        if max_age_seconds is not None:
            age = _num(it["age_seconds"])
            if age is None:
                continue
            if age > max_age_seconds:
                out["hidden_count"] += 1
                continue
        meas = _measurements(it["payload"])
        der = _derived(meas)
        proj = projection(it, now)
        items.append({
            "subject_ref": it["subject_ref"], "resource_pool_ref": it["resource_pool_ref"], "kind": it["kind"], "state": it["state"],
            "age_seconds": it["age_seconds"], "source": it["source"], "measurements": meas, "derived": der,
            "window": {k: it["payload"][k] for k in ("window_started_at", "resets_at") if k in it["payload"]},
            "projection": proj,
            **({"reason": QUOTA_PROBE_LOAD_DELAY} if it["payload"].get("reason") == QUOTA_PROBE_LOAD_DELAY else {})})
    if peer is None:  # a registered pool with no quota/rate_limit evidence is explicit UNKNOWN (never unlimited)
        for p in rep.sections["resource_pools"].data.get("pools", []):
            pid = p["resource_pool_id"]
            if p["kind"] in POOL_KINDS and pid not in groups and (pool is None or pool == pid):
                groups[pid] = [{"subject_ref": None, "resource_pool_ref": pid, "kind": p["kind"].lower(), "state": "UNKNOWN", "age_seconds": None,
                                "source": None, "measurements": {}, "derived": {},
                                "projection": project_exhaustion(None, None, None, None, now, evidence_state="UNKNOWN")}]
    # A pool with filtered evidence must not be synthesized as a pool with no evidence.
    groups = {ref: items for ref, items in groups.items() if items}
    _limit_scoped(groups)
    pools: list[dict[str, Any]] = [{"resource_pool_ref": k, "items": groups[k]} for k in sorted(groups, key=lambda x: (x is None, x or ""))]
    out["pools"] = pools
    out["reset_credits"] = resets
    if errors:
        out["error"] = "; ".join(errors)
        out["status"] = "PARTIAL"
    out["overall"] = "OK" if any(i["state"] in ("MEASURED", "STALE") for g in pools for i in g["items"]) else "UNKNOWN"
    return out


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _limit_scoped(groups: dict[str | None, list[dict[str, Any]]]) -> None:
    """A scoped weekly pool (cc F-7D) is also limited by its parent (C-7D) of the same subject: when both are MEASURED and the
    parent has less left, the item gets an additive `effective` (left = the parent's, reset = the nearer, `limited_by`).
    The pool's own `measurements` and `window` stay as measured; nothing is added when the parent is not lower."""
    for ref, items in groups.items():
        parent_ref = scoped_parent_ref(ref) if ref else None
        for item in items:
            parent = next((p for p in groups.get(parent_ref, []) if p["subject_ref"] == item["subject_ref"]), None)
            if parent is None or item["state"] != "MEASURED" or parent["state"] != "MEASURED":
                continue
            left, reset = _num(item["measurements"].get("remaining_fraction")), _num(item["window"].get("resets_at"))
            parent_left = _num(parent["measurements"].get("remaining_fraction"))
            effective_left, effective_reset, limited = limit_by_parent(left, reset, parent_left,
                                                                       _num(parent["window"].get("resets_at")))
            if limited:
                item["effective"] = {"remaining_fraction": effective_left, "resets_at": effective_reset,
                                     "limited_by": parent_ref}


def observations_summary(db_path: str | Path, *, read_at: float | None = None) -> dict[str, Any]:
    """Counts of the latest observation per subject/kind/pool by evaluated state, from `db_path` (read-only)."""
    db = str(db_path)
    out: dict[str, Any] = {"source_db": db, "status": "OK", "latest_total": 0, "by_state": {}, "error": None}
    if not Path(db).is_file():
        out.update(status="UNAVAILABLE", error=f"database not found: {db}")
        return out
    rep = ReadonlyDiag(db).render(["observations"], read_at=read_at)
    status, err = _classify(rep, db)
    if status != "OK":
        out.update(status="UNAVAILABLE" if status == "UNAVAILABLE" else "ERROR", error=err)
        return out
    sec = rep.sections["observations"]
    by: dict[str, int] = {}
    for it in sec.data.get("items", []):
        by[it["state"]] = by.get(it["state"], 0) + 1
    out.update(latest_total=sum(by.values()), by_state=dict(sorted(by.items())))
    if sec.errors:
        out.update(status="ERROR", error="; ".join(str(e.get("error")) for e in sec.errors))
    return out


def format_quota_table(rep: dict[str, Any], *, max_age_seconds: int | None = None) -> str:
    lines = [f"quota/rate-limit evidence (read-only, no refresh) source={rep['source_db']} status={rep['status']} overall={rep['overall']}"]
    if max_age_seconds is not None:
        lines[0] += f" | hidden: {rep['hidden_count']} older than {max_age_seconds}s"
    age = oldest_measured_quota_age(i for group in rep["pools"] for i in group["items"])
    if notice := quota_stale_line(age):
        lines.insert(0, notice)
    if rep["error"]:
        lines.append(f"  note: {rep['error']}")
    if not rep["pools"]:
        lines.append("  UNKNOWN: no quota/rate_limit evidence (this is not 'unlimited')")
    for g in rep["pools"]:
        lines.append(f"pool {g['resource_pool_ref'] or '(none)'}")
        for i in g["items"]:
            age = "-" if i["age_seconds"] is None else f"{i['age_seconds']:.0f}s"
            meas = " ".join(f"{k}={v}" for k, v in i["measurements"].items()) or "(no measurement)"
            der = " ".join(f"{k}={v:.4g} (derived)" for k, v in i["derived"].items())
            eff = i.get("effective")
            limit = f" effective={eff['remaining_fraction']:.0%} (limited by {eff['limited_by']})" if eff else ""
            state = QUOTA_PROBE_LOAD_LABEL if i.get("reason") == QUOTA_PROBE_LOAD_DELAY else i["state"]
            lines.append(f"  {i['subject_ref'] or '-':<16} {i['kind']:<10} {state:<11} age={age:<8} src={i['source'] or '-'}  {meas} {der}{limit}".rstrip())
    lines.extend(_reset_credit_lines(rep["reset_credits"], "reset credits (read-only; hours computed at read time)", "  "))
    return "\n".join(lines)


def quota_stale_line(age: float | None) -> str:
    hint = " -> peerhub observation refresh"
    if age is None:
        return "quota: no reading" + hint
    if age <= QUOTA_NOTICE_AGE_SECONDS:
        return ""
    duration = f"{int(age // _SECONDS_PER_DAY)}d" if age >= _SECONDS_PER_DAY else f"{int(age // _SECONDS_PER_HOUR)}h"
    return f"quota {duration} old" + hint


def _reset_credit_lines(items: list[dict[str, Any]], header: str, indent: str) -> list[str]:
    if not items:
        return []
    lines = [header]
    for i in items:
        age = "-" if i["age_seconds"] is None else f"{i['age_seconds']:.0f}s"
        if i["available_count"] is None:
            detail = f"available=UNKNOWN{' condition=' + i['condition'] if i['condition'] else ''}"
        else:
            hrs = i["hours_until_nearest_expiry"]
            detail = f"available={i['available_count']} nearest={i['nearest_expires_at'] or '-'}" + (f" ({hrs:.1f}h)" if hrs is not None else "")
            detail += " WARN: expires in <72h" if i["warn"] else ""
        lines.append(f"{indent}{i['subject_ref']:<8} {i['state']:<9} age={age:<6} {detail}")
        for c in i["credits"]:
            lines.append(f"{indent}  {c['id_hash']} {c['status']} {c['title']} expires={c['expires_at']} ({c['hours_until_expiry']:.1f}h)"
                         + (" WARN" if c["warn"] else ""))
    return lines
