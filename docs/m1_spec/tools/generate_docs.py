from pathlib import Path
import json

root = Path(__file__).resolve().parents[1]
registry_path = root / ("02_STANDARDS" if (root/"02_STANDARDS").exists() else "03_STANDARDS") / "standards_registry.json"
data = json.loads(registry_path.read_text(encoding="utf-8"))
items = data["standards"]
key = "general_decision" if (root/"02_STANDARDS").exists() else "peerhub_decision"

lines = [
    "# 표준·OSS 적용 결정표",
    "",
    "> 이 문서는 `standards_registry.json`에서 생성되는 사람용 View입니다. JSON이 SSOT입니다.",
    "",
    "| 표준/기술 | 검증 버전/상태 | 결정 | 적용 경계 | 가지치기 원칙 | 원본 링크 |",
    "|---|---|---|---|---|---|",
]
for x in items:
    urls = "<br>".join(x["official_urls"])
    lines.append(f"| {x['name']} | {x['version']} / {x['status']} | **{x[key]}** | {x['use']} | {x['prune']} | {urls} |")
out_dir = registry_path.parent
(out_dir/"STANDARDS_DECISION_TABLE.md").write_text("\n".join(lines)+"\n", encoding="utf-8")

links = ["# 공식 원본 링크 목록 — 2026-10-01 검증","",
         "아래 링크는 설계 결정에 참고한 **공식/원본 문서**입니다.",""]
for i,x in enumerate(items,1):
    links += [f"## {i}. {x['name']} — {x['version']}","",f"- 상태: {x['status']}",f"- 확인 내용: {x['fact']}","- 원본:"]
    links += [f"  - {u}" for u in x["official_urls"]]
    links.append("")
(out_dir/"ORIGINAL_LINKS.md").write_text("\n".join(links)+"\n", encoding="utf-8")
print("Generated standards views from", registry_path)
