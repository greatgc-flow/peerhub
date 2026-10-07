import pytest

from tests.communication.spec import TESTSET, catalog, catalog_ids, load

pytestmark = [pytest.mark.meta, pytest.mark.traceability]


@pytest.mark.catalog_id("META-001")
def test_meta_001_requirement_dimension_coverage_complete():
    reqs = load(TESTSET / "requirements.json")["requirements"]
    tests = catalog()
    by_req: dict[str, list[dict]] = {}
    for t in tests:
        for r in t["requirements"]:
            by_req.setdefault(r, []).append(t)
    known = {r["id"] for r in reqs}
    assert not (set(by_req) - known), f"tests reference unknown requirements: {sorted(set(by_req) - known)}"
    problems = []
    for r in reqs:
        linked = by_req.get(r["id"], [])
        dims = {d for t in linked for d in t["dimensions"]}
        uncovered = set(r["required_dimensions"]) - dims
        if uncovered:
            problems.append((r["id"], sorted(uncovered)))
        if sorted(r["tests"]) != sorted(t["id"] for t in linked):
            problems.append((r["id"], "declared tests != reverse index"))
    assert not problems, problems


@pytest.mark.catalog_id("META-002")
def test_meta_002_state_machine_transitions_covered_or_deferred():
    ids = catalog_ids()
    machines = load(TESTSET / "STATE_MACHINE_COVERAGE.json")["machines"]
    problems, seen = [], set()
    for m in machines:
        for tr in m["transitions"]:
            if tr["id"] in seen:
                problems.append((tr["id"], "duplicate id"))
            seen.add(tr["id"])
            st = tr["status"]
            if st == "COVERED":
                if not tr["tests"] or set(tr["tests"]) - ids:
                    problems.append((tr["id"], "COVERED without existing tests"))
            elif st == "DEFERRED":
                if not tr.get("trigger", "").strip():
                    problems.append((tr["id"], "DEFERRED without trigger"))
            else:
                problems.append((tr["id"], f"non-terminal status {st}"))
    assert seen and not problems, problems


@pytest.mark.catalog_id("META-003")
def test_meta_003_exception_catalog_closed():
    ids = catalog_ids()
    cat = load(TESTSET / "EXCEPTION_CATALOG.json")
    allowed = set(cat["allowed_status"])
    assert allowed == {"COVERED", "DEFERRED", "N_A", "OUT_OF_SCOPE"}
    problems, seen = [], set()
    for e in cat["exceptions"]:
        if e["id"] in seen:
            problems.append((e["id"], "duplicate id"))
        seen.add(e["id"])
        if not e.get("category", "").strip() or not e.get("case", "").strip():
            problems.append((e["id"], "unclassified"))
        st = e["status"]
        if st not in allowed:
            problems.append((e["id"], f"bad status {st}"))
        elif st == "COVERED":
            if not e["tests"] or set(e["tests"]) - ids:
                problems.append((e["id"], "COVERED without existing tests"))
        elif st == "DEFERRED":
            if not e.get("trigger", "").strip() or not e.get("rationale", "").strip():
                problems.append((e["id"], "DEFERRED needs rationale+trigger"))
        elif not e.get("rationale", "").strip():
            problems.append((e["id"], f"{st} needs rationale"))
    assert seen and not problems, problems


@pytest.mark.catalog_id("META-004")
def test_meta_004_interaction_matrix_fully_covered():
    ids = catalog_ids()
    rows = load(TESTSET / "INTERACTION_MATRIX.json")["interactions"]
    problems, seen_id, seen_pair = [], set(), set()
    for r in rows:
        pair = (r["a"], r["b"])
        if r["id"] in seen_id or pair in seen_pair:
            problems.append((r["id"], "duplicate id/pair"))
        seen_id.add(r["id"])
        seen_pair.add(pair)
        if r["status"] == "OPEN":
            problems.append((r["id"], "OPEN"))
        if r["status"] == "REQUIRED" and (not r["tests"] or set(r["tests"]) - ids):
            problems.append((r["id"], "REQUIRED without existing tests"))
    assert rows and not problems, problems
