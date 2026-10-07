"""Local-only live provider gate: runs the real claude/codex/agy canaries (spends provider quota).

Never run from CI. Usage: python -m tools.live_gate --yes [--only live|slow|e2e] [--out DIR]
Writes one JUnit file per stage so the result can be attached to a candidate as evidence.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = (("live", "runtime canaries"), ("slow", "real adapter and quota probes"), ("e2e", "public ask scenarios"))
PROVIDER_CLIS = ("agy", "claude", "codex")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="acknowledge that this spends real provider quota")
    ap.add_argument("--only", choices=[m for m, _ in STAGES], help="run a single stage")
    ap.add_argument("--out", type=Path, default=ROOT / ".peerhub" / "live-gate", help="JUnit output directory")
    args = ap.parse_args(argv)
    if os.environ.get("CI"):
        print("refusing to run in CI: the live gate is local-only", file=sys.stderr)
        return 2
    if not args.yes:
        print("the live gate spends real provider quota; re-run with --yes", file=sys.stderr)
        return 2
    missing = [c for c in PROVIDER_CLIS if shutil.which(c) is None]
    if missing:
        print(f"missing provider CLIs on PATH: {', '.join(missing)}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PEERHUB_LIVE": "1"}
    worst = 0
    for marker, label in STAGES:
        if args.only and args.only != marker:
            continue
        print(f"== {label} (-m {marker})", flush=True)
        junit = args.out / f"live-{marker}.xml"
        code = subprocess.call([sys.executable, "-u", "-m", "pytest", "-q", "-m", marker, f"--junitxml={junit}",
                                "tests/communication/live"], cwd=ROOT, env=env)
        worst = worst or code
    print(f"JUnit written to {args.out}")
    return 1 if worst else 0


if __name__ == "__main__":
    raise SystemExit(main())
