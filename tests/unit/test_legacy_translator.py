"""Tests for legacy action translation."""

import json
from pathlib import Path

from peerhub.application.legacy import LEGACY_CATALOG


def test_legacy_catalog_matches_ledger():
    ledger_path = Path(__file__).parent.parent.parent / "docs" / "design" / "phase0" / "migration-ledger-v2.json"
    assert ledger_path.is_file(), f"Ledger file not found at {ledger_path}"
    
    with open(ledger_path) as f:
        ledger = json.load(f)["ledger"]
        
    expected_catalog = {
        item["legacy_action"]: item["target_peerhub_command"]
        for item in ledger
    }
    
    assert LEGACY_CATALOG == expected_catalog, "LEGACY_CATALOG in legacy.py has drifted from migration-ledger-v2.json"
