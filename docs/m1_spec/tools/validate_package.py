from pathlib import Path
import csv, hashlib, json, re, sys
from jsonschema.validators import Draft202012Validator

root = Path(__file__).resolve().parents[1]
log = []
errors = []
def emit(x):
    print(x); log.append(x)

emit("PACKAGE=" + root.name)

# no global AGENTS.md
if any(p.name.lower()=="agents.md" for p in root.rglob("*") if p.is_file()):
    errors.append("AGENTS.md is intentionally prohibited in final packages.")

# parse JSON and validate JSON Schemas
schemas = {}
for p in sorted(root.rglob("*.schema.json")):
    try:
        s=json.loads(p.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(s)
        schemas[p.name]=s
    except Exception as e:
        errors.append(f"schema invalid: {p.relative_to(root)}: {e}")
for p in sorted(root.rglob("*.json")):
    try:
        json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        errors.append(f"json invalid: {p.relative_to(root)}: {e}")

# Standards registry source completeness
std_dir = root/"02_STANDARDS" if (root/"02_STANDARDS").exists() else root/"03_STANDARDS"
reg = json.loads((std_dir/"standards_registry.json").read_text(encoding="utf-8"))
stds = reg["standards"]
emit(f"standards={len(stds)}")
allowed_general={"ADOPT","BOUNDARY_ONLY","CONDITIONAL","REFERENCE_ONLY","DEFER","EXPORT_ONLY","FUTURE_RUNTIME_ADAPTER"}
for x in stds:
    if not x.get("official_urls"):
        errors.append("standard missing official_urls: "+x.get("id","?"))
    for u in x.get("official_urls",[]):
        if not re.match(r"^https://",u):
            errors.append(f"non-https official URL: {u}")
    for k in ["general_decision","peerhub_decision"]:
        if x.get(k) not in allowed_general:
            errors.append(f"invalid decision {x.get(k)} for {x.get('id')}")

# Ensure generated standards view exactly matches generator output by regenerating in memory-ish:
key="general_decision" if (root/"02_STANDARDS").exists() else "peerhub_decision"
expected=[
"# 표준·OSS 적용 결정표","",
"> 이 문서는 `standards_registry.json`에서 생성되는 사람용 View입니다. JSON이 SSOT입니다.","",
"| 표준/기술 | 검증 버전/상태 | 결정 | 적용 경계 | 가지치기 원칙 | 원본 링크 |",
"|---|---|---|---|---|---|"]
for x in stds:
    expected.append(f"| {x['name']} | {x['version']} / {x['status']} | **{x[key]}** | {x['use']} | {x['prune']} | {'<br>'.join(x['official_urls'])} |")
actual=(std_dir/"STANDARDS_DECISION_TABLE.md").read_text(encoding="utf-8").strip()
if actual != "\n".join(expected).strip():
    errors.append("STANDARDS_DECISION_TABLE.md drifted from standards_registry.json")

# M1 test-set completeness / recursive-MECE traceability
test_dir=root/"06_GUIDES"/"TEST_SET"
if test_dir.exists():
    req_obj=json.loads((test_dir/"requirements.json").read_text(encoding="utf-8"))
    test_obj=json.loads((test_dir/"test-catalog.json").read_text(encoding="utf-8"))
    req_schema=json.loads((test_dir/"requirements.schema.json").read_text(encoding="utf-8"))
    test_schema=json.loads((test_dir/"test-catalog.schema.json").read_text(encoding="utf-8"))
    for label,obj,schema in [("requirements",req_obj,req_schema),("test-catalog",test_obj,test_schema)]:
        verr=list(Draft202012Validator(schema).iter_errors(obj))
        if verr: errors.append(f"{label} schema validation failed: {verr[0].message}")
    reqs={x["id"]:x for x in req_obj.get("requirements",[])}
    tests=test_obj.get("tests",[])
    emit(f"m1_requirements={len(reqs)}")
    emit(f"m1_tests={len(tests)}")
    if req_obj.get("schema_version") != 2 or test_obj.get("schema_version") != 2:
        errors.append("M1 test schemas must use version 2 recursive-MECE model")
    if len(reqs) < 1 or len(tests) < 1: errors.append("M1 test catalog is empty")
    if test_obj.get("count") != len(tests): errors.append("test-catalog count drift")
    if len({t.get("id") for t in tests}) != len(tests): errors.append("Duplicate test id")
    coverage={rid:[] for rid in reqs}
    dim_coverage={rid:set() for rid in reqs}
    for t in tests:
        for field in ["id","tier","title","requirements","setup","action","oracle","priority","dimensions","case_type"]:
            if not t.get(field): errors.append(f"Incomplete test field {field}: {t.get('id')}")
        if t.get("live_provider") and t.get("deterministic"):
            errors.append(f"Live provider test cannot be deterministic: {t.get('id')}")
        dims=set(t.get("dimensions",[]))
        expected_type = "meta" if t.get("tier")=="meta" else ("fault" if (t.get("tier")=="fault" or "fault" in dims or "crash" in dims) else ("negative" if "negative" in dims else ("boundary" if "boundary" in dims else "positive")))
        if t.get("case_type") != expected_type:
            errors.append(f"case_type/dimensions drift in {t.get('id')}: {t.get('case_type')} != {expected_type}")
        for rid in t.get("requirements",[]):
            if rid not in reqs: errors.append(f"Unknown requirement {rid} in {t.get('id')}")
            else:
                coverage[rid].append(t.get("id")); dim_coverage[rid].update(t.get("dimensions",[]))
    uncovered=[rid for rid,x in coverage.items() if not x]
    if uncovered: errors.append("Uncovered M1 requirements: "+",".join(uncovered))
    for rid,r in reqs.items():
        declared=r.get("tests",[])
        if declared != coverage[rid]: errors.append(f"Requirement coverage drift: {rid}")
        missing=sorted(set(r.get("required_dimensions",[]))-dim_coverage[rid])
        if missing: errors.append(f"Requirement dimension coverage missing {rid}: {missing}")
    emit("required_dimension_uncovered="+str(sum(bool(set(r.get('required_dimensions',[]))-dim_coverage[rid]) for rid,r in reqs.items())))
    p0_uncovered=[rid for rid,r in reqs.items() if r.get("priority")=="P0" and not coverage[rid]]
    if p0_uncovered: errors.append("Uncovered P0 requirements: "+",".join(p0_uncovered))

    # Meta-inventory schema validation
    meta_specs=[
        ("state", "STATE_MACHINE_COVERAGE.json", "state-machine-coverage.schema.json"),
        ("exceptions", "EXCEPTION_CATALOG.json", "exception-catalog.schema.json"),
        ("interactions", "INTERACTION_MATRIX.json", "interaction-matrix.schema.json"),
        ("recursive-mece", "RECURSIVE_MECE_MODEL.json", "recursive-mece-model.schema.json"),
    ]
    for label,obj_name,schema_name in meta_specs:
        obj=json.loads((test_dir/obj_name).read_text(encoding="utf-8"))
        schema=json.loads((test_dir/schema_name).read_text(encoding="utf-8"))
        verr=list(Draft202012Validator(schema).iter_errors(obj))
        if verr: errors.append(f"{label} schema validation failed: {verr[0].message}")

    # Requirement source traceability + P0 dimension strength
    all_test_ids={t["id"] for t in tests}
    for rid,r in reqs.items():
        for token in [x.strip() for x in r.get("source","").split(";") if x.strip()]:
            # requirement sources are repo-relative file/glob paths
            matches=list(root.glob(token)) if any(ch in token for ch in "*?[") else [root/token]
            if not matches or not any(p.exists() for p in matches): errors.append(f"Requirement source not found {rid}: {token}")
        if r.get("priority")=="P0":
            linked=[t for t in tests if rid in t.get("requirements",[])]
            for dim in r.get("required_dimensions",[]):
                carriers=[t for t in linked if dim in t.get("dimensions",[])]
                if not carriers: continue
                if dim != "live" and not any(t.get("priority")=="P0" and t.get("deterministic") for t in carriers):
                    errors.append(f"P0 dimension lacks deterministic P0 evidence {rid}/{dim}")
                if dim == "live" and not any(t.get("priority")=="P0" and t.get("live_provider") for t in carriers):
                    errors.append(f"P0 live dimension lacks live P0 evidence {rid}")
    titles=[t.get("title") for t in tests]
    if len(set(titles)) != len(titles): errors.append("Duplicate test title")
    for t in tests:
        if t.get("tier") not in t.get("markers",[]): errors.append(f"Test tier marker missing: {t.get('id')} / {t.get('tier')}")

    # State-machine closure
    state=json.loads((test_dir/"STATE_MACHINE_COVERAGE.json").read_text(encoding="utf-8"))
    open_state=[]
    for machine in state.get("machines",[]):
        ids=set()
        for tr in machine.get("transitions",[]):
            if tr["id"] in ids: errors.append(f"Duplicate state transition id in {machine['name']}: {tr['id']}")
            ids.add(tr["id"])
            if tr.get("status") not in {"COVERED","DEFERRED","N_A"}: open_state.append(tr.get("id"))
            if tr.get("status")=="COVERED":
                if not tr.get("tests"): errors.append(f"Covered state transition lacks tests: {tr['id']}")
                for tid in tr.get("tests",[]):
                    if tid not in {t['id'] for t in tests}: errors.append(f"State transition unknown test {tid}")
            if tr.get("status")=="DEFERRED" and not tr.get("trigger"): errors.append(f"Deferred state transition lacks trigger: {tr['id']}")
    emit(f"open_state_transitions={len(open_state)}")
    if open_state: errors.append("Open state transitions: "+",".join(open_state))

    # Exception-space closure
    exc=json.loads((test_dir/"EXCEPTION_CATALOG.json").read_text(encoding="utf-8"))
    allowed=set(exc.get("allowed_status",[])); seen=set(); open_exc=[]
    for x in exc.get("exceptions",[]):
        if x["id"] in seen: errors.append("Duplicate exception id: "+x["id"])
        seen.add(x["id"])
        if x.get("status") not in allowed: open_exc.append(x["id"])
        if x.get("status")=="COVERED":
            if not x.get("tests"): errors.append("Covered exception lacks test: "+x["id"])
            for tid in x.get("tests",[]):
                if tid not in {t['id'] for t in tests}: errors.append(f"Exception unknown test {tid}")
        if x.get("status") in {"DEFERRED","OUT_OF_SCOPE"} and not (x.get("trigger") or x.get("rationale")):
            errors.append("Deferred/out-of-scope exception lacks rationale/trigger: "+x["id"])
    emit(f"exceptions={len(exc.get('exceptions',[]))}")
    emit(f"open_exceptions={len(open_exc)}")
    if open_exc: errors.append("Open exceptions: "+",".join(open_exc))

    # Cross-feature interaction closure
    inter=json.loads((test_dir/"INTERACTION_MATRIX.json").read_text(encoding="utf-8"))
    seen=set(); seen_pairs=set(); uncovered_inter=[]
    for x in inter.get("interactions",[]):
        if x["id"] in seen: errors.append("Duplicate interaction id: "+x["id"])
        seen.add(x["id"])
        pair=(x.get("a"),x.get("b"),x.get("risk"))
        if pair in seen_pairs: errors.append("Duplicate interaction tuple: "+repr(pair))
        seen_pairs.add(pair)
        if x.get("status")=="REQUIRED":
            if not x.get("tests"): uncovered_inter.append(x["id"])
            for tid in x.get("tests",[]):
                if tid not in {t['id'] for t in tests}: errors.append(f"Interaction unknown test {tid}")
    emit(f"interactions={len(inter.get('interactions',[]))}")
    emit(f"uncovered_required_interactions={len(uncovered_inter)}")
    if uncovered_inter: errors.append("Uncovered required interactions: "+",".join(uncovered_inter))

    # Every test must have exactly one primary release gate. This closes Requirement -> Test -> Gate vertically.
    gate_map_path=test_dir/"TEST_RELEASE_GATE_MAP.json"
    gate_map_schema_path=test_dir/"test-release-gate-map.schema.json"
    gate_map=json.loads(gate_map_path.read_text(encoding="utf-8"))
    gate_map_schema=json.loads(gate_map_schema_path.read_text(encoding="utf-8"))
    verr=list(Draft202012Validator(gate_map_schema).iter_errors(gate_map))
    if verr: errors.append(f"test-release-gate-map schema validation failed: {verr[0].message}")
    gate_entries=gate_map.get("entries",[])
    gate_test_ids=[x.get("test_id") for x in gate_entries]
    if len(gate_test_ids) != len(set(gate_test_ids)): errors.append("Duplicate test id in test-release-gate map")
    if set(gate_test_ids) != all_test_ids:
        errors.append(f"Test-release gate coverage mismatch: missing={sorted(all_test_ids-set(gate_test_ids))[:10]} extra={sorted(set(gate_test_ids)-all_test_ids)[:10]}")
    test_by={x["id"]:x for x in tests}
    allowed_gate_by_tier={
        "architecture":{"G0"},"schema":{"G0"},"unit":{"G0"},"property":{"G0"},"meta":{"G0"},"security":{"G0"},
        "migration":{"G1"},"e2e":{"G2"},"live":{"G3"},"package":{"G4"},"soak":{"G6"},
        "integration":{"G1","G2"},"concurrency":{"G1","G2"},"fault":{"G1","G2"},
    }
    for x in gate_entries:
        tid=x.get("test_id"); gate=x.get("primary_gate"); tier=x.get("tier")
        if tid in test_by:
            if x.get("priority") != test_by[tid].get("priority") or tier != test_by[tid].get("tier"):
                errors.append(f"Test-release gate metadata drift: {tid}")
            if gate not in allowed_gate_by_tier.get(tier,set()): errors.append(f"Unexpected primary gate {gate} for {tid}/{tier}")
    emit(f"gate_mapped_tests={len(gate_entries)}")


# Post-development closed-loop lifecycle completeness
life_dir=root/"08_LIFECYCLE"
if life_dir.exists():
    required_lifecycle_files=[
        "README.md","CLOSED_LOOP_OPERATING_MODEL.md","RELEASE_PROMOTION_ROLLBACK.md",
        "OPERATIONS_OBSERVABILITY_SLO.md","INCIDENT_PROBLEM_CHANGE.md",
        "FEEDBACK_TRIAGE_AND_LEARNING.md","EVIDENCE_RETENTION_AND_AUDIT.md",
        "DEPRECATION_EXTENSION_LIFECYCLE.md","POST_RELEASE_REVIEW.md",
        "closed-loop.json","closed-loop.schema.json","INVARIANT_CATALOG.md","release-gates.json",
        "release-gates.schema.json","signal-routing.json","signal-routing.schema.json","gate-evidence-policy.json","gate-evidence-policy.schema.json",
        "RUNBOOKS/RELEASE_RUNBOOK.md","RUNBOOKS/ROLLBACK_RUNBOOK.md",
        "RUNBOOKS/INCIDENT_RUNBOOK.md","RUNBOOKS/PROVIDER_MODEL_DRIFT_RUNBOOK.md",
        "RUNBOOKS/DATA_CORRUPTION_MIGRATION_RUNBOOK.md",
        "TEMPLATES/FEEDBACK_RECORD.md","TEMPLATES/RELEASE_EVIDENCE_INDEX.md",
        "TEMPLATES/POST_INCIDENT_REVIEW.md","TEMPLATES/ACCEPTED_RISK_OR_DEFERRED.md",
    ]
    missing=[x for x in required_lifecycle_files if not (life_dir/x).is_file()]
    if missing: errors.append("Lifecycle required files missing: "+",".join(missing))

    life=json.loads((life_dir/"closed-loop.json").read_text(encoding="utf-8"))
    life_schema=json.loads((life_dir/"closed-loop.schema.json").read_text(encoding="utf-8"))
    gates=json.loads((life_dir/"release-gates.json").read_text(encoding="utf-8"))
    gates_schema=json.loads((life_dir/"release-gates.schema.json").read_text(encoding="utf-8"))
    signals=json.loads((life_dir/"signal-routing.json").read_text(encoding="utf-8"))
    signals_schema=json.loads((life_dir/"signal-routing.schema.json").read_text(encoding="utf-8"))
    gate_policy=json.loads((life_dir/"gate-evidence-policy.json").read_text(encoding="utf-8"))
    gate_policy_schema=json.loads((life_dir/"gate-evidence-policy.schema.json").read_text(encoding="utf-8"))
    for label,obj,schema in [("closed-loop",life,life_schema),("release-gates",gates,gates_schema),("signal-routing",signals,signals_schema),("gate-evidence-policy",gate_policy,gate_policy_schema)]:
        verr=list(Draft202012Validator(schema).iter_errors(obj))
        if verr: errors.append(f"{label} schema validation failed: {verr[0].message}")

    stages=life.get("stages",[])
    stage_ids=[x.get("id") for x in stages]
    stage_set=set(stage_ids)
    if len(stage_set)!=len(stage_ids): errors.append("Duplicate lifecycle stage id")
    if life.get("entry_stage") not in stage_set: errors.append("Lifecycle entry_stage is unknown")
    for st in stages:
        for target in st.get("next",[])+st.get("failure_routes",[]):
            if target not in stage_set: errors.append(f"Lifecycle stage {st.get('id')} references unknown stage {target}")
    # Every stage must be reachable from the entry through either success or failure edges.
    reachable=set(); stack=[life.get("entry_stage")]
    by_id={x["id"]:x for x in stages}
    while stack:
        cur=stack.pop()
        if cur in reachable or cur not in by_id: continue
        reachable.add(cur)
        stack.extend(by_id[cur].get("next",[])+by_id[cur].get("failure_routes",[]))
    if reachable != stage_set: errors.append("Unreachable lifecycle stage(s): "+",".join(sorted(stage_set-reachable)))
    # The loop must explicitly return closure to intake, and improvement to RED.
    if "L0" not in by_id.get("L10",{}).get("next",[]): errors.append("Lifecycle Close does not return to Intake")
    if "L1" not in by_id.get("L9",{}).get("next",[]): errors.append("Lifecycle Improve does not return to RED")
    if "L8" not in by_id.get("L7",{}).get("failure_routes",[]): errors.append("Lifecycle Observe does not route failure to Learn")

    inv=life.get("invariants",[])
    if len({x.get("id") for x in inv}) != len(inv): errors.append("Duplicate lifecycle invariant id")
    if not inv: errors.append("Lifecycle has no correctness invariants")
    if len(inv) != 8: errors.append(f"Expected 8 lifecycle invariants, got {len(inv)}")
    if test_dir.exists():
        req_ids=set(reqs)
        for x in inv:
            linked=x.get("requirements",[])
            if not linked: errors.append("Lifecycle invariant lacks requirement traceability: "+x.get("id","?"))
            for rid in linked:
                if rid not in req_ids: errors.append(f"Lifecycle invariant {x.get('id')} references unknown requirement {rid}")
                elif not coverage.get(rid): errors.append(f"Lifecycle invariant {x.get('id')} requirement has no test coverage: {rid}")
    terminal=set(life.get("terminal_dispositions",[]))
    required_terminal={"FIXED","ROLLED_BACK","MITIGATED","ACCEPTED_RISK","DEFERRED_WITH_TRIGGER","INVALID_OR_NOT_REPRODUCED","DUPLICATE_LINKED","NO_CHANGE_REQUIRED"}
    if terminal != required_terminal: errors.append("Lifecycle terminal disposition set drift")
    closure_set=set(life.get("closure_requirements",[]))
    if "recurrence_watch" not in closure_set: errors.append("Lifecycle closure lacks recurrence_watch")
    if "unresolved_problem_link_or_rationale" not in closure_set: errors.append("Lifecycle closure lacks unresolved problem follow-up rule")

    gate_rows=gates.get("gates",[])
    if len({x.get("id") for x in gate_rows}) != len(gate_rows): errors.append("Duplicate lifecycle release gate id")
    gate_by={x["id"]:x for x in gate_rows}
    for required_gate in ["G0","G1","G2","G3","G4","G5","G7"]:
        if required_gate not in gate_by: errors.append("Missing lifecycle release gate: "+required_gate)
    if gate_by.get("G5",{}).get("on_fail") not in {"HOLD","ROLLBACK_OR_HOLD"}: errors.append("Publish gate lacks fail-closed action")
    if gate_by.get("G7",{}).get("on_fail") != "ROLLBACK_OR_HOLD": errors.append("Invariant gate must rollback or hold")
    for g in gate_rows:
        if g.get("blocking") and g.get("on_fail")=="OBSERVE": errors.append("Blocking gate cannot merely OBSERVE: "+g.get("id","?"))
        for dep in g.get("depends_on",[]):
            if dep not in gate_by: errors.append(f"Gate {g.get('id')} depends on unknown gate {dep}")
    # DAG / transitive publish dependency check.
    visiting=set(); visited=set()
    def visit_gate(gid):
        if gid in visiting: errors.append("Release gate dependency cycle at "+gid); return
        if gid in visited or gid not in gate_by: return
        visiting.add(gid)
        for dep in gate_by[gid].get("depends_on",[]): visit_gate(dep)
        visiting.remove(gid); visited.add(gid)
    for gid in gate_by: visit_gate(gid)
    def gate_ancestors(gid):
        out=set(); stack=list(gate_by.get(gid,{}).get("depends_on",[]))
        while stack:
            x=stack.pop()
            if x in out: continue
            out.add(x); stack.extend(gate_by.get(x,{}).get("depends_on",[]))
        return out
    publish_anc=gate_ancestors("G5")
    required_publish_anc={"G0","G1","G2","G3","G4","G7"}
    if not required_publish_anc.issubset(publish_anc): errors.append("Publish gate dependency DAG misses: "+",".join(sorted(required_publish_anc-publish_anc)))
    if test_dir.exists():
        blocking_gates={x['id'] for x in gate_rows if x.get('blocking')}
        for x in gate_entries:
            if x.get('priority')=='P0' and x.get('primary_gate') not in blocking_gates:
                errors.append(f"P0 test maps to non-blocking gate: {x.get('test_id')} -> {x.get('primary_gate')}")

    signal_rows=signals.get("signals",[])
    if len({x.get("id") for x in signal_rows}) != len(signal_rows): errors.append("Duplicate lifecycle signal id")
    if len({x.get("class") for x in signal_rows}) != len(signal_rows): errors.append("Duplicate lifecycle signal class")
    route_rows=signals.get("routes",[])
    route_by={x.get("id"):x for x in route_rows}
    if len(route_by) != len(route_rows): errors.append("Duplicate lifecycle signal route id")
    for rid,route in route_by.items():
        if route.get("lifecycle_stage") not in stage_set: errors.append(f"Signal route {rid} references unknown lifecycle stage")
        for field in ["guide","runbook"]:
            rel=route.get(field)
            if rel is not None and not (root/rel).is_file(): errors.append(f"Signal route {rid} missing {field}: {rel}")
    for sig in signal_rows:
        if sig.get("first_route") not in route_by: errors.append("Signal first_route is unresolved: "+sig.get("id","?"))
        if set(sig.get("possible_outcomes",[])) != terminal:
            errors.append("Signal does not enumerate all terminal dispositions: "+sig.get("id","?"))
        if sig.get("requires_evidence") is not True:
            errors.append("Signal routing must require evidence: "+sig.get("id","?"))
    # Gate evidence freshness/aging: existence is not validity.
    invalid_states=set(gate_policy.get("normalized_states",{}).get("invalid_as_evidence",[]))
    for st in {"QUEUED","CANCELLED","STALE","UNAVAILABLE"}:
        if st not in invalid_states: errors.append("Gate evidence policy missing invalid state: "+st)
    if gate_policy.get("normalized_states",{}).get("valid") != ["PASS"]:
        errors.append("Gate evidence valid state must normalize to PASS only")
    if "source_revision" not in gate_policy.get("candidate_identity_fields",[]):
        errors.append("Gate evidence policy missing source_revision binding")
    if gate_policy.get("thresholds_externalized") is not True:
        errors.append("Gate evidence time thresholds must remain externalized")
    if gate_policy.get("on_stale") != "HOLD" or gate_policy.get("on_timeout") != "HOLD":
        errors.append("Gate evidence stale/timeout must HOLD")
    emit("lifecycle_gate_evidence_policy=PASS" if not [e for e in errors if "Gate evidence" in e] else "lifecycle_gate_evidence_policy=FAIL")

    emit(f"lifecycle_stages={len(stages)}")
    emit(f"lifecycle_invariants={len(inv)}")
    emit(f"lifecycle_signals={len(signal_rows)}")
    emit(f"lifecycle_routes={len(route_rows)}")
    emit(f"lifecycle_gates={len(gate_rows)}")

# Master roadmap completeness / Core-boundary closure
road_dir=root/"09_ROADMAP"
if road_dir.exists():
    required_roadmap_files=[
        "README.md","PEERHUB_MASTER_ROADMAP_KO.md","M2_DURABLE_WORK_CONTINUITY.md",
        "M3_FEDERATED_INTELLIGENT_COLLABORATION.md","OPTIONAL_CAPABILITY_TRACKS.md",
        "MILESTONE_GATES.md","CAPABILITY_MILESTONE_MATRIX.md","M2_M3_PRE_IMPLEMENTATION_CONTRACT.md","M1_PUBLIC_CLI_CUTOVER_GATE.md","roadmap.json","roadmap.schema.json",
    ]
    missing=[x for x in required_roadmap_files if not (road_dir/x).is_file()]
    if missing: errors.append("Roadmap missing files: "+",".join(missing))
    road=json.loads((road_dir/"roadmap.json").read_text(encoding="utf-8"))
    road_schema=json.loads((road_dir/"roadmap.schema.json").read_text(encoding="utf-8"))
    verr=list(Draft202012Validator(road_schema).iter_errors(road))
    if verr: errors.append(f"roadmap schema validation failed: {verr[0].message}")
    core=road.get("core_contract",{})
    if core.get("concepts") != ["Peer","Stream","Record","Offset"]:
        errors.append("Roadmap Core concepts drifted from Peer/Stream/Record/Offset")
    if core.get("extension_imports_into_core") is not False:
        errors.append("Roadmap permits Extension import into Core")
    milestones=road.get("milestones",[])
    if [x.get("id") for x in milestones] != ["M1","M2","M3"]:
        errors.append("Required roadmap order must be exactly M1 -> M2 -> M3")
    if any(x.get("required") is not True for x in milestones):
        errors.append("M1-M3 must be required roadmap milestones")
    if any(x.get("core_delta") is not False for x in milestones):
        errors.append("M1-M3 roadmap must not expand the four-concept Core")
    opt=road.get("optional_tracks",[])
    expected_opt=[f"M4-{c}" for c in "ABCDEFGHIJKL"]
    if [x.get("id") for x in opt] != expected_opt:
        errors.append("Optional track ids/order must be M4-A..M4-L")
    for x in opt:
        if x.get("required") is not False or x.get("activation") != "EVIDENCE_TRIGGERED":
            errors.append("Optional track is not evidence-triggered opt-in: "+x.get("id","?"))
    quota=[x for x in road.get("capability_anchors",[]) if x.get("capability")=="quota observation/display"]
    if len(quota)!=1 or quota[0].get("milestone")!="M1" or "Diag" not in quota[0].get("owner",""):
        errors.append("quota/Diag capability must remain anchored in M1")
    if road.get('core_contract',{}).get('frozen_through') != 'ALL_REQUIRED_AND_OPTIONAL_TRACKS': errors.append('Core freeze must cover optional tracks too')
    if 'Generic Extension Host' not in [c for x in road.get('milestones',[]) if x.get('id')=='M2' for c in x.get('capabilities',[])]: errors.append('M2 must distinguish Generic Extension Host from M1 static modules')
    if 'Remote Runtime Port/Adapter Contract' not in [c for x in road.get('milestones',[]) if x.get('id')=='M3' for c in x.get('capabilities',[])]: errors.append('M3.6 must be remote runtime port/adapter contract, not mandatory HA')
    _depth={m.get('id'):m.get('design_depth') for m in road.get('milestones',[])}
    if _depth != {'M1':'TDD_READY','M2':'ROADMAP_FROZEN_PRE_TDD_REQUIRED','M3':'ROADMAP_FROZEN_PRE_TDD_REQUIRED'}: errors.append('roadmap design_depth drift')
    if any(not t.get('depends_on_capabilities') for t in road.get('optional_tracks',[])): errors.append('optional track capability dependency missing')
    if any('Remote Runtime Adapter Boundary' in str(x) for x in road.get('cross_milestone_rules',[])): errors.append('stale M3.6 Remote Runtime Adapter Boundary wording')
    emit(f"roadmap_required_milestones={len(milestones)}")
    emit(f"roadmap_optional_tracks={len(opt)}")
    emit("roadmap_core_boundary=PASS" if not [e for e in errors if e.startswith("Roadmap") or "roadmap" in e.lower() or "quota/Diag" in e] else "roadmap_core_boundary=FAIL")


# Unified general-standard conformance coverage
conf_dir=root/'10_STANDARDS_CONFORMANCE' if 'root' in globals() else ROOT/'10_STANDARDS_CONFORMANCE'
try:
    _std=json.loads((conf_dir/'UNIFIED_STANDARD_SNAPSHOT.json').read_text(encoding='utf-8'))
    _conf=json.loads((conf_dir/'product-conformance.json').read_text(encoding='utf-8'))
    _sch=json.loads((conf_dir/'product-conformance.schema.json').read_text(encoding='utf-8'))
    _ve=list(Draft202012Validator(_sch).iter_errors(_conf)) if 'Draft202012Validator' in globals() else []
    if _ve: errors.append('unified conformance schema validation failed: '+_ve[0].message)
    _ids=[x['id'] for x in _std.get('controls',[])]
    _cids=[x['id'] for x in _conf.get('controls',[])]
    if set(_ids)!=set(_cids) or len(_ids)!=len(_cids): errors.append('unified conformance control coverage mismatch')
    if _conf.get('standard_id')!=_std.get('standard_id') or _conf.get('standard_version')!=_std.get('version'): errors.append('unified conformance standard identity mismatch')
    _std_sha=hashlib.sha256((conf_dir/'UNIFIED_STANDARD_SNAPSHOT.json').read_bytes()).hexdigest()
    if _conf.get('standard_sha256') != _std_sha: errors.append('unified conformance standard_sha256 mismatch')
    if _conf.get('conformance_scope') not in {'TARGET_ARCHITECTURE','CURRENT_IMPLEMENTATION','MIXED_EXPLICIT'}: errors.append('unified conformance scope missing/invalid')
    for _x in _conf.get('controls',[]):
        if _x.get('status') in {'PASS','N_A'} and not _x.get('revisit_trigger'): errors.append('PASS/N_A conformance requires revisit_trigger: '+_x.get('id','?'))
    _mp=json.loads((conf_dir/'product-maturity.json').read_text(encoding='utf-8'))
    _mps=json.loads((conf_dir/'product-maturity.schema.json').read_text(encoding='utf-8'))
    _mve=list(Draft202012Validator(_mps).iter_errors(_mp))
    if _mve: errors.append('product maturity schema validation failed: '+_mve[0].message)
    _mids=[_x['id'] for _x in _mp.get('items',[])]
    if len(_mids)!=len(set(_mids)): errors.append('duplicate product maturity ids')
    if _conf.get('maturity_ref')!='10_STANDARDS_CONFORMANCE/product-maturity.json': errors.append('product maturity_ref drift')
    if _conf.get('distribution_ref')!='10_STANDARDS_CONFORMANCE/product-distribution.json': errors.append('product distribution_ref drift')
    if _conf.get('component_registry_ref')!='10_STANDARDS_CONFORMANCE/component-registry.json': errors.append('component registry_ref drift')
    _dp=json.loads((conf_dir/'product-distribution.json').read_text(encoding='utf-8')); _dps=json.loads((conf_dir/'product-distribution.schema.json').read_text(encoding='utf-8')); _dve=list(Draft202012Validator(_dps).iter_errors(_dp))
    if _dve: errors.append('product distribution schema validation failed: '+_dve[0].message)
    _cr=json.loads((conf_dir/'component-registry.json').read_text(encoding='utf-8')); _crs=json.loads((conf_dir/'component-register.schema.json').read_text(encoding='utf-8')); _crve=list(Draft202012Validator(_crs).iter_errors(_cr))
    if _crve: errors.append('component registry schema validation failed: '+_crve[0].message)
    if any(x.get('component_class')=='CORE' and x.get('id')!='m1-core' for x in _cr.get('components',[])): errors.append('PeerHub CORE expanded beyond M1 core')
    _ms={_x['id']:_x['status'] for _x in _mp.get('items',[])}
    if _ms.get('M1-IMPLEMENTATION')!='VERIFIED': errors.append('M1 implementation maturity must be VERIFIED at 4a6994 current-head snapshot')
    if _ms.get('M1-PUBLIC-CLI-CUTOVER')!='IMPLEMENTING': errors.append('M1 public CLI cutover maturity must be IMPLEMENTING')
    if _ms.get('M2')!='PLANNED' or _ms.get('M3')!='PLANNED': errors.append('M2/M3 must remain PLANNED')
    print('unified_conformance_controls='+str(len(_cids))) if 'emit' not in globals() else emit('unified_conformance_controls='+str(len(_cids)))
    print('unified_maturity_items='+str(len(_mids))) if 'emit' not in globals() else emit('unified_maturity_items='+str(len(_mids)))
except Exception as _exc:
    errors.append('unified conformance validation error: '+str(_exc))

# Manifest + checksum integrity. Mutable validator evidence and checksum file are excluded from self-reference.
manifest_path=root/"MANIFEST.json"
if manifest_path.exists():
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    entries=manifest.get("files",[])
    expected_paths={p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.name not in {"MANIFEST.json","SHA256SUMS.txt","PACKAGE_VALIDATION.txt"}}
    listed={x.get("path") for x in entries}
    if listed != expected_paths:
        errors.append(f"Manifest coverage mismatch: missing={sorted(expected_paths-listed)[:10]} extra={sorted(listed-expected_paths)[:10]}")
    for x in entries:
        p=root/x["path"]
        if not p.exists(): continue
        data=p.read_bytes(); h=hashlib.sha256(data).hexdigest()
        if x.get("bytes") != len(data) or x.get("sha256") != h: errors.append("Manifest hash/size mismatch: "+x["path"])
    emit(f"manifest_files={len(entries)}")

sum_path=root/"SHA256SUMS.txt"
if sum_path.exists():
    rows=[]
    for line in sum_path.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        m=re.match(r"^([0-9a-f]{64})  (.+)$",line)
        if not m: errors.append("Invalid SHA256SUMS line: "+line); continue
        rows.append((m.group(2),m.group(1)))
    expected_paths={p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.name not in {"SHA256SUMS.txt","PACKAGE_VALIDATION.txt"}}
    listed={p for p,_ in rows}
    if listed != expected_paths: errors.append(f"Checksum coverage mismatch: missing={sorted(expected_paths-listed)[:10]} extra={sorted(listed-expected_paths)[:10]}")
    for rel,h0 in rows:
        p=root/rel
        if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest()!=h0: errors.append("Checksum mismatch: "+rel)
    emit(f"checksum_files={len(rows)}")

# Final audit must contain only terminal statuses
for p in root.rglob("final_audit.csv"):
    with p.open(encoding="utf-8-sig",newline="") as f:
        rows=list(csv.DictReader(f))
    emit(f"audit_rows={len(rows)}")
    for r in rows:
        if r["status"] not in {"PASS","DEFERRED","N/A"}:
            errors.append("Non-terminal audit status: "+repr(r))

# Reject hidden C0 control characters in text artifacts (except TAB/LF/CR).
for p in root.rglob("*"):
    if not p.is_file() or p.suffix.lower() not in {".md", ".txt", ".csv", ".json", ".py", ".bat"}:
        continue
    data=p.read_bytes()
    bad=[(i,b) for i,b in enumerate(data) if b < 32 and b not in (9,10,13)]
    if bad:
        errors.append(f"Hidden control character(s) in {p.relative_to(root)}: {bad[:5]}")

# Avoid unresolved placeholders/TODO in normative markdown
for p in root.rglob("*.md"):
    txt=p.read_text(encoding="utf-8")
    for bad in ["TODO", "TBD", "FIXME"]:
        if re.search(rf"\b{bad}\b",txt):
            errors.append(f"{bad} found in {p.relative_to(root)}")

# semantic terminology/current-view lint must run BEFORE final PASS.
for _p in [root/'10_STANDARDS_CONFORMANCE'/'COMPONENT_STRUCTURE_MAPPING.md', root/'09_ROADMAP'/'PEERHUB_MASTER_ROADMAP_KO.md']:
    if _p.exists() and 'ADAPTER_PROVIDER' in _p.read_text(encoding='utf-8'): errors.append('stale flat ADAPTER_PROVIDER classification: '+str(_p.relative_to(root)))
_start=(root/'START_HERE_KO.md').read_text(encoding='utf-8')
_final=(root/'FINAL_ROADMAP_DECISION_KO.md').read_text(encoding='utf-8')
if '실제 구현 maturity는 IMPLEMENTED' in _start or '구현 maturity는 IMPLEMENTED' in _final: errors.append('human maturity view stale: M1 current-head is VERIFIED')
_byid={x['id']:x for x in _cr.get('components',[])}
if _byid.get('diag',{}).get('state_ownership') not in ([],None): errors.append('readonly diag must not own persisted state')
_bk=_byid.get('backup-recovery',{}).get('state_ownership',[])
if not any(x.get('kind')=='BACKUP' for x in _bk): errors.append('backup-recovery must classify BACKUP state explicitly')
_sc=_byid.get('skill-catalog',{}).get('state_ownership',[])
if not any(x.get('recovery_class')=='AUTHORITATIVE' for x in _sc) or not any(x.get('recovery_class')=='REBUILDABLE' for x in _sc): errors.append('skill-catalog must separate canonical source from generated index')

if errors:
    emit("RESULT=FAIL")
    for e in errors: emit("ERROR: "+e)
    (root/"PACKAGE_VALIDATION.txt").write_text("\n".join(log)+"\n",encoding="utf-8")
    sys.exit(1)
emit("RESULT=PASS")
(root/"PACKAGE_VALIDATION.txt").write_text("\n".join(log)+"\n",encoding="utf-8")
