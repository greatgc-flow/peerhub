"""Wave 7 release-gate integration (REL-004 consistency family, REL-006 DAG): TEST_RELEASE_GATE_MAP.json maps all 210 catalog ids to
exactly one gate, and release-gates.json is an acyclic, fail-closed DAG (G5 publish requires every blocking ancestor)."""
import json
import re

import pytest

from tests.communication.harness.pkg_env import REPO

pytestmark = [pytest.mark.package, pytest.mark.release, pytest.mark.traceability]

TS = REPO / "docs/m1_spec/06_GUIDES/TEST_SET"
GATES_JSON = REPO / "docs/m1_spec/08_LIFECYCLE/release-gates.json"
GATE_COUNTS = {"G0": 77, "G1": 43, "G2": 68, "G3": 6, "G4": 14, "G6": 2}  # literal, from TEST_RELEASE_GATE_MATRIX.md table
ALLOWED_BY_TIER = {  # literal copy of the rule table in docs/m1_spec/tools/validate_package.py
    "architecture": {"G0"}, "schema": {"G0"}, "unit": {"G0"}, "property": {"G0"}, "meta": {"G0"}, "security": {"G0"},
    "migration": {"G1"}, "e2e": {"G2"}, "live": {"G3"}, "package": {"G4"}, "soak": {"G6"},
    "integration": {"G1", "G2"}, "concurrency": {"G1", "G2"}, "fault": {"G1", "G2"}}
VALID_ON_FAIL = {"FIX", "HOLD", "ROLLBACK_OR_HOLD", "OBSERVE"}


def _json(p):
    return json.loads(p.read_text(encoding="utf-8"))


def catalog():
    return _json(TS / "test-catalog.json")["tests"]


def gate_entries():
    return _json(TS / "TEST_RELEASE_GATE_MAP.json")["entries"]


def gates():
    return {g["id"]: g for g in _json(GATES_JSON)["gates"]}


def find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Iterative DFS with colours; returns one cycle path or None."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {n: WHITE for n in graph}
    for root in graph:
        if colour[root] != WHITE:
            continue
        stack = [(root, iter(graph[root]))]
        path = [root]
        colour[root] = GREY
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                colour[node] = BLACK
                stack.pop()
                path.pop()
            elif nxt not in graph:
                raise KeyError(f"{node} depends on unknown gate {nxt}")
            elif colour[nxt] == GREY:
                return path[path.index(nxt):] + [nxt]
            elif colour[nxt] == WHITE:
                colour[nxt] = GREY
                path.append(nxt)
                stack.append((nxt, iter(graph[nxt])))
    return None


def ancestors(graph, node):
    out, stack = set(), list(graph[node])
    while stack:
        n = stack.pop()
        if n not in out:
            out.add(n)
            stack.extend(graph[n])
    return out


def publishable(graph, blocking, results, target="G5"):
    """Fail closed: a gate may pass only if every blocking ancestor has an explicit 'pass'; missing evidence = not passed."""
    return all(results.get(a) == "pass" for a in ancestors(graph, target) if a in blocking)


# ------------------------------------------------------------------------------------------ mapping
def test_rel_004_gate_map_covers_every_catalog_id_exactly_once_with_consistent_metadata():
    cat, entries = catalog(), gate_entries()
    assert len(cat) == 210 and len(entries) == 210
    ids = [t["id"] for t in cat]
    mapped = [e["test_id"] for e in entries]
    assert len(set(ids)) == 210 and sorted(mapped) == sorted(ids)  # exactly once, nothing extra, nothing missing
    by = {t["id"]: t for t in cat}
    for e in entries:
        t = by[e["test_id"]]
        assert (e["priority"], e["tier"]) == (t["priority"], t["tier"]), e["test_id"]
        assert e["primary_gate"] in ALLOWED_BY_TIER[t["tier"]], (e["test_id"], e["primary_gate"], t["tier"])
    counts = {}
    for e in entries:
        counts[e["primary_gate"]] = counts.get(e["primary_gate"], 0) + 1
    assert counts == GATE_COUNTS and sum(GATE_COUNTS.values()) == 210
    # live/soak/package tiers land on exactly their own gates (positive structure checks, not just membership)
    assert {e["primary_gate"] for e in entries if e["tier"] == "live"} == {"G3"}
    assert {e["primary_gate"] for e in entries if e["tier"] == "package"} == {"G4"}
    assert {e["primary_gate"] for e in entries if e["tier"] == "soak"} == {"G6"}


