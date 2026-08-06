import csv
import json
import os
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent.parent.parent
    csv_path = root / "docs" / "design" / "phase0" / "hub-actions-v1.csv"
    json_path = root / "docs" / "design" / "phase0" / "legacy-hub-surface-current.json"
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        actions_v1 = {row['legacy_action']: row for row in reader}
        
    with open(json_path, 'r', encoding='utf-8') as f:
        surface = json.load(f)
        
    action_details = surface.get('action_details', {})
    
    ledger_entries = []
    
    for action, v1_data in actions_v1.items():
        details = action_details.get(action, {})
        
        reads = details.get('declared_state_reads', [])
        writes = details.get('declared_state_writes', [])
        effects = details.get('declared_external_effects', [])
        
        mutability = "mutable" if (writes or effects) else "readonly"
        
        entry = {
            "legacy_action": action,
            "legacy_handler": details.get("handler", "UNKNOWN"),
            "domain": v1_data.get("domain", "UNKNOWN"),
            "effect_class": "unspecified",
            "mutability": mutability,
            "relevant_legacy_arguments_defaults": "see_global_surface",
            "state_files_tables_read_written": {
                "reads": reads,
                "writes": writes
            },
            "subprocess_provider_effects": effects,
            "exit_error_output_contract": "unspecified",
            "target_peerhub_command": v1_data.get("proposed_v1_command", "UNKNOWN"),
            "target_peerhub_module_symbol": "unspecified",
            "disposition": v1_data.get("disposition", "UNKNOWN"),
            "implementation_status": "INVENTORIED",
            "fixture_ids": [],
            "current_capture_revision": None,
            "shadow_comparison_status": "NONE",
            "authority_phase": "ENGRAM_AUTHORITY",
            "rollback_owner_path": "legacy_hub",
            "last_verified_legacy_and_peerhub_revisions": None,
            "unresolved_drift_finding_ids": []
        }
        ledger_entries.append(entry)
        
    ledger_v2 = {
        "meta": {
            "schema_version": "2.0",
            "generator": "tools/migration_ledger/generate_ledger.py",
            "note": "Authoritative migration ledger v2"
        },
        "ledger": ledger_entries
    }
    
    out_json = root / "docs" / "design" / "phase0" / "migration-ledger-v2.json"
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(ledger_v2, f, indent=2)
        
    out_csv = root / "docs" / "design" / "phase0" / "migration-ledger-v2.csv"
    with open(out_csv, 'w', encoding='utf-8', newline='') as f:
        if not ledger_entries:
            return
        fieldnames = list(ledger_entries[0].keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for entry in ledger_entries:
            row = entry.copy()
            row["state_files_tables_read_written"] = json.dumps(row["state_files_tables_read_written"])
            row["subprocess_provider_effects"] = json.dumps(row["subprocess_provider_effects"])
            row["fixture_ids"] = json.dumps(row["fixture_ids"])
            row["unresolved_drift_finding_ids"] = json.dumps(row["unresolved_drift_finding_ids"])
            writer.writerow(row)
            
    print(f"Generated {out_json} and {out_csv}")
    print(f"Total rows: {len(ledger_entries)}")

if __name__ == '__main__':
    main()
