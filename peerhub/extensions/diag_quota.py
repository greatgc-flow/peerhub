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
from typing import Any, TypedDict, cast

from peerhub.extensions.diag import DiagnosticReport, ReadonlyDiag
from peerhub.extensions.observation_model import MEASUREMENT_KEYS
from peerhub.extensions.quota_projection import project_exhaustion

SCHEMA_VERSION = "1.0"
QUOTA_KINDS = ("quota", "rate_limit")
RESET_CREDIT_KIND = "reset_credit"
RESET_CREDIT_WARN_SECONDS = 72 * 3600  # an available credit expiring in strictly less than this is flagged WARN
POOL_KINDS = ("QUOTA", "RATE_LIMIT")  # registered pools of these kinds without evidence are listed as UNKNOWN
# status -> public CLI exit code: 4 storage fault, 5 diag unavailable, 6 schema version
EXIT_BY_STATUS = {"OK": 0, "UNAVAILABLE": 5, "FAILED": 4, "SCHEMA_VERSION": 6}


class _QuotaObservation(TypedDict):
    kind: str
    state: str
    subject_ref: str
    resource_pool_ref: str | None
    payload: dict[str, object]
    observed_at: str
    age_seconds: float | None


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
                            "hours_until_expiry": left / 3600, "title": c.get("title"),
                            "warn": c.get("status") == "available" and left < RESET_CREDIT_WARN_SECONDS})
    nearest = _ts(p.get("nearest_expires_at")) if shown else None
    left_n = None if nearest is None else nearest - now
    return {"subject_ref": it["subject_ref"], "resource_pool_ref": it["resource_pool_ref"], "state": it["state"], "age_seconds": it["age_seconds"],
            "source": it["source"], "condition": p.get("condition"), "available_count": count if shown else None,
            "nearest_expires_at": None if nearest is None else _iso(nearest),
            "hours_until_nearest_expiry": None if left_n is None else left_n / 3600,
            "warn": left_n is not None and left_n < RESET_CREDIT_WARN_SECONDS, "credits": credits}