def test_rel_004_human_matrix_equals_the_machine_gate_map():
    text = (TS / "TEST_RELEASE_GATE_MATRIX.md").read_text(encoding="utf-8")
    md = {}
    for gate, body in re.findall(r"^### (G\d)\n((?:`[^`\n]+`(?:, )?)+)", text, re.M):
        md[gate] = re.findall(r"`([^`]+)`", body)
    machine = {}
    for e in gate_entries():
        machine.setdefault(e["primary_gate"], []).append(e["test_id"])
    assert {g: sorted(v) for g, v in md.items()} == {g: sorted(v) for g, v in machine.items()}
    assert {g: len(v) for g, v in md.items()} == GATE_COUNTS


def test_rel_004_every_catalog_id_belongs_to_exactly_one_implementation_wave():
    from tools.traceability import wave_of

    waves = _json(REPO / "docs/m1_impl/waves.json")["waves"]
    ids = [t["id"] for t in catalog()]
    assigned = {i: wave_of(i, waves) for i in ids}
    assert all(w is not None for w in assigned.values()), [i for i, w in assigned.items() if w is None]
    assert sorted(i for i, w in assigned.items() if w == 7) == sorted(
        ["REL-001", "REL-002", "REL-003", "REL-004", "REL-005", "REL-006", "REL-007", "REL-008", "REL-009", "REL-010", "REL-011",
         "REL-012", "REL-013", "REL-014", "MIG-003", "IMP-001", "IMP-002", "IMP-003", "IMP-004"])  # literal wave-7 scope (19 ids)
    # every package-tier / G4 test id is in wave 7 (the package gate is closed by this wave)
    g4 = [e["test_id"] for e in gate_entries() if e["primary_gate"] == "G4"]
    assert all(assigned[i] == 7 for i in g4) and len(g4) == 14


# ------------------------------------------------------------------------------------------ DAG
def test_rel_006_gate_dag_is_acyclic_complete_and_fail_closed():
    g = gates()
    graph = {k: list(v["depends_on"]) for k, v in g.items()}
    assert set(graph) == {"G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"}
    assert find_cycle(graph) is None
    blocking = {k for k, v in g.items() if v["blocking"]}
    assert blocking == {"G0", "G1", "G2", "G3", "G4", "G5", "G7"} and not g["G6"]["blocking"]
    assert ancestors(graph, "G5") >= {"G0", "G1", "G2", "G3", "G4", "G7"} and "G6" not in ancestors(graph, "G5")
    for k in blocking:
        assert g[k]["on_fail"] in VALID_ON_FAIL - {"OBSERVE"}, k  # a blocking gate never merely observes
    assert g["G5"]["on_fail"] in {"HOLD", "ROLLBACK_OR_HOLD"} and g["G7"]["on_fail"] == "ROLLBACK_OR_HOLD"
    # every P0 test is on a blocking gate; the non-blocking soak gate holds no P0 test
    assert all(e["primary_gate"] in blocking for e in gate_entries() if e["priority"] == "P0")
    # fail-closed simulation over the real DAG
    allpass = {k: "pass" for k in graph}
    assert publishable(graph, blocking, allpass) is True  # positive control
    for failing in sorted(ancestors(graph, "G5") & blocking):
        assert publishable(graph, blocking, {**allpass, failing: "fail"}) is False, failing
        missing = {k: v for k, v in allpass.items() if k != failing}  # absent evidence is not a pass
        assert publishable(graph, blocking, missing) is False, failing
    assert publishable(graph, blocking, {**allpass, "G6": "fail"}) is True  # soak failure is observed, not blocking


def test_rel_006_cycle_and_wiring_defects_are_detected_by_the_checkers():
    assert find_cycle({"A": ["B"], "B": ["C"], "C": ["A"]}) == ["A", "B", "C", "A"]
    assert find_cycle({"A": [], "B": ["A"]}) is None
    with pytest.raises(KeyError):
        find_cycle({"A": ["Z"]})
    graph = {k: list(v["depends_on"]) for k, v in gates().items()}
    broken = {**graph, "G5": [d for d in graph["G5"] if d != "G7"]}  # publish no longer behind the invariant gate
    blocking = {"G0", "G1", "G2", "G3", "G4", "G5", "G7"}
    allpass = {k: "pass" for k in graph}
    assert publishable(broken, blocking, {**allpass, "G7": "fail"}) is True  # the broken wiring would publish: the checks above catch it
    assert publishable(graph, blocking, {**allpass, "G7": "fail"}) is False
