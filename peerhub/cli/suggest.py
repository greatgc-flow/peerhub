"""Recommend an ask from effective profile policy and committed quota evidence."""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict, cast

from peerhub.cli.monitor_refresh import quota_expired, quota_workspace, try_quota_refresh
from peerhub.core.quota_policy import LOW_QUOTA_FRACTION, format_pace
from peerhub.core.scalars import finite_number
from peerhub.core.shell import format_shell_command as _shell_command

REASON_PROFILE_NOT_ENABLED = "not suggested (profile setting)"  # the profile file sets suggest = false; its facts are still listed
TIE_ORDER = {"ag": 0, "cx": 1, "cc": 2}
MAX_5H_USAGE = .90


@dataclass(frozen=True)
class _Profile:
    name: str
    peer: str
    enabled: bool
    short: list[str]
    weekly: list[str]


class _Candidate(TypedDict):
    profile: str
    peer: str
    eligible: bool
    worst_pool: str | None
    left_pct: float | None
    pace_pt: float | None
    fast: bool
    reason: str | None


_Ranking = tuple[float, int, str, str, str]


def register_suggest_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("suggest", help="Recommend an ask from profiles and quota evidence")
    parser.add_argument("--tier", choices=("standard", "effort", "deepthink"), required=True,
                        help="Profile tier to recommend")
    parser.add_argument("--task", choices=("review", "code", "read"),
                        help="Use <task>.txt as the prompt file in the suggested ask command. The code task adds --writable.")
    parser.add_argument("--candidates", action="store_true", help="Show the tier's candidate profiles and quota facts")


def _pool_items(db: str) -> dict[tuple[str, str], dict[str, Any]]:
    from peerhub.extensions.diag_quota import quota_items

    items = quota_items(db)
    # Multiple observation sources can report a pool. Use its youngest evidence.
    by_pool: dict[tuple[str, str], dict[str, Any]] = {}
    for item in sorted(items, key=lambda item: item["age_seconds"] if item["age_seconds"] is not None else math.inf):
        peer = item["subject_ref"]
        if peer is not None:
            ref = item["resource_pool_ref"] or ""
            by_pool.setdefault((peer, ref.rsplit(":", 1)[-1]), item)
    return by_pool


def _usable(item: dict[str, Any]) -> bool:
    return (item.get("state") == "MEASURED" and not quota_expired(item)
            and item["projection"]["state"] != "expired")


def _remaining(item: dict[str, Any]) -> float | None:
    remaining = finite_number(item["measurements"].get("remaining_fraction")) if _usable(item) else None
    return remaining if remaining is not None and 0 <= remaining <= 1 else None


def _fallback_reason(items: list[dict[str, Any]]) -> str:
    if any(item.get("projection", {}).get("state") == "expired" for item in items):
        return "reset"
    if any(item.get("state") == "STALE" or (item.get("age_seconds") is not None and quota_expired(item))
           for item in items):
        return "stale"
    return "unknown"


def _evaluate(profile: _Profile, readings: dict[tuple[str, str], dict[str, Any]]) -> tuple[_Candidate, _Ranking | None]:
    """Apply the suggestion gates once and return the evidence alongside the rank."""
    short = {pool: readings.get((profile.peer, pool), {}) for pool in profile.short}
    short = {pool: item for pool, item in short.items()
             if not (item.get("state") == "ABSENT" and not quota_expired(item))}
    weekly = {pool: readings.get((profile.peer, pool), {}) for pool in profile.weekly}
    pools = short | weekly
    left = {pool: _remaining(item) for pool, item in pools.items()}
    paces = {pool: finite_number(item["projection"]["pace_pt"]) if _usable(item) else None
             for pool, item in weekly.items()}
    max_used = max((1 - value for pool, value in left.items() if pool in short and value is not None), default=None)
    max_pace = max((value for value in paces.values() if value is not None), default=None)
    worst_left, worst_pool = min(((value, pool) for pool, value in left.items() if value is not None),
                                default=(None, None))
    fast = any(value is not None and value > LOW_QUOTA_FRACTION and pools[pool]["projection"]["state"] == "threat"
               for pool, value in left.items())

    rank = None
    if not profile.enabled:
        reason = REASON_PROFILE_NOT_ENABLED
    elif max_used is not None and max_used >= MAX_5H_USAGE:
        reason = f"5H usage >= {MAX_5H_USAGE:.0%}"
    elif any(left[pool] == 0 for pool in weekly):
        reason = "exhausted 7D pool"
    else:
        unknown_short = [item for pool, item in short.items() if left[pool] is None]
        unknown_weekly = [item for pool, item in weekly.items() if paces[pool] is None]
        unknown_5h = not profile.short or bool(unknown_short)
        if not unknown_5h and max_pace is not None and not unknown_weekly:
            five = f"5H usage {max_used:.1%}" if max_used is not None else "5H n/a"
            explanation = f"{five}, 7D PACE {format_pace(max_pace)} pp"
            reason = "fast pool" if fast else None
            score = max_pace
        else:
            explanation = "quota unknown (5H unknown)" if unknown_5h else "quota unknown"
            reason = _fallback_reason(unknown_short + unknown_weekly)
            score = math.inf
        rank = (score, TIE_ORDER[profile.peer], profile.name, profile.peer, explanation)

    return _Candidate(profile=profile.name, peer=profile.peer, eligible=rank is not None, worst_pool=worst_pool,
                      left_pct=None if worst_left is None else worst_left * 100, pace_pt=max_pace,
                      fast=fast, reason=reason), rank


