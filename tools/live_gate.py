"""Local-only live provider gate: runs the real claude/codex/agy canaries (spends provider quota).

Never run from CI. Usage: python -m tools.live_gate --yes [--only live|slow|e2e|canary] [--peer cc|cx|ag] [--out DIR] [--allow-dirty]
Writes one JUnit file per stage (g3.xml, g3-slow.xml, g3-e2e.xml) and binds each to the candidate commit (tools.release_evidence
stamp, gate G3), so the files can be attached to a release as the G3 evidence. A dirty working tree is refused: the stamped
revision must describe the code that was tested. --only canary runs optional measurements separately,
writing canary.xml without a G3 stamp; it is excluded from the default release gate.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = (("live", "runtime canaries", "g3.xml"), ("slow", "real adapter and quota probes", "g3-slow.xml"),
          ("e2e", "public ask scenarios", "g3-e2e.xml"))
STAGE_SELECTIONS = {"live": "live and not slow and not e2e", "slow": "slow and not e2e", "e2e": "e2e"}
CANARY_STAGE = ("canary", "optional public ask measurements (outside release catalog)", "canary.xml")
EVIDENCE_PATHS = ("docs/implementation/live_evidence",)  # written by the canaries themselves
PROVIDER_CLIS = ("agy", "claude", "codex")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="acknowledge that this spends real provider quota")
    ap.add_argument("--only", choices=[s[0] for s in (*STAGES, CANARY_STAGE)], help="run a single stage")
    ap.add_argument("--peer", choices=("cc", "cx", "ag"), help="with --only live/canary: select one peer to reduce quota use")
    ap.add_argument("--allow-dirty", action="store_true", help="stamp even though the working tree has uncommitted changes")
    ap.add_argument("--out", type=Path, default=ROOT / ".peerhub" / "work" / "live-gate", help="JUnit output directory")
    args = ap.parse_args(argv)
    if args.peer and args.only not in ("live", "canary"):
        ap.error("--peer requires --only live or --only canary (partial evidence is not a complete release gate)")
    if os.environ.get("CI"):
        print("refusing to run in CI: the live gate is local-only", file=sys.stderr)
        return 2
    if not args.yes:
        print("the live gate spends real provider quota; re-run with --yes", file=sys.stderr)
        return 2
    missing = [c for c in PROVIDER_CLIS if shutil.which(c) is None]
    if missing and args.only not in ("live", "canary"):
        print(f"missing provider CLIs on PATH: {', '.join(missing)}", file=sys.stderr)
        return 2
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", ".", *(f":(exclude){p}" for p in EVIDENCE_PATHS)],
                                    cwd=ROOT, text=True).strip()
    if dirty and not args.allow_dirty:
        print("refusing: uncommitted changes would make the stamped revision untruthful (commit them or pass --allow-dirty)\n" + dirty,
              file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PEERHUB_LIVE": "1"}
    worst = 0
    stamped: list[Path] = []
    stages = (CANARY_STAGE,) if args.only == "canary" else STAGES
    for marker, label, name in stages:
        if args.only and args.only != marker:
            continue
        selection = "canary" if marker == "canary" else STAGE_SELECTIONS[marker]
        if args.peer:
            selection = f"({selection}) and {args.peer}"
        print(f"== {label} (-m {selection})", flush=True)
        junit = args.out / name
        code = subprocess.call([sys.executable, "-u", "-m", "pytest", "-q", "-m", selection, f"--junitxml={junit}",
                                "tests/communication/live"], cwd=ROOT, env=env)
        worst = worst or code
        if marker != "canary" and junit.is_file():
            stamped.append(junit)
    from tools.release_evidence import stamp_junit

    for junit in stamped:
        stamp_junit(junit, commit=commit, gate="G3")
    if args.only == "canary":
        print(f"Canary JUnit written to {args.out} (outside release evidence)")
    else:
        print(f"JUnit written to {args.out} (bound to {commit[:12]}, gate G3)")
    return 1 if worst else 0


if __name__ == "__main__":
    raise SystemExit(main())
