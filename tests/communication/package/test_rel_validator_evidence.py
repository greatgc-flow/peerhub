"""Wave 7: REL-004 (docs/version/schema/catalog consistency), REL-013 (tamper detection), REL-014 (validator stdout == evidence file).

The validator is always run on a COPY of the spec package under pytest's temp root: it rewrites PACKAGE_VALIDATION.txt, and the
tracked file must not be touched by tests."""
import hashlib
import json
import shutil
import sys

import pytest

from tests.communication.harness import pkg_env
from tests.communication.harness.pkg_env import REPO

pytestmark = [pytest.mark.package, pytest.mark.release, pytest.mark.timeout(600)]

SPEC = REPO / "docs/m1_spec"
ENV = {"PYTHONIOENCODING": "utf-8"}


@pytest.fixture
def pkg_copy(tmp_path):
    dst = tmp_path / "m1_spec_copy"
    shutil.copytree(SPEC, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def validate(pkg):
    cp = pkg_env.run([sys.executable, str(pkg / "tools/validate_package.py")], cwd=pkg.parent, env=pkg_env.clean_env(ENV))
    return cp


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def flip_one_byte(p):
    b = bytearray(p.read_bytes())
    i = next(k for k, c in enumerate(b) if chr(c) in "abcdefghijklmnop")  # a printable ASCII letter: no control chars, still valid UTF-8
    b[i] = ord("Z") if b[i] != ord("Z") else ord("Y")
    p.write_bytes(bytes(b))


# ----------------------------------------------------------------------------------------------------- REL-004
def test_rel_004_validator_passes_on_the_package_and_generated_views_have_not_drifted(pkg_copy):
    cp = validate(pkg_copy)
    assert cp.returncode == 0 and "RESULT=PASS" in cp.stdout.splitlines(), cp.stdout[-2000:]
    for line in ("legacy_commands=109", "m1_tests=210", "gate_mapped_tests=210"):
        assert line in cp.stdout.splitlines(), line
    def view(n):  # generate_docs.py writes via write_text: newline translation is platform behaviour, not drift
        return (pkg_copy / "03_STANDARDS" / n).read_bytes().replace(b"\r\n", b"\n").strip()

    before = {n: view(n) for n in ("STANDARDS_DECISION_TABLE.md",)}  # ORIGINAL_LINKS.md has pre-existing drift: Q-W7-5 (OWNER)
    gen = pkg_env.run([sys.executable, str(pkg_copy / "tools/generate_docs.py")], cwd=pkg_copy.parent, env=pkg_env.clean_env(ENV))
    assert gen.returncode == 0, gen.stderr
    assert {n: view(n) for n in before} == before  # regenerated views are identical (modulo platform newlines)
    # positive control: the drift check does fire when a generated view is edited
    (pkg_copy / "03_STANDARDS/STANDARDS_DECISION_TABLE.md").write_text("drifted\n", encoding="utf-8")
    bad = validate(pkg_copy)
    assert bad.returncode == 1 and "STANDARDS_DECISION_TABLE.md drifted from standards_registry.json" in bad.stdout


def test_rel_004_source_version_matches_built_metadata_and_schema_copies_match_the_spec(dist):
    ver = pkg_env.source_version()
    assert pkg_env.wheel_text(dist.wheel, ".dist-info/METADATA").count(f"\nVersion: {ver}\n") == 1
    spec = SPEC / "04_SCHEMAS"
    for pkg, names in (("core", ("offset", "peer", "record", "stream")), ("extensions", ("peer-observation", "resource-pool"))):
        for n in names:
            assert sha(REPO / f"peerhub/{pkg}/schemas/{n}.schema.json") == sha(spec / f"{n}.schema.json"), (pkg, n)  # no source/spec drift
    cat = json.loads((SPEC / "06_GUIDES/TEST_SET/test-catalog.json").read_text(encoding="utf-8"))
    assert cat["count"] == len(cat["tests"]) == 210


# ----------------------------------------------------------------------------------------------------- REL-013
def _tamper_flip(pkg):
    flip_one_byte(pkg / "02_EXTENSIONS/EXTENSION_CATALOG.md")
    return "02_EXTENSIONS/EXTENSION_CATALOG.md"


def _tamper_append(pkg):
    with open(pkg / "01_M1/MIGRATION_CUTOVER.md", "ab") as f:
        f.write(b"x")
    return "01_M1/MIGRATION_CUTOVER.md"


def _tamper_delete(pkg):
    (pkg / "01_M1/BOUNDARY_MAP.md").unlink()
    return "01_M1/BOUNDARY_MAP.md"


def _tamper_json(pkg):
    p = pkg / "08_LIFECYCLE/release-gates.json"
    p.write_bytes(p.read_bytes().replace(b'"G0"', b'"GZ"', 1))
    return "08_LIFECYCLE/release-gates.json"


@pytest.mark.parametrize("tamper", [_tamper_flip, _tamper_append, _tamper_delete, _tamper_json], ids=lambda f: f.__name__[8:])
def test_rel_013_tampered_package_file_fails_validation_and_names_the_file_without_rewriting_baselines(pkg_copy, tamper):
    clean = validate(pkg_copy)
    assert clean.returncode == 0, clean.stdout[-1500:]  # positive control: the untampered copy passes
    baseline = {n: (pkg_copy / n).read_bytes() for n in ("MANIFEST.json", "SHA256SUMS.txt")}
    rel = tamper(pkg_copy)
    bad = validate(pkg_copy)
    assert bad.returncode == 1
    lines = bad.stdout.splitlines()
    assert "RESULT=FAIL" in lines and "RESULT=PASS" not in lines
    errors = [ln for ln in lines if ln.startswith("ERROR: ")]
    assert any(rel in e for e in errors), errors  # the mismatched file is named
    assert any(("Manifest" in e or "Checksum" in e) for e in errors if rel in e), errors
    assert {n: (pkg_copy / n).read_bytes() for n in baseline} == baseline  # baseline hashes are never rewritten to make tampering pass


def test_rel_013_tampering_the_checksum_baseline_itself_is_also_detected(pkg_copy):
    sums = pkg_copy / "SHA256SUMS.txt"
    lines = sums.read_text(encoding="utf-8").splitlines()
    h, name = lines[0].split("  ", 1)
    lines[0] = ("0" if h[0] != "0" else "1") + h[1:] + "  " + name
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bad = validate(pkg_copy)
    assert bad.returncode == 1 and f"ERROR: Checksum mismatch: {name}" in bad.stdout.splitlines()


# ----------------------------------------------------------------------------------------------------- REL-014
def _norm(text):
    return [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip() != ""]


def test_rel_014_validator_stdout_equals_persisted_evidence_for_pass_and_fail(pkg_copy):
    evidence = pkg_copy / "PACKAGE_VALIDATION.txt"
    evidence.write_text("STALE EVIDENCE\n", encoding="utf-8")
    ok = validate(pkg_copy)
    assert ok.returncode == 0
    assert _norm(evidence.read_text(encoding="utf-8")) == _norm(ok.stdout)  # line-for-line, including RESULT
    assert _norm(ok.stdout)[-1] == "RESULT=PASS" and "STALE EVIDENCE" not in evidence.read_text(encoding="utf-8")
    flip_one_byte(pkg_copy / "02_EXTENSIONS/EXTENSION_CATALOG.md")
    flip_one_byte(pkg_copy / "01_M1/BOUNDARY_MAP.md")
    evidence.write_text("STALE EVIDENCE\n", encoding="utf-8")
    bad = validate(pkg_copy)
    assert bad.returncode == 1
    out, persisted = _norm(bad.stdout), _norm(evidence.read_text(encoding="utf-8"))
    assert persisted == out and "RESULT=FAIL" in out
    errors = [ln for ln in out if ln.startswith("ERROR: ")]
    assert len(errors) >= 4 and [ln for ln in persisted if ln.startswith("ERROR: ")] == errors  # every ERROR persisted, same order
    assert all(any(p in e for e in errors) for p in ("EXTENSION_CATALOG.md", "BOUNDARY_MAP.md"))
