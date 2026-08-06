import json
import os
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent.parent.parent
    surface_json_path = root / "docs" / "design" / "phase0" / "legacy-hub-surface-current.json"
    
    with open(surface_json_path, 'r', encoding='utf-8') as f:
        surface = json.load(f)
        
    action_details = surface.get('action_details', {})
    
    # helper -> { 'direct_callers': [], 'transitive_callers': [] }
    seams = {}
    
    for action, details in action_details.items():
        direct_helpers = details.get('helper_dependencies', {}).get('internal_helpers', [])
        transitive_helpers = details.get('helper_dependencies', {}).get('transitive_internal_helpers', [])
        
        for helper in direct_helpers:
            if helper not in seams:
                seams[helper] = {'direct_callers': [], 'transitive_callers': []}
            if action not in seams[helper]['direct_callers']:
                seams[helper]['direct_callers'].append(action)
                
        for helper in transitive_helpers:
            if helper not in seams:
                seams[helper] = {'direct_callers': [], 'transitive_callers': []}
            if action not in seams[helper]['transitive_callers']:
                seams[helper]['transitive_callers'].append(action)
                
    # Filter to only actual shared seams (called by >1 action transitively or directly)
    # The prompt says: "the ones multiple action handlers call into"
    shared_seams = {}
    for helper, data in seams.items():
        if len(data['transitive_callers']) > 1:
            shared_seams[helper] = {
                "direct_callers_count": len(data['direct_callers']),
                "transitive_callers_count": len(data['transitive_callers']),
                "direct_callers": sorted(data['direct_callers']),
                "transitive_callers": sorted(data['transitive_callers'])
            }
            
    # Sort shared seams by number of transitive callers (descending), then name
    sorted_shared_seams = dict(sorted(shared_seams.items(), key=lambda x: (-x[1]['transitive_callers_count'], x[0])))

    ledger = {
        "meta": {
            "schema_version": "1.0",
            "generator": "tools/shared_seam_ledger/generate_ledger.py",
            "note": "Maps shared internal helpers (seams) to the actions that depend on them."
        },
        "total_shared_seams": len(sorted_shared_seams),
        "shared_seams": sorted_shared_seams
    }
    
    out_json = root / "docs" / "design" / "phase0" / "shared-seam-ledger.json"
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(ledger, f, indent=2)
        
    print(f"Generated {out_json}")
    print(f"Total shared seams identified: {len(sorted_shared_seams)}")

if __name__ == '__main__':
    main()
