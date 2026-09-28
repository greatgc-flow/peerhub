import argparse
import sys
from pathlib import Path
import re

# Add peerhub to python path if not running via module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from peerhub.runtime import create_runtime
from peerhub.core.context import PathLayout, RuntimeContext
from peerhub.cli import SystemClock, UuidSource, _detect_workspace_home_id  # pyright: ignore[reportPrivateUsage]
from peerhub.governance.directive_digest import compute_directive_digest

from collections.abc import Sequence
from typing import Any, cast
DIRECTIVES_META: dict[str, dict[str, Any]] = {
    "DIR-001": {
        "title": "ROI-Based Auto-Termination for Exhaustive Work Sessions",
        "consumers": [{"consumer_name": "PeerHub/Orchestrator", "implementation_status": "PENDING", "evidence_refs": ["no ROI-gate/EXHAUSTIVE_COMPLETE consumer found in peerhub source"]}],
        "source_path": "_sys/ai/user-directives.md#DIR-001",
    },
    "DIR-002": {
        "title": "Minimum Non-Interactive Permissions for All Peers",
        "consumers": [{"consumer_name": "cc", "implementation_status": "PENDING", "evidence_refs": []}, {"consumer_name": "cx", "implementation_status": "PENDING", "evidence_refs": ["real PeerHub Codex adapter invocation supplies no sandbox flag and inherits config.toml"]}],
        "source_path": "_sys/ai/user-directives.md#DIR-002",
    },
    "DIR-003": {
        "title": "test_contracts.py Must Be Updated When hub.py Public API Changes",
        "consumers": [],
        "source_path": "_sys/ai/user-directives.md#DIR-003",
    },
    "DIR-004": {
        "title": "Measured-Only Claims ??No Guessing, No Estimation",
        "consumers": [{"consumer_name": "peerhub.dispatch.capability", "implementation_status": "PENDING", "evidence_refs": []}],
        "source_path": "_sys/ai/user-directives.md#DIR-004",
    },
    "DIR-005": {
        "title": "Smartest-Model Final Arbiter ??scoped peer-equality exception",
        "consumers": [{"consumer_name": "FinalArbiterPolicy/arbiter_review.py", "implementation_status": "PENDING", "evidence_refs": []}],
        "source_path": "_sys/ai/user-directives.md#DIR-005",
    },
    "DIR-006": {
        "title": "Unanimous Consensus Required at Direction/Plan Altitude, Not Per-Tool-Call",
        "consumers": [{"consumer_name": "ProposalCoordinator/.peerhub/proposals.json", "implementation_status": "PENDING", "evidence_refs": []}],
        "source_path": "_sys/ai/user-directives.md#DIR-006",
    },
}


def parse_directives(markdown_path: Path) -> dict[str, str]:
    text = markdown_path.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    
    current_id = None
    current_lines: list[str] = []
    
    for line in text.splitlines():
        if line.startswith("### DIR-"):
            # Save previous
            if current_id:
                result[current_id] = "\n".join(current_lines).strip()
            
            match = re.match(r"^### (DIR-\d+):", line)
            if match:
                current_id = match.group(1)
                current_lines: list[str] = []
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


def migrate_directives(source_md: Path, workspace_root: Path) -> int:
    """Migrate the legacy markdown into an explicitly selected workspace."""

    if not source_md.is_file():
        print(f"Error: Could not find {source_md}")
        return 2

    parsed_rules = parse_directives(source_md)
    missing = sorted(set(DIRECTIVES_META) - set(parsed_rules))
    if missing:
        print(f"Error: Missing directive sections: {', '.join(missing)}")
        return 2

    workspace_root = workspace_root.resolve()
    paths = PathLayout.for_workspace(workspace_root)
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(
            paths.database_path, workspace_root.name
        ),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )

    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        service = runtime.directive_service

        for d_id, meta in DIRECTIVES_META.items():
            rule_md = parsed_rules[d_id]
            print(f"Migrating {d_id}...")
            service.migrate(
                directive_id=d_id,
                title=cast(str, meta["title"]),
                rule_markdown=rule_md,
                digest=compute_directive_digest(rule_md),
                consumers=cast(list[dict[str, Any]], meta["consumers"]),
                source_path=cast(str, meta["source_path"]),
            )

            if d_id == "DIR-003":
                print(f"Retiring {d_id}...")
                service.retire(
                    directive_id=d_id,
                    actor_id="terminal",
                    reason=(
                        "hub.py deleted in Engram/peerhub separation, "
                        "directive has no surviving consumer"
                    ),
                )

        print("Migration complete. Listing active directives:")
        for target in service.list_all():
            print(f"{target.target_id}: {dict(target.state)['lifecycle']}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate engram directives")
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to the legacy user-directives.md file",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Target PeerHub workspace (default: current directory)",
    )
    args = parser.parse_args(argv)
    return migrate_directives(args.source, args.workspace)

if __name__ == "__main__":
    raise SystemExit(main())
