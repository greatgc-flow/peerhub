"""Quota utilization report per account pool from stored evidence (read-only; never probes providers).

Closed 7D windows get exactly one verdict, checked in order:
EXHAUSTED_EARLY (a sampled 0% with more than EARLY_HOURS to reset), POSSIBLY_UNUSED (last sample within
NEAR_RESET_HOURS of reset shows more than UNUSED_LEFT left), USED (that sample shows UNUSED_LEFT or less),
UNKNOWN (no sample near the reset). Open windows get PACE, or EXHAUSTED_EARLY / USED once a 0% is sampled. Verdicts measure utilization from sampled evidence, not task value,
and pools are shared by every session on the account, so they never attribute use to one peer.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.quota_backtest import read_samples

H = 3600.0
EXHAUSTED = 1.0          # exhausted = a measured 0% left (frozen spec); near-zero positive capacity is not exhausted
EARLY_HOURS = 12.0
NEAR_RESET_HOURS = 12.0
UNUSED_LEFT = 0.15
FRESH_HOURS = 6.0        # open-window PACE needs a sample at most this old
PACE_PP = 10.0           # |PACE| above this is slow/fast


def _window_seconds(pool: str) -> float:
    return 7 * 24 * H if pool.endswith("7D") else 5 * H


def _valid(sample: dict[str, Any], now: float) -> bool:
    used, t, reset = sample.get("used_fraction"), sample.get("measured_at"), sample.get("resets_at")
    return (sample.get("state") == "MEASURED" and isinstance(used, (int, float)) and 0 <= used <= 1
            and isinstance(t, (int, float)) and isinstance(reset, (int, float)) and t <= now and t < reset)


def _windows(samples: list[dict[str, Any]]) -> dict[str, list[list[dict[str, Any]]]]:
    """Cluster samples per pool by reset time: a reset within 10% of the window of the cluster's FIRST reset
    joins it (bounded, so slowly drifting idle anchors cannot chain distinct windows together)."""
    by_pool: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        by_pool.setdefault(str(sample["pool"]), []).append(sample)
    out: dict[str, list[list[dict[str, Any]]]] = {}
    for pool, items in by_pool.items():
        tolerance = 0.1 * _window_seconds(pool)
        clusters: list[list[dict[str, Any]]] = []
        for sample in sorted(items, key=lambda x: (x["resets_at"], x["measured_at"])):
            if clusters and sample["resets_at"] - clusters[-1][0]["resets_at"] <= tolerance:
                clusters[-1].append(sample)
            else:
                clusters.append([sample])
        out[pool] = [sorted(c, key=lambda x: x["measured_at"]) for c in clusters]
    return out


def _closed(pool: str, series: list[dict[str, Any]], since: float | None) -> dict[str, Any]:
    reset = max(x["resets_at"] for x in series)
    start = reset - _window_seconds(pool)
    zeros = [x for x in series if x["used_fraction"] >= EXHAUSTED]
    last = series[-1]
    row: dict[str, Any] = dict(pool=pool, resets_at=reset, samples=len(series),
                               incomplete=since is not None and since > start)
    first_zero = zeros[0] if zeros else None
    if first_zero and reset - first_zero["measured_at"] > EARLY_HOURS * H:
        before = [x for x in series if x["measured_at"] < first_zero["measured_at"]]
        row.update(verdict="EXHAUSTED_EARLY",
                   first_zero_between=[before[-1]["measured_at"] if before else None, first_zero["measured_at"]],
                   observed_exhausted_hours=round((zeros[-1]["measured_at"] - first_zero["measured_at"]) / H, 1))
    elif reset - last["measured_at"] <= NEAR_RESET_HOURS * H:
        left = round(1 - last["used_fraction"], 4)
        row.update(verdict="POSSIBLY_UNUSED" if left > UNUSED_LEFT else "USED", left_at_last_sample=left,
                   hours_before_reset=round((reset - last["measured_at"]) / H, 1))
    else:
        row.update(verdict="UNKNOWN", reason=f"no sample within {NEAR_RESET_HOURS:g}h of reset")
    return row


def _open(pool: str, series: list[dict[str, Any]], now: float) -> dict[str, Any]:
    last = series[-1]
    reset = last["resets_at"]
    row: dict[str, Any] = dict(pool=pool, resets_at=reset, left=round(1 - last["used_fraction"], 4),
                               age_hours=round((now - last["measured_at"]) / H, 1))
    start = last.get("window_start")
    if now - last["measured_at"] > FRESH_HOURS * H:
        return {**row, "pace_state": "UNKNOWN", "reason": f"stale: last sample older than {FRESH_HOURS:g}h"}
    if last["used_fraction"] >= EXHAUSTED:
        zero = next(x for x in series if x["used_fraction"] >= EXHAUSTED)
        hours = round((reset - zero["measured_at"]) / H, 1)
        return {**row, "pace_state": "EXHAUSTED_EARLY" if hours > EARLY_HOURS else "USED",
                "reason": f"first sampled exhausted {_ts(zero['measured_at'])}, {hours}h before reset"}
    if not isinstance(start, (int, float)) or not start < last["measured_at"] < reset:
        return {**row, "pace_state": "UNKNOWN", "reason": "no valid window anchor"}
    pace = 100 * (last["used_fraction"] - (last["measured_at"] - start) / (reset - start))
    state = "fast" if pace > PACE_PP else "slow" if pace < -PACE_PP else "on-pace"
    return {**row, "pace_pp": round(pace, 1), "pace_state": state}


def build_report(samples: list[dict[str, Any]], *, now: float, since: float | None = None) -> dict[str, Any]:
    valid = [x for x in samples if _valid(x, now) and (since is None or x["measured_at"] >= since)]
    invalid = sum(1 for x in samples if not _valid(x, now))
    closed: list[dict[str, Any]] = []
    opened: list[dict[str, Any]] = []
    five: dict[str, dict[str, int]] = {}
    for pool, windows in sorted(_windows(valid).items()):
        if pool.endswith("5H"):
            five[pool] = {"observed_windows": len(windows),
                          "observed_exhausted_windows": sum(any(x["used_fraction"] >= EXHAUSTED for x in w) for w in windows)}
            continue
        for series in windows:
            if max(x["resets_at"] for x in series) <= now:
                closed.append(_closed(pool, series, since))
            else:
                opened.append(_open(pool, series, now))
    return dict(generated_at=now, since=since, invalid_samples=invalid, closed_7d=closed, open_7d=opened, five_hour=five,
                thresholds=dict(exhausted_used=EXHAUSTED, early_hours=EARLY_HOURS, near_reset_hours=NEAR_RESET_HOURS,
                                unused_left=UNUSED_LEFT, fresh_hours=FRESH_HOURS, pace_pp=PACE_PP))


def _ts(value: float | None) -> str:
    return "-" if value is None else datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%MZ")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"Generated {_ts(report['generated_at'])}; since {_ts(report['since'])}; invalid samples ignored: {report['invalid_samples']}.",
             f"Thresholds: exhausted = a measured 0% left; EXHAUSTED_EARLY = sampled exhaustion with > {EARLY_HOURS:g}h to reset; "
             f"POSSIBLY_UNUSED / USED = last sample within {NEAR_RESET_HOURS:g}h of reset shows > / <= {UNUSED_LEFT:.0%} left; "
             f"otherwise UNKNOWN. Open-window PACE = used% - elapsed% (fast > +{PACE_PP:g}, slow < -{PACE_PP:g}; "
             f"needs a sample <= {FRESH_HOURS:g}h old). Sampled evidence only; pools are shared by all sessions on the account.",
             "", "## Closed 7D windows", "", "| Pool | Reset | Samples | Verdict | Evidence |", "|---|---|---|---|---|"]
    for w in report["closed_7d"]:
        if w["verdict"] == "EXHAUSTED_EARLY":
            ev = (f"first sampled exhaustion between {_ts(w['first_zero_between'][0])} and {_ts(w['first_zero_between'][1])}, "
                  f"{(w['resets_at'] - w['first_zero_between'][1]) / H:.1f}h before reset; sampled exhausted span {w['observed_exhausted_hours']}h")
        elif w["verdict"] == "UNKNOWN":
            ev = w["reason"]
        else:
            ev = f"{w['left_at_last_sample']:.0%} left {w['hours_before_reset']}h before reset"
        ev += " (window cut by --since)" if w["incomplete"] else ""
        lines.append(f"| {w['pool']} | {_ts(w['resets_at'])} | {w['samples']} | {w['verdict']} | {ev} |")
    lines += ["", "## Open 7D windows", "", "| Pool | Resets | Left | PACE pp | State | Note |", "|---|---|---|---|---|---|"]
    for w in report["open_7d"]:
        lines.append(f"| {w['pool']} | {_ts(w['resets_at'])} | {w['left']:.1%} | {w.get('pace_pp', '-')} | {w['pace_state']} | "
                     f"{w.get('reason', f'sample age {w['age_hours']}h')} |")
    lines += ["", "## 5H windows", "", "| Pool | Observed windows | Observed exhausted windows |", "|---|---|---|"]
    for pool, c in report["five_hour"].items():
        lines.append(f"| {pool} | {c['observed_windows']} | {c['observed_exhausted_windows']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--since", help="ISO date/time (UTC unless an offset is given); earlier samples are ignored and cut windows are marked")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    since = None
    if args.since:
        parsed = datetime.fromisoformat(args.since)
        since = (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp()
    try:
        report = build_report(read_samples(args.db), now=time.time(), since=since)
    except (sqlite3.Error, OSError) as exc:
        print(f"Cannot read quota store: {exc}", file=sys.stderr)
        return 5 if not args.db.is_file() else 4
    print(json.dumps(report, sort_keys=True) if args.json else render_markdown(report), end="" if not args.json else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
