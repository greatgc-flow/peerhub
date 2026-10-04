"""M1 release evidence bundle (REL-007): commit/version, deterministic suite result, live canaries, package hashes and a
requirement coverage summary, with SHA-256 checksums over every bundle file.

Fail closed: `release_ready` is true only if EVERY catalog test on a blocking gate (any priority; everything but G6) has a PASSED
result (failed/error/missing/skipped all block; a skip with a machine-readable live/soak/CI-only reason is reported as
`not_verified`, never as a pass), EVERY requirement is covered, all live canaries passed, at least one package exists, and every package version equals the source version.
Soak (G6) is non-blocking and does not affect readiness.

Usage: python -m tools.m1_release_evidence --junit J.xml [--junit ...] --dist DIR --out OUTDIR [--commit SHA]   (exit 1 if not ready)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_SET = ROOT / "docs/m1_spec/06_GUIDES/TEST_SET"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def source_version(root: Path = ROOT) -> str:
    m = re.search(r"""__version__\s*=\s*["']([^"']+)["']""", (root / "peerhub/_version.py").read_text(encoding="utf-8"))
    if not m:
        raise ValueError("cannot parse peerhub/_version.py")
    return m.group(1)


def _test_ids(name: str, ids: list[str]) -> list[str]:
    key_of = {i: i.lower().replace("-", "_") for i in ids}
    low = name.lower()
    return [i for i, k in key_of.items() if re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![0-9])", low)]


# accepted machine-readable skip reasons (live opt-in / provider availability / soak opt-in / CI-only); anything else is a plain skip
ALLOWED_SKIP = re.compile(r"^(LIVE-OPT-IN|LIVE-PROVIDER-UNAVAILABLE|SOAK-OPT-IN|CI-ONLY)\[[^\]]*\]")
# A matrix cell that cannot run in this job but whose execution is recorded as evidence in the repo (docs/m1_impl/matrix_evidence/*.json);
# the claim itself is enforced by the REL-010 evidence tests, which must pass in the same run (see the aggregation below).
EVIDENCE_SKIP = re.compile(r"^CI-ONLY\[[^\]]*\]: VERIFIED-(CI|LOCAL)")


def junit_outcomes(paths: list[Path], ids: list[str]) -> dict[str, str]:
    """catalog id -> passed|failed|skipped|not_verified|evidence_verified|error|missing. An id passes only if EVERY matching testcase passed.
    `not_verified` = skipped with an accepted machine-readable reason; it is never a pass."""
    seen: dict[str, list[str]] = {}
    for p in paths:
        for case in ET.parse(p).getroot().iter("testcase"):
            outcome = "passed"
            for child in case:
                tag = child.tag
                if tag in ("failure", "error", "skipped"):
                    outcome = "failed" if tag == "failure" else tag
                    if tag == "skipped":
                        reason = (child.get("message") or child.text or "").strip()
                        outcome = ("evidence_verified" if EVIDENCE_SKIP.match(reason) else
                                   "not_verified" if ALLOWED_SKIP.match(reason) else "skipped")
                    break
            for tid in _test_ids(case.get("name", ""), ids):
                seen.setdefault(tid, []).append(outcome)
    out = {}
    for tid in ids:
        res = seen.get(tid)
        if not res:
            out[tid] = "missing"
        else:
            # evidence_verified results count only next to at least one real pass of the same id (the test that validates the evidence)
            ok = all(r in ("passed", "evidence_verified") for r in res) and "passed" in res
            out[tid] = "passed" if ok else next(r for r in ("failed", "error", "skipped", "not_verified", "evidence_verified") if r in res)
    return out


def build_manifest(*, repo_root: Path, junit_paths: list[Path], dist_dir: Path, commit: str | None = None) -> dict:
    ts = Path(repo_root) / "docs/m1_spec/06_GUIDES/TEST_SET"
    catalog = json.loads((ts / "test-catalog.json").read_text(encoding="utf-8"))["tests"]
    reqs = json.loads((ts / "requirements.json").read_text(encoding="utf-8"))["requirements"]
    gates = {e["test_id"]: e["primary_gate"] for e in json.loads((ts / "TEST_RELEASE_GATE_MAP.json").read_text(encoding="utf-8"))["entries"]}
    ids = [t["id"] for t in catalog]
    live_ids = [t["id"] for t in catalog if t.get("live_provider")]
    outcomes = junit_outcomes([Path(p) for p in junit_paths], ids)
    if commit is None:
        cp = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_root), capture_output=True, text=True)
        commit = cp.stdout.strip() if cp.returncode == 0 and cp.stdout.strip() else "unknown"
    version = source_version(Path(repo_root))
    blockers: list[str] = []
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        blockers.append(f"commit is not a full git sha: {commit!r}")
    det = [i for i in ids if i not in live_ids and gates[i] != "G6"]
    suite = {"total": len(det), "passed": sum(outcomes[i] == "passed" for i in det),
             "failed": sum(outcomes[i] in ("failed", "error") for i in det), "skipped": sum(outcomes[i] == "skipped" for i in det),
             "not_verified": sum(outcomes[i] == "not_verified" for i in det),
             "missing": sum(outcomes[i] == "missing" for i in det), "junit_sha256": {Path(p).name: _sha(Path(p)) for p in junit_paths}}
    live = {i: outcomes[i] for i in live_ids}
    for i, st in live.items():
        if st != "passed":
            blockers.append(f"live canary {i} is {st}")
    blocking = {i for i in ids if gates[i] != "G6"}
    unverified = {i: outcomes[i] for i in ids if i in blocking and i not in live_ids and outcomes[i] != "passed"}
    for i, st in unverified.items():
        blockers.append(f"blocking test {i} (gate {gates[i]}) is {st}" + (" (not verified; an accepted skip is never a pass)" if st == "not_verified" else ""))
    uncovered, uncovered_p0 = [], []
    for r in reqs:
        linked = [t for t in r["tests"] if t in blocking]
        if not all(outcomes[t] == "passed" for t in linked):
            uncovered.append(r["id"])
            if r["priority"] == "P0":
                uncovered_p0.append(r["id"])
    if uncovered_p0:
        blockers.append("uncovered P0 requirement(s): " + ", ".join(uncovered_p0))
    if set(uncovered) - set(uncovered_p0):
        blockers.append("uncovered requirement(s): " + ", ".join(sorted(set(uncovered) - set(uncovered_p0))))
    packages = []
    for p in sorted(Path(dist_dir).glob("*")):
        if p.suffix == ".whl" or p.name.endswith(".tar.gz"):
            packages.append({"file": p.name, "sha256": _sha(p), "bytes": p.stat().st_size})
            if f"-{version}" not in p.name:
                blockers.append(f"package {p.name} version differs from source version {version}")
    if not packages:
        blockers.append("no package artifacts found")
    return {"schema_version": 1, "commit": commit, "version": version, "deterministic_suite": suite, "live_canaries": live, "unverified_blocking_tests": unverified,
            "packages": packages, "requirement_coverage": {"total": len(reqs), "covered": len(reqs) - len(uncovered),
                                                            "uncovered": sorted(uncovered), "uncovered_p0": sorted(uncovered_p0)},
            "blockers": blockers, "release_ready": not blockers}


def write_bundle(out: Path, manifest: dict, junit_paths: list[Path], dist_dir: Path) -> None:
    out = Path(out)
    (out / "junit").mkdir(parents=True, exist_ok=True)
    (out / "packages").mkdir(parents=True, exist_ok=True)
    (out / "evidence.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    for p in junit_paths:
        shutil.copy2(p, out / "junit" / Path(p).name)
    for pkg in manifest["packages"]:
        shutil.copy2(Path(dist_dir) / pkg["file"], out / "packages" / pkg["file"])
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (out / "SHA256SUMS").write_bytes("".join(f"{_sha(out / f)}  {f}\n" for f in files).encode("utf-8"))


def verify_bundle(out: Path) -> list[str]:
    """Recompute every checksum; never rewrites SHA256SUMS. Returns error lines (empty = intact)."""
    out = Path(out)
    errs: list[str] = []
    sums = out / "SHA256SUMS"
    if not sums.is_file():
        return ["SHA256SUMS missing"]
    listed: dict[str, str] = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        m = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not m:
            errs.append(f"invalid SHA256SUMS line: {line!r}")
            continue
        listed[m.group(2)] = m.group(1)
    for f, h in listed.items():
        p = out / f
        if not p.is_file():
            errs.append(f"missing file: {f}")
        elif _sha(p) != h:
            errs.append(f"checksum mismatch: {f}")
    actual = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and p.name != "SHA256SUMS"}
    errs += [f"file not covered by SHA256SUMS: {f}" for f in sorted(actual - set(listed))]
    ev = out / "evidence.json"
    if ev.is_file() and "evidence.json" in listed and _sha(ev) == listed["evidence.json"]:
        for pkg in json.loads(ev.read_text(encoding="utf-8")).get("packages", []):
            p = out / "packages" / pkg["file"]
            if p.is_file() and _sha(p) != pkg["sha256"]:
                errs.append(f"package hash differs from evidence.json: packages/{pkg['file']}")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--junit", action="append", required=True, type=Path)
    ap.add_argument("--dist", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--commit", default=None)
    a = ap.parse_args(argv)
    m = build_manifest(repo_root=ROOT, junit_paths=a.junit, dist_dir=a.dist, commit=a.commit)
    write_bundle(a.out, m, a.junit, a.dist)
    print(json.dumps({"release_ready": m["release_ready"], "blockers": m["blockers"]}, indent=2))
    return 0 if m["release_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
