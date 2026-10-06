"""M1 release evidence bundle (REL-007): commit/version, deterministic suite result, live canaries, package hashes and a
requirement coverage summary, with SHA-256 checksums over every bundle file.

Fail closed: `release_ready` is true only if EVERY catalog test on a blocking gate (any priority; everything but G6) has a PASSED
result (failed/error/missing/skipped all block; a skip alone is never a pass).
Foreign matrix-cell skips require a real pass for the exact testcase identity
in other supplied candidate-bound evidence; archived VERIFIED claims never count.
EVERY requirement is covered, all live canaries passed, at least one package exists, and every package version equals the source version.
Soak (G6) is non-blocking and does not affect readiness.

Usage: python -m tools.release_evidence --junit J.xml [--junit ...] --dist DIR --out OUTDIR [--commit SHA]   (exit 1 if not ready)
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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_SET = ROOT / "docs/m1_spec/06_GUIDES/TEST_SET"
POLICY_REL = "docs/m1_spec/08_LIFECYCLE/gate-evidence-policy.json"
GATES_REL = "docs/m1_spec/08_LIFECYCLE/release-gates.json"
SELECTOR_SOURCE = "peerhub/cli/selector.py"
PROP = "candidate."  # JUnit <property> namespace written by stamp_junit
# Binding properties every candidate-bound JUnit must carry. `id` is the holistic identity hash: a JUnit stamped without package digests
# (gates G0-G3, produced before the build) carries the id of the identity WITHOUT package_sha256 ("source scope"); a JUnit stamped with
# --dist (G4) carries package_sha256 and the id of the FULL identity ("package scope"). The verifier recomputes the expected id for the
# scope and requires equality, so a stripped or edited property, a forged id or swapped wheels all fail. Tests of the package gate (G4)
# only count from package-scoped files.
PACKAGE_GATE = "G4"
REQUIRED_BINDING = ("source_revision", "selector_default", "policy_sha256", "gate_definition_sha256", "stamped_at", "id", "gate")


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
# Archived matrix claims are historical metadata, never current-candidate passes.
ARCHIVE_SKIP = re.compile(r"^CI-ONLY\[[^\]]*\]: VERIFIED-(CI|LOCAL)")
MATRIX_SKIP = re.compile(r"^CI-ONLY\[python=[^;\]]+;os=[^\]]+\]")


def junit_outcomes(paths: list[Path], ids: list[str]) -> dict[str, str]:
    """Aggregate exact (classname, name) identities before catalog IDs.

    The candidate-mode caller passes only accepted candidate-bound files. A foreign
    CI cell skip may be satisfied by a real pass of that exact parametrized case
    in another accepted file. Archive claims, ordinary/live skips and any failure
    cannot be converted into passes by unrelated cases sharing a catalog ID.
    """
    cases: dict[tuple[str, str], list[str]] = {}
    for p in paths:
        for case in ET.parse(p).getroot().iter("testcase"):
            outcome = "passed"
            # A malformed case with both skip and failure must still fail closed.
            if case.find("failure") is not None:
                outcome = "failed"
            elif case.find("error") is not None:
                outcome = "error"
            elif (skip := case.find("skipped")) is not None:
                reason = (skip.get("message") or skip.text or "").strip()
                outcome = ("not_verified" if ARCHIVE_SKIP.match(reason) else
                           "matrix_skip" if MATRIX_SKIP.match(reason) else
                           "not_verified" if ALLOWED_SKIP.match(reason) else "skipped")
            identity = (case.get("classname", ""), case.get("name", ""))
            cases.setdefault(identity, []).append(outcome)
    seen: dict[str, list[str]] = {}
    for (_, name), results in cases.items():
        if "passed" in results and all(result in {"passed", "matrix_skip"} for result in results):
            outcome = "passed"
        else:
            outcome = next((result for result in ("failed", "error", "skipped", "not_verified") if result in results),
                           "not_verified")
        for tid in _test_ids(name, ids):
            seen.setdefault(tid, []).append(outcome)
    out = {}
    for tid in ids:
        res = seen.get(tid)
        if not res:
            out[tid] = "missing"
        else:
            out[tid] = "passed" if all(r == "passed" for r in res) else next(
                r for r in ("failed", "error", "skipped", "not_verified") if r in res)
    return out


def selector_default(root: Path = ROOT) -> str:
    m = re.search(r"""DEFAULT_CLI_SELECTOR\s*(?::[^=]+)?=\s*["']([^"']+)["']""", (root / SELECTOR_SOURCE).read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"cannot parse DEFAULT_CLI_SELECTOR from {SELECTOR_SOURCE}")
    return m.group(1)


def _package_shas(dist_dir: Path) -> list[str]:
    return sorted(_sha(p) for p in Path(dist_dir).glob("*") if p.suffix == ".whl" or p.name.endswith(".tar.gz"))


def candidate_fields(*, commit: str, package_sha256: list[str], selector: str, policy_path: Path, gates_path: Path) -> dict:
    """Cutover candidate identity: everything that makes evidence for one candidate invalid for another."""
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    return {"source_revision": commit, "package_sha256": sorted(package_sha256), "selector_default": selector,
            "policy_schema_version": policy.get("schema_version"), "policy_sha256": _sha(Path(policy_path)),
            "gate_definition_sha256": _sha(Path(gates_path))}


def load_release_policy(release_policy: Path | None) -> tuple[int, int]:
    """(policy_ttl_seconds, max_clock_skew_seconds) from the repo-owned release policy. No defaults: missing or invalid is an error."""
    if release_policy is None:
        raise ValueError("a release policy file (--release-policy) is required in candidate mode")
    data = json.loads(Path(release_policy).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"{release_policy}: needs schema_version 1")
    out = []
    for key, low in (("policy_ttl_seconds", 1), ("max_clock_skew_seconds", 0)):
        v = data.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or v < low:
            raise ValueError(f"{release_policy}: {key} must be an integer >= {low}")
        out.append(v)
    return out[0], out[1]


def file_gate(name: str) -> str | None:
    """The gate a JUnit file is evaluated as, from its name (junit/g1.xml, g3-slow.xml -> G1, G3)."""
    m = re.match(r"g(\d+)(?![0-9])", name.lower())
    return f"G{m.group(1)}" if m else None


def scoped_fields(fields: dict, package_scope: bool) -> dict:
    return fields if package_scope else {k: v for k, v in fields.items() if k != "package_sha256"}


def candidate_id(fields: dict) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def stamp_junit(path: Path, *, commit: str, gate: str, repo_root: Path = ROOT, dist_dir: Path | None = None, policy_path: Path | None = None,
                at: datetime | None = None) -> None:
    """Bind a JUnit file to the candidate inputs known where it was produced (<property name="candidate.*">) and record when."""
    policy_path = Path(policy_path) if policy_path else Path(repo_root) / POLICY_REL
    if not re.fullmatch(r"G[0-9]", gate):
        raise ValueError(f"gate must look like G0..G9, not {gate!r}")
    props = {"gate": gate, "source_revision": commit, "selector_default": selector_default(Path(repo_root)), "policy_sha256": _sha(policy_path),
             "gate_definition_sha256": _sha(Path(repo_root) / GATES_REL),
             "stamped_at": (at or _now()).astimezone(timezone.utc).isoformat()}
    pkgs = _package_shas(dist_dir) if dist_dir is not None else []
    if dist_dir is not None:
        props["package_sha256"] = ",".join(pkgs)
    fields = candidate_fields(commit=commit, package_sha256=pkgs, selector=props["selector_default"], policy_path=policy_path,
                              gates_path=Path(repo_root) / GATES_REL)
    props["id"] = candidate_id(scoped_fields(fields, dist_dir is not None))
    tree = ET.parse(path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        raise ValueError(f"{path}: no testsuite element")
    for suite in suites:
        holder = suite.find("properties")
        if holder is None:
            holder = ET.Element("properties")
            suite.insert(0, holder)
        for k, v in props.items():
            for old in [e for e in holder.findall("property") if e.get("name") == PROP + k]:
                holder.remove(old)
            ET.SubElement(holder, "property", {"name": PROP + k, "value": v})
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _bound_props(path: Path) -> dict[str, str] | None:
    """candidate.* properties common to every testsuite in the file; None if the suites disagree or there are none."""
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    seen = [{e.get("name", "")[len(PROP):]: e.get("value", "") for e in s.findall("properties/property") if e.get("name", "").startswith(PROP)}
            for s in suites]
    if not seen or any(x != seen[0] for x in seen):
        return None
    return seen[0]


def assess_evidence(junit_paths: list[Path], cand: dict, ttl: int, skew: int, now: datetime) -> list[dict]:
    """One verdict per JUnit file: accepted | stale | mismatched | unbound | missing, with the reason and scope (source|package).
    Only `accepted` is evidence. The expected id is recomputed here from the candidate inputs of THIS run."""
    out = []
    for p in map(Path, junit_paths):
        if not p.is_file():
            out.append({"file": p.name, "status": "missing", "scope": None, "reason": "evidence file does not exist (gate did not produce it)"})
            continue
        props = _bound_props(p)
        miss = [k for k in REQUIRED_BINDING if not (props or {}).get(k)]
        if props is None or miss:
            out.append({"file": p.name, "status": "unbound", "scope": None,
                        "reason": "missing candidate binding" + (": " + ", ".join(miss) if miss else "")})
            continue
        if props["gate"] != file_gate(p.name):
            out.append({"file": p.name, "status": "mismatched", "scope": None,
                        "reason": f"stamped for gate {props['gate']} but evaluated as gate {file_gate(p.name)} (file name)"})
            continue
        pkg = "package_sha256" in props
        scope = "package" if pkg else "source"
        diff = [k for k in ("source_revision", "selector_default", "policy_sha256", "gate_definition_sha256") if props[k] != cand[k]]
        if pkg and props["package_sha256"] != ",".join(cand["package_sha256"]):
            diff.append("package_sha256")
        if props["id"] != candidate_id(scoped_fields(cand, pkg)):
            diff.append("id")
        if diff:
            out.append({"file": p.name, "status": "mismatched", "scope": scope, "reason": "bound to a different candidate: " + ", ".join(diff)})
            continue
        try:
            age = (now - datetime.fromisoformat(props["stamped_at"])).total_seconds()
        except (ValueError, TypeError):
            out.append({"file": p.name, "status": "unbound", "scope": scope, "reason": f"unparseable candidate.stamped_at {props['stamped_at']!r}"})
            continue
        if age > ttl:
            out.append({"file": p.name, "status": "stale", "scope": scope, "reason": f"evidence age {int(age)}s exceeds policy_ttl_seconds {ttl}"})
            continue
        if age < -skew:
            out.append({"file": p.name, "status": "future", "scope": scope,
                        "reason": f"stamped {int(-age)}s in the future, beyond max_clock_skew_seconds {skew}"})
            continue
        out.append({"file": p.name, "status": "accepted", "scope": scope, "reason": "current and bound to the candidate"})
    return out


def build_manifest(*, repo_root: Path, junit_paths: list[Path], dist_dir: Path, commit: str | None = None, candidate: bool = False,
                   policy_path: Path | None = None, release_policy: Path | None = None, now: datetime | None = None) -> dict:
    ts = Path(repo_root) / "docs/m1_spec/06_GUIDES/TEST_SET"
    catalog = json.loads((ts / "test-catalog.json").read_text(encoding="utf-8"))["tests"]
    reqs = json.loads((ts / "requirements.json").read_text(encoding="utf-8"))["requirements"]
    gates = {e["test_id"]: e["primary_gate"] for e in json.loads((ts / "TEST_RELEASE_GATE_MAP.json").read_text(encoding="utf-8"))["entries"]}
    ids = [t["id"] for t in catalog]
    live_ids = [t["id"] for t in catalog if t.get("live_provider")]
    if commit is None:
        cp = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_root), capture_output=True, text=True)
        commit = cp.stdout.strip() if cp.returncode == 0 and cp.stdout.strip() else "unknown"
    version = source_version(Path(repo_root))
    cand_block: dict | None = None
    bindings: list[dict] = []
    holds: list[str] = []
    used_junit = [Path(p) for p in junit_paths]
    pol: dict = {}
    if candidate:
        pol_path = Path(policy_path) if policy_path else Path(repo_root) / POLICY_REL
        pol = json.loads(pol_path.read_text(encoding="utf-8"))
        fields = candidate_fields(commit=commit, package_sha256=_package_shas(dist_dir), selector=selector_default(Path(repo_root)),
                                  policy_path=pol_path, gates_path=Path(repo_root) / GATES_REL)
        cid = candidate_id(fields)
        cand_block = {**fields, "candidate_id": cid}
        bindings = assess_evidence(used_junit, fields, *load_release_policy(release_policy), now or _now())
        action = str(pol.get("on_stale", "HOLD"))
        holds += [f"{action}: evidence {b['file']} is {b['status']}: {b['reason']}" for b in bindings if b["status"] != "accepted"]
        pkg_junit = [p for p, b in zip(used_junit, bindings) if b["status"] == "accepted" and b["scope"] == "package" and file_gate(p.name) == PACKAGE_GATE]
        used_junit = [p for p, b in zip(used_junit, bindings) if b["status"] == "accepted"]
        if not pkg_junit:
            holds.append(f"HOLD: no current JUnit carries the package digests of the dist being released (gate {PACKAGE_GATE} evidence)")
    outcomes = junit_outcomes(used_junit, ids)
    if candidate:  # package-gate tests are evidence only from files bound to the released package digests
        pkg_out = junit_outcomes(pkg_junit, ids)
        outcomes = {i: (pkg_out[i] if gates[i] == PACKAGE_GATE else outcomes[i]) for i in ids}
    blockers: list[str] = list(holds)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        blockers.append(f"commit is not a full git sha: {commit!r}")
    det = [i for i in ids if i not in live_ids and gates[i] != "G6"]
    suite = {"total": len(det), "passed": sum(outcomes[i] == "passed" for i in det),
             "failed": sum(outcomes[i] in ("failed", "error") for i in det), "skipped": sum(outcomes[i] == "skipped" for i in det),
             "not_verified": sum(outcomes[i] == "not_verified" for i in det),
             "missing": sum(outcomes[i] == "missing" for i in det), "junit_sha256": {Path(p).name: _sha(Path(p)) for p in junit_paths if Path(p).is_file()}}
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
        linked = [t for t in r.get("tests") or [] if t in blocking]
        if not r.get("tests") or not all(outcomes[t] == "passed" for t in linked):  # no linked tests at all = uncovered, never a crash
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
    if candidate:
        valid = set(pol["normalized_states"]["valid"])
        for g in [g["id"] for g in json.loads((Path(repo_root) / GATES_REL).read_text(encoding="utf-8"))["gates"] if g.get("blocking")]:
            gids = [i for i in ids if gates[i] == g]
            if gids and not all(outcomes[i] == "passed" and "PASS" in valid for i in gids):
                blockers.append(f"HOLD: blocking gate {g} has no complete current PASS evidence bound to the candidate (every test of the gate must pass)")
    extra = {"candidate": cand_block, "evidence_bindings": bindings, "hold_reasons": [b for b in blockers if b.startswith(("HOLD", "REVERIFY"))]} if candidate else {}
    return {**extra, "schema_version": 1, "commit": commit, "version": version, "deterministic_suite": suite, "live_canaries": live, "unverified_blocking_tests": unverified,
            "packages": packages, "requirement_coverage": {"total": len(reqs), "covered": len(reqs) - len(uncovered),
                                                            "uncovered": sorted(uncovered), "uncovered_p0": sorted(uncovered_p0)},
            "blockers": blockers, "release_ready": not blockers}


def write_bundle(out: Path, manifest: dict, junit_paths: list[Path], dist_dir: Path) -> None:
    out = Path(out)
    (out / "junit").mkdir(parents=True, exist_ok=True)
    (out / "packages").mkdir(parents=True, exist_ok=True)
    (out / "evidence.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    for p in junit_paths:
        if Path(p).is_file():
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
    ap.add_argument("--junit", action="append", type=Path)
    ap.add_argument("--dist", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--commit", default=None)
    ap.add_argument("--candidate", action="store_true", help="bind to the cutover candidate identity and enforce freshness from the policy file")
    ap.add_argument("--policy-file", type=Path, default=None, help="gate evidence policy (default: the spec's gate-evidence-policy.json)")
    ap.add_argument("--gate", default=None, help="gate id (G0..G4) the stamped JUnit files belong to; evidence is accepted only for the gate its file name says")
    ap.add_argument("--release-policy", type=Path, default=None, help="repo-owned release policy with policy_ttl_seconds (required with --candidate)")
    ap.add_argument("--stamp-junit", type=Path, action="append", default=None,
                    help="bind this JUnit file to the candidate inputs (needs --commit; repeatable) and exit; a missing file is skipped with a warning")
    a = ap.parse_args(argv)
    if a.stamp_junit is not None:
        if not a.commit or not a.gate:
            ap.error("--stamp-junit requires --commit and --gate")
        for f in a.stamp_junit:
            if not f.is_file():
                print(f"::warning::{f} was not produced; it cannot become evidence (the gate will HOLD)", file=sys.stderr)
                continue
            stamp_junit(f, commit=a.commit, gate=a.gate, dist_dir=a.dist, policy_path=a.policy_file)
        return 0
    if not (a.junit and a.dist and a.out):
        ap.error("--junit, --dist and --out are required")
    if a.candidate and a.release_policy is None:
        ap.error("--candidate requires --release-policy")
    try:
        m = build_manifest(repo_root=ROOT, junit_paths=a.junit, dist_dir=a.dist, commit=a.commit, candidate=a.candidate, policy_path=a.policy_file,
                           release_policy=a.release_policy)
    except ValueError as e:
        ap.error(str(e))
    write_bundle(a.out, m, a.junit, a.dist)
    print(json.dumps({"release_ready": m["release_ready"], "blockers": m["blockers"]}, indent=2))
    return 0 if m["release_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
