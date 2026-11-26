"""Local-only live provider gate: runs the real claude/codex/agy canaries (spends provider quota).

Never run from CI. Usage: python -m tools.live_gate --yes [--only live|slow|e2e|canary] [--peer cc|cx|ag] [--out DIR]
Writes one JUnit file per stage (g3.xml, g3-slow.xml, g3-e2e.xml) and binds each to the candidate commit (tools.release_evidence
stamp, gate G3), so the files can be attached to a release as the G3 evidence. A dirty working tree is refused: the stamped
revision must describe the code that was tested. A full passing run exits 1 when no evidence file <date>-v<version>.json
exists in .peerhub/work/live-gate; the "Live evidence to attach" line is printed only after a passing run that is not
--only canary. --only canary runs optional measurements separately,
writing canary.xml without a G3 stamp; it is excluded from the default release gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.etree.ElementTree import ParseError

from tools.release_evidence import RELEASE_POLICY_REL, junit_passed, source_binding_errors, source_version

ROOT = Path(__file__).resolve().parents[1]
STAGES = (("live", "runtime canaries", "g3.xml"), ("slow", "real adapter and quota probes", "g3-slow.xml"),
          ("e2e", "public ask scenarios", "g3-e2e.xml"))
STAGE_SELECTIONS = {"live": "live and not slow and not e2e", "slow": "slow and not e2e", "e2e": "e2e"}
CANARY_STAGE = ("canary", "optional public ask measurements (outside release catalog)", "canary.xml")
EVIDENCE_DIR = ROOT / ".peerhub" / "work" / "live-gate"  # the canaries write <date>-v<version>.json here (git-ignored)
PROVIDER_CLIS = ("agy", "claude", "codex")


def evidence_json_name(name: str, version: str) -> bool:
    return re.fullmatch(rf"[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}-v{re.escape(version)}\.json", name) is not None


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checkpoint_evidence(checkpoint: Path, out: Path, evidence_dir: Path, commit: str, version: str,
                        policy: Path) -> dict[str, str]:
    if not checkpoint.exists():
        return {}
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    if saved.get("head") != commit:
        raise ValueError(f"live checkpoint HEAD differs from {commit}")
    hashes = saved["evidence_sha256"]
    reports = [out / name for _, _, name in STAGES if name in hashes]
    json_names = [name for name in hashes if evidence_json_name(name, version)]
    if (not reports or [p.name for p in reports] != [stage[2] for stage in STAGES[:len(reports)]]
            or len(json_names) != 1 or len(hashes) != len(reports) + 1):
        raise ValueError("live checkpoint must contain consecutive passing stages and one live evidence JSON")
    for path in (*reports, evidence_dir / json_names[0]):
        if not path.is_file() or file_digest(path) != hashes[path.name]:
            raise ValueError(f"live checkpoint SHA-256 mismatch for {path.name}")
    if errors := source_binding_errors(reports, commit, policy):
        raise ValueError("\n".join(errors))
    for report in reports:
        if not junit_passed(report):
            raise ValueError(f"{report.name} did not pass all live tests")
    return hashes


def write_checkpoint(checkpoint: Path, commit: str, hashes: dict[str, str]) -> None:
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=checkpoint.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps({"head": commit, "evidence_sha256": hashes}).encode("utf-8"))
        temporary.replace(checkpoint)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="acknowledge that this spends real provider quota")
    ap.add_argument("--only", choices=[s[0] for s in (*STAGES, CANARY_STAGE)], help="run a single stage")
    ap.add_argument("--peer", choices=("cc", "cx", "ag", "ca", "oa", "ga"),
                    help="with --only live/canary: select one peer to reduce quota use (ca, oa and ga exist in canary only; they spend API credits)")
    ap.add_argument("--out", type=Path, default=EVIDENCE_DIR, help="JUnit output directory")
    ap.add_argument("--checkpoint", type=Path, help="record hashes of completed full-gate stages for release_flow")
    ap.add_argument("--resume", action="store_true", help="reuse verified stages from --checkpoint")
    args = ap.parse_args(argv)
    if args.peer and args.only not in ("live", "canary"):
        ap.error("--peer requires --only live or --only canary (partial evidence is not a complete release gate)")
    if (args.checkpoint and args.only) or (args.resume and not args.checkpoint):
        ap.error("--checkpoint applies to the full gate and --resume requires --checkpoint")
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
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    if dirty:
        print("refusing: uncommitted changes would make live evidence untruthful (commit them before running)\n" + dirty,
              file=sys.stderr)
        return 2
    try:
        return run_stages(args, commit)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ParseError) as exc:
        print(f"live gate stopped: {exc}", file=sys.stderr)
        return 1


def run_stages(args: argparse.Namespace, commit: str) -> int:
    from tools.release_evidence import stamp_junit

    args.out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PEERHUB_LIVE": "1"}
    hashes: dict[str, str] = {}
    before_json: dict[str, str] = {}
    version = ""
    if args.checkpoint:
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("live checkpoint requires a full HEAD SHA")
        version = source_version(ROOT)
        if args.resume:
            hashes = checkpoint_evidence(args.checkpoint, args.out, EVIDENCE_DIR, commit, version, ROOT / RELEASE_POLICY_REL)
        else:
            args.checkpoint.unlink(missing_ok=True)
        before_json = {p.name: file_digest(p) for p in EVIDENCE_DIR.glob(f"*-v{version}.json")
                       if p.is_file() and evidence_json_name(p.name, version)}
    worst = 0
    stages = (CANARY_STAGE,) if args.only == "canary" else STAGES
    stages = tuple(stage for stage in stages if (not args.only or args.only == stage[0]) and stage[2] not in hashes)
    for _, _, name in stages:
        (args.out / name).unlink(missing_ok=True)
    for marker, label, name in stages:
        selection = "canary" if marker == "canary" else STAGE_SELECTIONS[marker]
        if args.peer:
            selection = f"({selection}) and {args.peer}"
        print(f"== {label} (-m {selection})", flush=True)
        junit = args.out / name
        code = subprocess.call([sys.executable, "-u", "-m", "pytest", "-q", "-m", selection, f"--junitxml={junit}",
                                "tests/communication/live"], cwd=ROOT, env=env)
        worst = worst or code
        if marker != "canary" and junit.is_file():
            stamp_junit(junit, commit=commit, gate="G3")
        if code:
            break
        if args.checkpoint:
            if not junit_passed(junit):
                raise ValueError(f"{name} did not pass all live tests")
            if marker == STAGES[0][0]:
                changed = {p.name: digest for p in EVIDENCE_DIR.glob(f"*-v{version}.json")
                           if p.is_file() and evidence_json_name(p.name, version)
                           and (digest := file_digest(p)) != before_json.get(p.name)}
                if len(changed) != 1:
                    raise ValueError(f"expected one new or updated <date>-v{version}.json from the live gate")
                hashes.update(changed)
            hashes[name] = file_digest(junit)
            write_checkpoint(args.checkpoint, commit, hashes)
    if args.only == "canary":
        print(f"Canary JUnit written to {args.out} (outside release evidence)")
    else:
        print(f"JUnit written to {args.out} (bound to {commit[:12]}, gate G3)")
    if worst or args.only == "canary":
        return 1 if worst else 0
    from peerhub import __version__

    evidence_files = (sorted(EVIDENCE_DIR / name for name in hashes if evidence_json_name(name, version))
                      if args.checkpoint else sorted(EVIDENCE_DIR.glob(f"*-v{__version__}.json")))
    if not evidence_files and not args.only:
        print(f"missing live evidence: expected {EVIDENCE_DIR}{os.sep}<date>-v{__version__}.json "
              "(the release needs it as its fourth asset)", file=sys.stderr)
        return 1
    for evidence in evidence_files:
        print(f"Live evidence to attach to the release: {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
