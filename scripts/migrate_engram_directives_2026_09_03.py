import sys
from pathlib import Path
import re

# Add peerhub to python path if not running via module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from peerhub.runtime import create_runtime
from peerhub.core.context import PathLayout, RuntimeContext
from peerhub.cli import SystemClock, UuidSource, _detect_workspace_home_id

DIRECTIVES_META = {
    "DIR-001": {
        "title": "ROI-Based Auto-Termination for Exhaustive Work Sessions",
        "digest": "sha256:bd6a452ea5af1fab2653055ebdb52ac023d345ac8eaf3565fb23f6262c359f98",
        "consumers": [{"consumer_name": "PeerHub/Orchestrator", "implementation_status": "PENDING", "evidence_refs": ["no ROI-gate/EXHAUSTIVE_COMPLETE consumer found in peerhub source"]}],
        "source_path": "_sys/ai/user-directives.md#DIR-001",
    },
    "DIR-002": {
        "title": "Minimum Non-Interactive Permissions for All Peers",
        "digest": "sha256:6a68da23ad2d663acbb64bda3e45b565e199c7df480fcfb6011e9b023cab79fb",
        "consumers": [{"consumer_name": "cc", "implementation_status": "PENDING", "evidence_refs": []}, {"consumer_name": "cx", "implementation_status": "PENDING", "evidence_refs": ["real PeerHub Codex adapter invocation supplies no sandbox flag and inherits config.toml"]}],
        "source_path": "_sys/ai/user-directives.md#DIR-002",
    },
    "DIR-003": {
        "title": "test_contracts.py Must Be Updated When hub.py Public API Changes",
        "digest": "sha256:9bb8df7f009ce6a572e9d9d5d9f9574a91ab9b1f1449b1d779e92909b193eac2",
        "consumers": [],
        "source_path": "_sys/ai/user-directives.md#DIR-003",
    },
    "DIR-004": {
        "title": "Measured-Only Claims — No Guessing, No Estimation",
        "digest": "sha256:47f495f76342681af8e7cccf76e09be88e37114e3138ece07fe14fbaa8880777",
        "consumers": [{"consumer_name": "peerhub.dispatch.capability", "implementation_status": "PENDING", "evidence_refs": []}],
        "source_path": "_sys/ai/user-directives.md#DIR-004",
    },
    "DIR-005": {
        "title": "Smartest-Model Final Arbiter — scoped peer-equality exception",
        "digest": "sha256:c871314e6f273ada6a56b8124466c56e301fadfb7cb8cc2e3eeb2a2f9cc9c934",
        "consumers": [{"consumer_name": "FinalArbiterPolicy/arbiter_review.py", "implementation_status": "PENDING", "evidence_refs": []}],
        "source_path": "_sys/ai/user-directives.md#DIR-005",
    },
    "DIR-006": {
        "title": "Unanimous Consensus Required at Direction/Plan Altitude, Not Per-Tool-Call",
        "digest": "sha256:ba5e5423878b59976a434bf2c0428e92e3b7a5f022a327096d816045dfb4a451",
        "consumers": [{"consumer_name": "ProposalCoordinator/.peerhub/proposals.json", "implementation_status": "PENDING", "evidence_refs": []}],
        "source_path": "_sys/ai/user-directives.md#DIR-006",
    },
}


def parse_directives(markdown_path: Path) -> dict[str, str]:
    text = markdown_path.read_text(encoding="utf-8")
    result = {}
    
    current_id = None
    current_lines = []
    
    for line in text.splitlines():
        if line.startswith("### DIR-"):
            # Save previous
            if current_id:
                result[current_id] = "\n".join(current_lines).strip()
            
            match = re.match(r"^### (DIR-\d+):", line)
            if match:
                current_id = match.group(1)
                current_lines = []
            else:
                current_id = None
        elif line.startswith("### ") or line.startswith("## "):
            # End of section (next heading)
            if current_id:
                result[current_id] = "\n".join(current_lines).strip()
                current_id = None
        elif current_id is not None:
            current_lines.append(line)
            
    if current_id:
        result[current_id] = "\n".join(current_lines).strip()
        
    return result


def main() -> None:
    source_md = Path(r"D:\Engram&Peerhub\engram-main-worktree\_sys\ai\user-directives.md")
    if not source_md.exists():
        print(f"Error: Could not find {source_md}")
        return
        
    parsed_rules = parse_directives(source_md)
    
    workspace_root = Path(__file__).resolve().parent.parent
    paths = PathLayout.for_workspace(workspace_root)
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name), 
        paths=paths, 
        clock=SystemClock(), 
        ids=UuidSource()
    )
    
    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        service = runtime.directive_service
        
        for d_id, meta in DIRECTIVES_META.items():
            rule_md = parsed_rules.get(d_id)
            if not rule_md:
                print(f"Warning: Rule markdown not found for {d_id}")
                continue
                
            print(f"Migrating {d_id}...")
            service.migrate(
                directive_id=d_id,
                title=meta["title"],
                rule_markdown=rule_md,
                digest=meta["digest"],
                consumers=meta["consumers"],
                source_path=meta["source_path"]
            )
            
            if d_id == "DIR-003":
                print(f"Retiring {d_id}...")
                service.retire(
                    directive_id=d_id,
                    actor_id="terminal",
                    reason="hub.py deleted in Engram/peerhub separation, directive has no surviving consumer"
                )
                
        print("Migration complete. Listing active directives:")
        for t in service.list_all():
            print(f"{t.target_id}: {dict(t.state)['lifecycle']}")

if __name__ == "__main__":
    main()
