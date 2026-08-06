"""
generate_drift_report.py - Automated Drift Report Generator (Stage 0 Artifact)

Usage going forward:
1. Copy the current surface manifest to a backup:
   cp docs/design/phase0/legacy-hub-surface-current.json docs/design/phase0/legacy-hub-surface-old.json
2. Regenerate artifact 1 (the current state):
   python tools/surface_manifest/generate_manifest.py
3. Run this drift tool comparing old vs new:
   python tools/drift_report/generate_drift_report.py \\
       docs/design/phase0/legacy-hub-surface-old.json \\
       docs/design/phase0/legacy-hub-surface-current.json \\
       docs/design/phase0/shared-seam-ledger.json \\
       docs/design/phase0/drift-report.md
4. Review the generated drift-report.md, paying special attention to any NEEDS_RECHARACTERIZATION rows.
"""
import json
import sys
from pathlib import Path

def main():
    if len(sys.argv) < 4:
        print("Usage: generate_drift_report.py <old_manifest> <new_manifest> <shared_seam_ledger> [output_file]")
        sys.exit(1)

    old_manifest_path = Path(sys.argv[1])
    new_manifest_path = Path(sys.argv[2])
    ledger_path = Path(sys.argv[3])
    output_path = Path(sys.argv[4]) if len(sys.argv) > 4 else None

    with open(old_manifest_path, 'r', encoding='utf-8') as f:
        old_manifest = json.load(f)
    with open(new_manifest_path, 'r', encoding='utf-8') as f:
        new_manifest = json.load(f)
    with open(ledger_path, 'r', encoding='utf-8') as f:
        ledger = json.load(f)

    report_lines = []
    report_lines.append("# Legacy Hub Drift Report")
    report_lines.append("")

    hard_fail = False

    # 1. Action Vector Match
    old_actions = set(old_manifest['action_vector']['actions'])
    new_actions = set(new_manifest['action_vector']['actions'])
    
    added_actions = new_actions - old_actions
    removed_actions = old_actions - new_actions

    if added_actions or removed_actions:
        hard_fail = True
        report_lines.append("## 🚨 ACTION VECTOR MISMATCH (HARD FAIL)")
        if added_actions:
            report_lines.append(f"- **Added actions**: {', '.join(sorted(added_actions))}")
        if removed_actions:
            report_lines.append(f"- **Removed actions**: {', '.join(sorted(removed_actions))}")
        report_lines.append("")
    else:
        report_lines.append("## Action Vector")
        report_lines.append("- No actions added or removed.")
        report_lines.append("")

    # 2. Argparse Changes
    old_args = {a.get('dest'): a for a in old_manifest.get('argparse_surface', {}).get('arguments', [])}
    new_args = {a.get('dest'): a for a in new_manifest.get('argparse_surface', {}).get('arguments', [])}

    report_lines.append("## Argparse Changes")
    arg_changed = False
    for dest, new_arg in new_args.items():
        if dest not in old_args:
            report_lines.append(f"- Added argument: `{dest}`")
            arg_changed = True
        else:
            old_arg = old_args[dest]
            diffs = []
            for k in ['action', 'type', 'default', 'choices']:
                if old_arg.get(k) != new_arg.get(k):
                    diffs.append(f"{k} changed from {old_arg.get(k)} to {new_arg.get(k)}")
            if diffs:
                report_lines.append(f"- Modified argument `{dest}`: {', '.join(diffs)}")
                arg_changed = True
                
    for dest in old_args:
        if dest not in new_args:
            report_lines.append(f"- Removed argument: `{dest}`")
            arg_changed = True

    if not arg_changed:
        report_lines.append("- No argparse flag/default/choice changes detected.")
    report_lines.append("")

    # 3. Shared Helpers & Action Level Drift
    needs_rechar = set()
    action_notes = {a: [] for a in new_actions & old_actions}

    # Recompute transitive callers for all shared helpers in the new manifest
    shared_seams = ledger.get('shared_seams', {})
    new_transitive_callers = {h: set() for h in shared_seams.keys()}
    
    for action, details in new_manifest.get('action_details', {}).items():
        helpers = details.get('helper_dependencies', {}).get('transitive_internal_helpers', [])
        for h in helpers:
            if h in new_transitive_callers:
                new_transitive_callers[h].add(action)

    changed_helpers = set()
    for h, old_data in shared_seams.items():
        old_callers = set(old_data.get('transitive_callers', []))
        new_callers = new_transitive_callers[h]
        if old_callers != new_callers:
            changed_helpers.add(h)
            for a in (old_callers | new_callers):
                if a in action_notes:
                    action_notes[a].append(f"Downstream of changed shared helper `{h}`")
                    needs_rechar.add(a)

    if changed_helpers:
        report_lines.append("## Shared Helper Call-Graph Changes")
        for h in sorted(changed_helpers):
            report_lines.append(f"- Helper `{h}` call-graph membership changed.")
        report_lines.append("")

    # 4. Action-to-handler and state/effect changes
    for action in (new_actions & old_actions):
        old_det = old_manifest.get('action_details', {}).get(action, {})
        new_det = new_manifest.get('action_details', {}).get(action, {})

        # Handler mapping
        if old_det.get('handler') != new_det.get('handler'):
            action_notes[action].append(f"Handler changed from {old_det.get('handler')} to {new_det.get('handler')}")
            needs_rechar.add(action)

        # State reads
        if old_det.get('declared_state_reads') != new_det.get('declared_state_reads'):
            action_notes[action].append("State reads changed")
            needs_rechar.add(action)

        # State writes
        if old_det.get('declared_state_writes') != new_det.get('declared_state_writes'):
            action_notes[action].append("State writes changed")
            needs_rechar.add(action)

        # External effects
        if old_det.get('declared_external_effects') != new_det.get('declared_external_effects'):
            action_notes[action].append("External effects changed")
            needs_rechar.add(action)

    report_lines.append("## Action Drift & Recharacterization")
    if not needs_rechar:
        report_lines.append("- No actions require recharacterization.")
    else:
        for action in sorted(needs_rechar):
            notes = "; ".join(action_notes[action])
            report_lines.append(f"- **{action}**: NEEDS_RECHARACTERIZATION ({notes})")

    report_content = "\n".join(report_lines)
    
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report_content)
        print(f"Drift report written to {output_path}")
    else:
        print(report_content)

    if hard_fail:
        sys.exit(1)

if __name__ == '__main__':
    main()
