"""Recommend an ask from effective profile policy and committed quota evidence."""
from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import sys
from pathlib import Path
from typing import Any, cast

from peerhub.cli.support import quota_expired, quota_items, quota_workspace, try_quota_refresh

TIE_ORDER = {"ag": 0, "cx": 1, "cc": 2}
MAX_5H_USAGE = .90


def register_suggest_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("suggest", help="Recommend an ask from profiles and quota evidence")
    parser.add_argument("--tier", choices=("standard", "effort", "deepthink"), required=True,
                        help="Profile tier to recommend")
    parser.add_argument("--task", choices=("review", "code", "read"),
                        help="Use TASK.txt for the prompt. Code enables writes for CLI peers.")


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _pool_items(db: str) -> dict[tuple[str, str], dict[str, Any]]:
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


def _short_usage(item: dict[str, Any]) -> float | None:
    remaining = _number(item["measurements"].get("remaining_fraction")) if _usable(item) else None
    return 1 - remaining if remaining is not None and 0 <= remaining <= 1 else None


def _shell_command(argv: list[str]) -> str:
    if os.name == "nt":
        return " ".join(arg if arg.replace(".", "").replace("-", "").isalnum()
                        else "'" + arg.replace("'", "''") + "'" for arg in argv)
    return shlex.join(argv)


def _unavailable(reason: str, *, json_output: bool) -> int:
    print(f"suggest: {reason}", file=sys.stderr)
    if json_output:
        print(json.dumps({"status": "unavailable", "reason": reason}))
    return 1


def run_suggest(args: argparse.Namespace) -> int:
    from peerhub.extensions.adapters.profiles import list_profiles

    if not Path(args.db).is_file():
        return _unavailable("no peer has usable 5H and 7D quota readings (store missing)", json_output=args.json)

    workspace = quota_workspace(args.db)
    candidates: list[tuple[str, str, list[str], list[str]]] = []
    for profile in list_profiles(workspace, include_meta=True):
        name = str(profile["profile"])
        peer, _, tier = name.partition(".")
        if tier == args.tier and peer in TIE_ORDER and profile["fields"]["suggest"]["value"]:  # API peers cost credits: never suggested
            pools = cast(list[str], profile["fields"]["pools"]["value"] or [])
            candidates.append((name, peer, [pool for pool in pools if pool.endswith("-5H")],
                               [pool for pool in pools if pool.endswith("-7D")]))

    readings = _pool_items(args.db)
    due = sorted({peer for _, peer, short, weekly in candidates if peer in TIE_ORDER
                  and any(quota_expired(readings.get((peer, pool), {})) for pool in [*short, *weekly])
                  and quota_expired(readings.get((peer, ""), {}))})
    if due:
        try_quota_refresh(args.db, due)
        readings = _pool_items(args.db)

    ranked: list[tuple[float, int, str, str, str]] = []
    for name, peer, short, weekly in candidates:
        short_items = [readings.get((peer, pool), {}) for pool in short]
        used = [_short_usage(item) for item in short_items
                if not (item.get("state") == "ABSENT" and not quota_expired(item))]
        weekly_items = [readings.get((peer, pool), {}) for pool in weekly]
        paces = [_number(item["projection"]["pace_pt"]) if _usable(item) else None for item in weekly_items]
        max_used = max((value for value in used if value is not None), default=None)
        max_pace = max((value for value in paces if value is not None), default=None)
        if max_used is not None and max_used >= MAX_5H_USAGE:
            continue
        if any(_usable(item) and _number(item["measurements"].get("remaining_fraction")) == 0 for item in weekly_items):
            continue  # an exhausted 7D pool cannot serve the ask
        priority = TIE_ORDER[peer]
        unknown_5h = not short or None in used
        if not unknown_5h and max_pace is not None and None not in paces:
            five = f"5H usage {max_used:.1%}" if max_used is not None else "5H n/a"
            reason = f"{five}, 7D PACE {max_pace:+.1f} pp"
            ranked.append((max_pace, priority, name, peer, reason))
        else:
            reason = "quota unknown (5H unknown)" if unknown_5h else "quota unknown"
            ranked.append((math.inf, priority, name, peer, reason))

    if not candidates:
        return _unavailable("no enabled suggestion profile for this tier", json_output=args.json)
    if not ranked:
        return _unavailable(f"every candidate is at {MAX_5H_USAGE:.0%} 5H usage or has an exhausted 7D pool",
                            json_output=args.json)

    _, _, name, peer, reason = min(ranked)
    query_file = f"{args.task}.txt" if args.task else "prompt.txt"
    argv = ["peerhub", "--db", args.db, "ask", peer, "--profile", name, "--fresh", "--query-file", query_file]
    if workspace != Path.cwd().resolve():
        argv.extend(["--workspace", str(workspace)])
    if peer == "ag" or (args.task == "code" and peer in TIE_ORDER):
        argv.append("--writable")
    command = _shell_command(argv)
    if args.json:
        print(json.dumps({"status": "ok", "profile": name, "command": command, "argv": argv, "reason": reason}))
    else:
        print(command)
        print(reason)
    return 0
