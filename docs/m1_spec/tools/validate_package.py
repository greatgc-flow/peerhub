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

# PeerHub 109 command completeness
disp=root/"02_EXTENSIONS"/"109_COMMAND_DISPOSITION.csv"
if disp.exists():
    with disp.open(encoding="utf-8-sig",newline="") as f:
        rows=list(csv.DictReader(f))
    emit(f"legacy_commands={len(rows)}")
    if len(rows)!=109: errors.append(f"Expected 109 command rows, got {len(rows)}")
    if len({r["command"] for r in rows})!=len(rows): errors.append("Duplicate command")
    for r in rows:
        if not r["disposition"] or not r["target_component"] or not r["milestone"]:
            errors.append("Incomplete command disposition: "+repr(r))

    # Compare disposition against the exact current-repo call-map evidence snapshot.
    evidence_path=root/"07_AUDIT"/"CURRENT_REPO_COMMAND_MAP_20261003.json"
    evidence=json.loads(evidence_path.read_text(encoding="utf-8"))
    commands=evidence.get("commands",[])
    if evidence.get("measurement",{}).get("leaf_command_count") != 109:
        errors.append("Current repo evidence does not declare 109 leaf commands")
    if len(commands) != 109:
        errors.append(f"Expected 109 current repo command evidence rows, got {len(commands)}")
    if len(commands)==len(rows):
        for i,(r,c) in enumerate(zip(rows,commands),1):
            if r["command"] != c.get("path") or r["current_effect"] != c.get("effect"):
                errors.append(f"Command disposition drift at row {i}: {r['command']}/{r['current_effect']} != {c.get('path')}/{c.get('effect')}")

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

if errors:
    emit("RESULT=FAIL")
    for e in errors: emit("ERROR: "+e)
    (root/"PACKAGE_VALIDATION.txt").write_text("\n".join(log)+"\n",encoding="utf-8")
    sys.exit(1)
emit("RESULT=PASS")
(root/"PACKAGE_VALIDATION.txt").write_text("\n".join(log)+"\n",encoding="utf-8")