def quota_report(db_path: str | Path, *, peer: str | None = None, pool: str | None = None, read_at: float | None = None) -> dict[str, Any]:
    now = time.time() if read_at is None else read_at
    db = str(db_path)
    out: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "status": "OK", "source_db": db, "read_at": now,
                           "filters": {"peer": peer, "pool": pool}, "overall": "UNKNOWN", "pools": [], "reset_credits": [], "error": None}
    if not Path(db).is_file():  # never open (and thereby never create) a missing database
        out.update(status="UNAVAILABLE", error=f"database not found: {db}")
        return out
    rep = ReadonlyDiag(db).render(["resource_pools", "observations"], read_at=now)
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
        meas = _measurements(it["payload"])
        der = _derived(meas)
        start = it["payload"].get("window_started_at")
        reset = it["payload"].get("resets_at")
        meas_at = None
        if it.get("observed_at"):
            try:
                meas_at = datetime.fromisoformat(str(it["observed_at"]).replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        if meas_at is None and it.get("age_seconds") is not None:
            meas_at = now - it["age_seconds"]
        proj = project_exhaustion(der.get("used_fraction"), start, meas_at, reset, now)
        groups.setdefault(it["resource_pool_ref"], []).append({
            "subject_ref": it["subject_ref"], "resource_pool_ref": it["resource_pool_ref"], "kind": it["kind"], "state": it["state"],
            "age_seconds": it["age_seconds"], "source": it["source"], "measurements": meas, "derived": der,
            "window": {k: it["payload"][k] for k in ("window_started_at", "resets_at") if k in it["payload"]},
            "projection": proj})
    if peer is None:  # a registered pool with no quota/rate_limit evidence is explicit UNKNOWN (never unlimited)
        for p in rep.sections["resource_pools"].data.get("pools", []):
            pid = p["resource_pool_id"]
            if p["kind"] in POOL_KINDS and pid not in groups and (pool is None or pool == pid):
                groups[pid] = [{"subject_ref": None, "resource_pool_ref": pid, "kind": p["kind"].lower(), "state": "UNKNOWN", "age_seconds": None,
                                "source": None, "measurements": {}, "derived": {},
                                "projection": project_exhaustion(None, None, None, None, now)}]
    pools: list[dict[str, Any]] = [{"resource_pool_ref": k, "items": groups[k]} for k in sorted(groups, key=lambda x: (x is None, x or ""))]
    out["pools"] = pools
    out["reset_credits"] = resets
    if errors:
        out["error"] = "; ".join(errors)
        out["status"] = "PARTIAL"
    out["overall"] = "OK" if any(i["state"] in ("MEASURED", "STALE") for g in pools for i in g["items"]) else "UNKNOWN"
    return out


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


def format_quota_table(rep: dict[str, Any]) -> str:
    lines = [f"quota/rate-limit evidence (read-only, no refresh) source={rep['source_db']} status={rep['status']} overall={rep['overall']}"]
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
            lines.append(f"  {i['subject_ref'] or '-':<16} {i['kind']:<10} {i['state']:<11} age={age:<8} src={i['source'] or '-'}  {meas} {der}".rstrip())
    lines.extend(_reset_credit_lines(rep["reset_credits"], "reset credits (read-only; hours computed at read time)", "  "))
    return "\n".join(lines)


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


def _human_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "-"
    s = int(seconds)
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{sec:02d}s" if m else f"{sec}s"


def format_dashboard(rep: DiagnosticReport, *, store_path: str | None = None, store_source: str | None = None) -> str:
    """Presentation of a single read-only snapshot; no probes or policy engines."""
    header = f"PeerHub diagnostics  {rep.status}"
    if store_path:
        header += f"  store={store_path}" + (f" ({store_source})" if store_source else "")
    lines = [header]
    if rep.error:
        return "\n".join([*lines, rep.error])
    # Earliest threatened pool header alert
    sec_obs = rep.sections.get("observations")
    obs_items = cast(list[_QuotaObservation], sec_obs.data.get("items", [])) if sec_obs else []
    all_quota = [i for i in obs_items if i["kind"] in QUOTA_KINDS]
    threatened: list[tuple[float, str, str, str]] = []
    for it in all_quota:
        pl = it["payload"]
        rf = pl.get("remaining_fraction")
        if it["state"] in ("MEASURED", "STALE") and isinstance(rf, (float, int)) and not isinstance(rf, bool):
            st, res = pl.get("window_started_at"), pl.get("resets_at")
            m_at = None
            if it.get("observed_at"):
                try:
                    m_at = datetime.fromisoformat(str(it["observed_at"]).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    pass
            age = it["age_seconds"]
            if m_at is None and age is not None:
                m_at = rep.read_at - age
            pr = project_exhaustion(1.0 - rf, st, m_at, res, rep.read_at)
            p_name = it.get("resource_pool_ref") or it.get("kind") or "quota"
            s_name = it.get("subject_ref") or "-"
            eat = pr["exhaustion_at"]
            if pr["exhausts_before_reset"] and eat is not None:
                c_str = datetime.fromtimestamp(eat).astimezone().strftime("%H:%M")
                r_str = _human_duration(max(0.0, eat - rep.read_at))
                threatened.append((eat, s_name, p_name, f"exhausts ~{c_str} (in {r_str}), before reset"))
            elif pr.get("status") == "exhausted":
                threatened.append((rep.read_at, s_name, p_name, "exhausted"))
    if threatened:
        earliest = min(threatened, key=lambda x: x[0])
        lines.append(f"  ALERT: {earliest[1]}/{earliest[2]} {earliest[3]}")

    for name in ("peers", "streams"):
        sec = rep.sections[name]
        items = sec.data.get(name, [])
        lines.append(f"{name}: {len(items)}  {sec.status}")
        for item in items:
            lines.append(f"  {item.get('peer_id', item.get('stream_id'))}")
        lines.extend(f"  ERROR: {e['error']}" for e in sec.errors)
    lines.append("quota / rate limits")
    sec = rep.sections["observations"]
    items = [i for i in sec.data.get("items", []) if i["kind"] in QUOTA_KINDS]
    if not items:
        lines.append("  UNKNOWN: no quota/rate-limit observations")
    for item in items:
        payload = item["payload"]
        rf = payload.get("remaining_fraction")
        measured = item["state"] in ("MEASURED", "STALE")
        numbers = measured and isinstance(rf, (float, int)) and not isinstance(rf, bool)
        usage = f"used={1-rf:.1%} headroom={rf:.1%}" if numbers else "headroom=UNKNOWN"
        window = item["resource_pool_ref"] or item["kind"]
        lines.append(f"  {item['subject_ref']} {window} {item['state']} {usage} source={item['source']}")
        start, reset = payload.get("window_started_at"), payload.get("resets_at")
        if numbers and isinstance(start, (int, float)) and isinstance(reset, (int, float)) and reset > start:
            elapsed = min(1.0, max(0.0, (rep.read_at - start) / (reset - start)))
            # Descriptive comparison only; no admission/routing decision or invented quota.
            lines.append(f"    window={(reset-start)/3600:g}h elapsed={elapsed:.1%} used-vs-elapsed={(1-rf)-elapsed:+.1%} reset_at={reset}")
            m_at = None
            if item.get("observed_at"):
                try:
                    m_at = datetime.fromisoformat(str(item["observed_at"]).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    pass
            if m_at is None and item.get("age_seconds") is not None:
                m_at = rep.read_at - item["age_seconds"]
            pr = project_exhaustion(1.0 - rf, start, m_at, reset, rep.read_at)
            eat = pr["exhaustion_at"]
            if pr["status"] == "ok" and eat is not None:
                c_str = datetime.fromtimestamp(eat).astimezone().strftime("%H:%M")
                r_str = _human_duration(max(0.0, eat - rep.read_at))
                lines.append(f"    ETA: exhausts ~{c_str} (in {r_str}), before reset  [WARN]")
            elif pr["status"] == "safe_until_reset":
                lines.append("    ETA: safe until reset")
            elif pr["status"] == "exhausted":
                lines.append("    ETA: exhausted  [WARN]")
            elif pr["status"] == "idle":
                lines.append("    ETA: idle")
    lines.extend(f"  ERROR: {e['error']}" for e in sec.errors)
    pools = rep.sections["resource_pools"]
    observed = {i["resource_pool_ref"] for i in items}
    for pool in pools.data.get("pools", []):
        if pool["kind"] in POOL_KINDS and pool["resource_pool_id"] not in observed:
            lines.append(f"  {pool['resource_pool_id']} UNKNOWN: no evidence")
    lines.extend(f"  ERROR: {e['error']}" for e in pools.errors)
    resets = [_reset_credit_view(i, rep.read_at) for i in sec.data.get("items", []) if i["kind"] == RESET_CREDIT_KIND]
    lines.extend(_reset_credit_lines(resets, "reset credits", "  "))

    act = rep.sections.get("activity")
    if act and act.data.get("peers"):
        act_peers = act.data["peers"]
        if len(act_peers) <= 10:
            lines.append(f"activity: asks (newest {act.data.get('window', 50)} per peer)")
            lines.append(f"  {'PEER':<5} {'ASKS':>4} {'OK':>4} {'UNC':>4} {'FAIL':>4} {'RATE':>5} {'MEDIAN':>7}  LAST")
            for ap in act_peers:
                asks, ok = ap.get("asks", 0), ap.get("ok", 0)
                unc, bad = ap.get("uncertain", 0), ap.get("failed", 0)
                rate_txt = "-" if not asks else f"{ok / asks * 100:.0f}%"
                med = ap.get("median_seconds")
                med_txt = "-" if med is None else _human_duration(med)
                last_age = ap.get("last_age_seconds")
                last_age_txt = "-" if last_age is None else f"{_human_duration(last_age)} ago"
                lines.append(f"  {ap.get('peer', '-'):<5} {asks:>4} {ok:>4} {unc:>4} {bad:>4} {rate_txt:>5} {med_txt:>7}  {last_age_txt} {ap.get('last_status', '-')}")
        else:
            lines.append(f"activity: asks ({len(act_peers)} peers, table skipped)")
    return "\n".join(lines)
