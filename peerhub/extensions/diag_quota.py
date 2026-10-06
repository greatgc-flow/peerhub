"""PeerHub first-party extension: read-only quota / rate-limit / resource-pool evidence view (CLI `diag quota`, `diag health` observations summary).

Pure reader over `ReadonlyDiag.render` (read-only handle, one snapshot): it never probes, refreshes or writes evidence.
Honesty rules: states are reported exactly as Diag evaluates them (MEASURED/STALE/UNKNOWN/UNAVAILABLE/...); missing evidence is
an explicit UNKNOWN entry, never "unlimited"; only keys of MEASUREMENT_KEYS are passed through, numbers unconverted.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from peerhub.extensions.diag import DiagnosticReport, ReadonlyDiag
from peerhub.extensions.observation_model import MEASUREMENT_KEYS

SCHEMA_VERSION = "1.0"
QUOTA_KINDS = ("quota", "rate_limit")
POOL_KINDS = ("QUOTA", "RATE_LIMIT")  # registered pools of these kinds without evidence are listed as UNKNOWN
# status -> public CLI exit code: 4 storage fault, 5 diag unavailable, 6 schema version
EXIT_BY_STATUS = {"OK": 0, "UNAVAILABLE": 5, "FAILED": 4, "SCHEMA_VERSION": 6}


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


def quota_report(db_path: str | Path, *, peer: str | None = None, pool: str | None = None, read_at: float | None = None) -> dict[str, Any]:
    now = time.time() if read_at is None else read_at
    db = str(db_path)
    out: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "status": "OK", "source_db": db, "read_at": now,
                           "filters": {"peer": peer, "pool": pool}, "overall": "UNKNOWN", "pools": [], "error": None}
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
    obs = rep.sections["observations"]
    errors += [str(e.get("error")) for e in obs.errors]
    for it in obs.data.get("items", []):
        if it["kind"] not in QUOTA_KINDS:
            continue
        if (peer is not None and it["subject_ref"] != peer) or (pool is not None and it["resource_pool_ref"] != pool):
            continue
        meas = _measurements(it["payload"])
        groups.setdefault(it["resource_pool_ref"], []).append({
            "subject_ref": it["subject_ref"], "resource_pool_ref": it["resource_pool_ref"], "kind": it["kind"], "state": it["state"],
            "age_seconds": it["age_seconds"], "source": it["source"], "measurements": meas, "derived": _derived(meas),
            "window": {k: it["payload"][k] for k in ("window_started_at", "resets_at") if k in it["payload"]}})
    if peer is None:  # a registered pool with no quota/rate_limit evidence is explicit UNKNOWN (never unlimited)
        for p in rep.sections["resource_pools"].data.get("pools", []):
            pid = p["resource_pool_id"]
            if p["kind"] in POOL_KINDS and pid not in groups and (pool is None or pool == pid):
                groups[pid] = [{"subject_ref": None, "resource_pool_ref": pid, "kind": p["kind"].lower(), "state": "UNKNOWN", "age_seconds": None,
                                "source": None, "measurements": {}, "derived": {}}]
    pools: list[dict[str, Any]] = [{"resource_pool_ref": k, "items": groups[k]} for k in sorted(groups, key=lambda x: (x is None, x or ""))]
    out["pools"] = pools
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
    return "\n".join(lines)


def format_dashboard(rep: DiagnosticReport) -> str:
    """Presentation of a single read-only snapshot; no probes or policy engines."""
    lines = [f"PeerHub diagnostics  {rep.status}"]
    if rep.error:
        return "\n".join([*lines, rep.error])
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
    lines.extend(f"  ERROR: {e['error']}" for e in sec.errors)
    pools = rep.sections["resource_pools"]
    observed = {i["resource_pool_ref"] for i in items}
    for pool in pools.data.get("pools", []):
        if pool["kind"] in POOL_KINDS and pool["resource_pool_id"] not in observed:
            lines.append(f"  {pool['resource_pool_id']} UNKNOWN: no evidence")
    lines.extend(f"  ERROR: {e['error']}" for e in pools.errors)
    return "\n".join(lines)
