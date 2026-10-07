"""Wave 6: every Exception-catalog / Interaction-matrix row that maps to a Wave 1-6 id has an actual test for that id."""
import json

import pytest

from tests.communication.spec import TESTSET, load
from tools.traceability import WAVES, found_ids, wave_of

pytestmark = [pytest.mark.meta, pytest.mark.traceability]


def _mapped():
    waves = json.loads(WAVES.read_text(encoding="utf-8"))["waves"]
    rows = [(e["id"], t) for e in load(TESTSET / "EXCEPTION_CATALOG.json")["exceptions"] if e["status"] == "COVERED" for t in e["tests"]]
    rows += [(i["id"], t) for i in load(TESTSET / "INTERACTION_MATRIX.json")["interactions"] if i["status"] == "REQUIRED" for t in i["tests"]]
    return waves, rows


def test_meta_w6_inventory_rows_for_waves_up_to_6_have_tests():
    waves, rows = _mapped()
    due = sorted({t for _, t in rows if (wave_of(t, waves) or 99) <= 6})
    present = found_ids(due)
    missing = [(rid, t) for rid, t in rows if t in due and t not in present]
    assert len(due) > 60 and not missing, missing  # non-vacuous: many ids are due, none lacks a test


def test_meta_w6_wave6_ids_are_all_referenced_by_an_inventory_or_the_catalog():
    waves, rows = _mapped()
    ids6 = {t for t in {x for _, x in rows} if wave_of(t, waves) == 6}
    assert {"FLT-001", "FLT-002", "FLT-009", "FLT-011", "FLT-012", "FLT-013", "FLT-014", "E2E-002", "E2E-004", "E2E-005", "E2E-008",
            "E2E-012", "SEC-001", "FLT-007", "FLT-008", "FLT-010", "FLT-003", "FLT-005", "FLT-006"} <= ids6
