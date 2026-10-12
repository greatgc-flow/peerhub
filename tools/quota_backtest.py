"""Replay stored quota evidence without opening a writable store or probing providers."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from peerhub.extensions.quota_projection import project_exhaustion

CLASSES = ("threat&exhausted", "threat&ok (FALSE ALARM)",
           "quiet&exhausted (MISSED)", "quiet&ok")
OPEN_NOTICE = "Open windows have unknown outcomes and are not scored."
ACCURACY_NOTICE = "Zero completed exhausted windows: accuracy is NOT demonstrated."


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return float(value) if math.isfinite(value) else None
        except OverflowError:
            pass
    return None


def read_samples(db: Path) -> list[dict[str, Any]]:
    """Use SQLite mode=ro plus query_only; never initialize or migrate storage."""
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            "SELECT resource_pool_ref, state, payload_json, observed_at FROM observations "
            "WHERE kind = 'quota' AND resource_pool_ref IS NOT NULL ORDER BY capture_seq"
        ).fetchall()
    samples: list[dict[str, Any]] = []
    for pool, state, raw, observed in rows:
        try:
            payload = json.loads(raw)
            measured = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            if not isinstance(payload, dict) or measured.tzinfo is None:
                continue
            remaining = _number(payload.get("remaining_fraction"))
            samples.append(dict(pool=pool, state=state, measured_at=measured.timestamp(),
                                used_fraction=None if remaining is None else 1 - remaining,
                                window_start=payload.get("window_started_at"),
                                resets_at=payload.get("resets_at"), anchor_basis=payload.get("anchor_basis")))
        except (ValueError, TypeError, OverflowError):
            continue
    return samples


def backtest(samples: list[dict[str, Any]], *, now: float) -> dict[str, Any]:
    """Score observed outcomes, not unobserved consumption between samples.

    A threat means at least one pre-exhaustion sample forecast a threat. An
    already exhausted sample is outcome evidence, never a successful prediction.
    Each exact pool/reset group is a window; moving idle anchors are not merged.
    """
    groups: dict[tuple[str, float], dict[float, dict[str, Any]]] = {}
    invalid = 0
    for sample in samples:
        reset = _number(sample.get("resets_at"))
        measured = _number(sample.get("measured_at"))
        used = _number(sample.get("used_fraction"))
        if (reset is None or measured is None or used is None or not 0 <= used <= 1
                or measured >= reset or measured > now or sample.get("state") != "MEASURED"):
            invalid += 1
            continue
        groups.setdefault((str(sample["pool"]), reset), {})[measured] = sample
    counts: Counter[str] = Counter({label: 0 for label in CLASSES})
    windows: list[dict[str, Any]] = []
    skipped = 0
    for (pool, reset), unique in sorted(groups.items()):
        series = [unique[t] for t in sorted(unique)]
        maximum = max(s["used_fraction"] for s in series)
        if len(series) < 5 or maximum < 0.05:
            skipped += 1
            continue
        replay = [project_exhaustion(s["used_fraction"], s.get("window_start"),
                                    s["measured_at"], reset, s["measured_at"],
                                    evidence_state=s["state"], anchor_basis=s.get("anchor_basis"))
                  for s in series]
        exhausted_at = next((s["measured_at"] for s in series if s["used_fraction"] == 1), None)
        threat = any(p["state"] == "threat" and (exhausted_at is None or s["measured_at"] < exhausted_at)
                     for s, p in zip(series, replay))
        completed = now >= reset
        label = "open (outcome unknown)"
        if completed:
            label = CLASSES[(0 if threat else 2) + (0 if exhausted_at is not None else 1)]
            counts[label] += 1
        windows.append(dict(pool=pool, resets_at=reset, samples=len(series), max_used_fraction=maximum,
                            threat=threat, exhausted=exhausted_at is not None if completed else None,
                            scored=completed, classification=label,
                            projection_states=dict(Counter(p["state"] for p in replay))))
    exhausted_count = counts[CLASSES[0]] + counts[CLASSES[2]]
    notices = [OPEN_NOTICE, "Outcomes reflect sampled evidence only; unobserved exhaustion may be missed.",
               "Forecasts use conditional window averages; inferred anchors remain unvalidated."]
    if not exhausted_count:
        notices.append(ACCURACY_NOTICE)
    return dict(windows=windows, summary=dict(counts), open_windows=sum(not w["scored"] for w in windows),
                skipped_windows=skipped, invalid_samples=invalid, completed_exhausted_windows=exhausted_count,
                notices=notices)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = backtest(read_samples(args.db), now=time.time())
    except (sqlite3.Error, OSError) as exc:
        print(f"Cannot read quota store: {exc}", file=sys.stderr)
        return 5 if not args.db.is_file() else 4
    if args.json:
        print(json.dumps(report, sort_keys=True, allow_nan=False))
    else:
        for window in report["windows"]:
            print(f"{window['pool']} reset={window['resets_at']:g} samples={window['samples']}: {window['classification']}")
        for label, count in report["summary"].items():
            print(f"{label}: {count}")
        print(f"Open: {report['open_windows']}; skipped: {report['skipped_windows']}; invalid samples: {report['invalid_samples']}")
        print("\n".join(report["notices"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
