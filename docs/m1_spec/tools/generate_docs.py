from pathlib import Path
import json

root = Path(__file__).resolve().parents[1]
registry_path = root / ("02_STANDARDS" if (root/"02_STANDARDS").exists() else "03_STANDARDS") / "standards_registry.json"
data = json.loads(registry_path.read_text(encoding="utf-8"))
items = data["standards"]
key = "general_decision" if (root/"02_STANDARDS").exists() else "peerhub_decision"

lines = [
    "# Standards/OSS Adoption Decision Table",
    "",
    "> This document is a human-readable View generated from `standards_registry.json`. JSON is the SSOT.",
    "",
    "| Standard/Technology | Verified Version/Status | Decision | Application Boundary | Pruning Principle | Source Links |",
    "|---|---|---|---|---|---|",
]
for x in items:
    urls = "<br>".join(x["official_urls"])
    lines.append(f"| {x['name']} | {x['version']} / {x['status']} | **{x[key]}** | {x['use']} | {x['prune']} | {urls} |")
out_dir = registry_path.parent
(out_dir/"STANDARDS_DECISION_TABLE.md").write_text("\n".join(lines)+"\n", encoding="utf-8")

links = ["# Official Original Link List — 2026-10-01 Verified","",
         "The links below are the **official/original documents** referenced for design decisions.",""]
for i,x in enumerate(items,1):
    links += [f"## {i}. {x['name']} — {x['version']}","",f"- Status: {x['status']}",f"- Verified content: {x['fact']}","- Original:"]
    links += [f"  - {u}" for u in x["official_urls"]]
    links.append("")
(out_dir/"ORIGINAL_LINKS.md").write_text("\n".join(links)+"\n", encoding="utf-8")
print("Generated standards views from", registry_path)