def _show_result(payload: dict[str, object], *, json_output: bool, candidates: list[_Candidate] | None) -> None:
    if json_output:
        if candidates is not None:
            payload["candidates"] = candidates
        print(json.dumps(payload))
    elif candidates is not None:
        rows = [("PROFILE", "PEER", "ELIGIBLE", "WORST POOL (LEFT %)", "PACE (7D pp)", "FAST", "REASON")]
        for row in candidates:
            left, pace = row["left_pct"], row["pace_pt"]
            worst = f"{row['worst_pool']} {left:.1f}%" if left is not None else "?"
            rows.append((row["profile"], row["peer"], "yes" if row["eligible"] else "no", worst,
                         format_pace(pace) if pace is not None else "?", "yes" if row["fast"] else "no", row["reason"] or "-"))
        widths = [max(map(len, column)) for column in zip(*rows)]
        for row in rows:
            print("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip())


def _unavailable(reason: str, *, json_output: bool, candidates: list[_Candidate] | None) -> int:
    print(f"suggest: {reason}", file=sys.stderr)
    _show_result({"status": "unavailable", "reason": reason}, json_output=json_output, candidates=candidates)
    return 1


def run_suggest(args: argparse.Namespace) -> int:
    from peerhub.extensions.adapters.profiles import list_profiles

    candidates: list[_Candidate] | None = [] if args.candidates else None
    if not Path(args.db).is_file():
        return _unavailable("no peer has usable 5H and 7D quota readings (store missing)",
                            json_output=args.json, candidates=candidates)

    workspace = quota_workspace(args.db)
    profiles: list[_Profile] = []
    for profile in list_profiles(workspace, include_meta=True):
        name = str(profile["profile"])
        peer, _, tier = name.partition(".")
        if tier != args.tier:
            continue
        # API peers cost credits and are excluded from recommendations.
        enabled = peer in TIE_ORDER and bool(profile["fields"]["suggest"]["value"])
        if enabled or args.candidates:
            pools = cast(list[str], profile["fields"]["pools"]["value"] or [])
            profiles.append(_Profile(name, peer, enabled, [pool for pool in pools if pool.endswith("-5H")],
                                     [pool for pool in pools if pool.endswith("-7D")]))

    readings = _pool_items(args.db)
    due = sorted({profile.peer for profile in profiles if profile.enabled
                  and any(quota_expired(readings.get((profile.peer, pool), {})) for pool in profile.short + profile.weekly)
                  and quota_expired(readings.get((profile.peer, ""), {}))})
    if due:
        try_quota_refresh(args.db, due)
        readings = _pool_items(args.db)

    ranked: list[_Ranking] = []
    for profile in profiles:
        candidate, rank = _evaluate(profile, readings)
        if candidates is not None:
            candidates.append(candidate)
        if rank is not None:
            ranked.append(rank)

    if not any(profile.enabled for profile in profiles):
        return _unavailable("no enabled suggestion profile for this tier", json_output=args.json, candidates=candidates)
    if not ranked:
        return _unavailable(f"every candidate is at {MAX_5H_USAGE:.0%} 5H usage or has an exhausted 7D pool",
                            json_output=args.json, candidates=candidates)

    _, _, name, peer, reason = min(ranked)
    query_file = f"{args.task}.txt" if args.task else "prompt.txt"
    argv = ["peerhub", "--db", args.db, "ask", peer, "--profile", name, "--fresh", "--query-file", query_file]
    if workspace != Path.cwd().resolve():
        argv.extend(["--workspace", str(workspace)])
    if peer == "ag" or (args.task == "code" and peer in TIE_ORDER):
        argv.append("--writable")
    command = _shell_command(argv)
    if not args.json:
        print(command)
        print(reason)
    _show_result({"status": "ok", "profile": name, "command": command, "argv": argv, "reason": reason},
                 json_output=args.json, candidates=candidates)
    return 0
