"""
test_generate_manifest.py - Unit test for Stage 0 Legacy Hub Surface Manifest Generator
"""
import json
import sys
from pathlib import Path
import pytest

PEERHUB_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PEERHUB_ROOT) not in sys.path:
    sys.path.insert(0, str(PEERHUB_ROOT))

# Use importlib to avoid pytest collection path issues
import importlib.util
spec = importlib.util.spec_from_file_location(
    "generate_manifest_mod",
    PEERHUB_ROOT / "tools" / "surface_manifest" / "generate_manifest.py"
)
gen_mod = importlib.util.module_from_spec(spec)
sys.modules["generate_manifest_mod"] = gen_mod
spec.loader.exec_module(gen_mod)

generate_manifest = gen_mod.generate_manifest
DEFAULT_OUTPUT_PATH = gen_mod.DEFAULT_OUTPUT_PATH
DEFAULT_SYS_DIR = gen_mod.DEFAULT_SYS_DIR


def test_generator_runs_and_produces_valid_manifest(tmp_path: Path) -> None:
    temp_output = tmp_path / "test-surface-manifest.json"
    manifest = generate_manifest(sys_dir=DEFAULT_SYS_DIR, output_path=temp_output)

    assert temp_output.exists()
    assert manifest["meta"]["schema_version"] == "1.0"
    assert manifest["meta"]["generator_name"] == "tools/surface_manifest/generate_manifest.py"

    # Action vector invariants
    action_vector = manifest["action_vector"]
    assert action_vector["action_count"] == 90
    assert len(action_vector["actions"]) == 90
    assert len(action_vector["action_vector_digest"]) == 64

    # hub.py hash invariant (3b2d7503...), i.e. live P:/_sys/core/hub.py as of
    # legacy-hub commit 656a1f8. This pin tracks a file outside this repo, so it
    # must be re-pinned whenever legacy hub.py changes; the previous value
    # (bd13cf55, commit 54bedd7) is the one frozen in the committed snapshot below.
    hub_info = manifest["source_files"]["hub_py"]
    assert hub_info["sha256"].startswith("3b2d7503")
    assert hub_info["line_count"] > 10000

    # Dispatch table & Action details 1-to-1 match
    dispatch = manifest["dispatch_table"]
    details = manifest["action_details"]
    assert len(dispatch) == 90
    assert len(details) == 90
    assert set(action_vector["actions"]) == set(dispatch.keys())
    assert set(action_vector["actions"]) == set(details.keys())

    # Help receipt invariant
    help_receipt = manifest["help_receipt"]
    assert "usage: hub" in help_receipt["normalized_help_text"]
    assert len(help_receipt["help_text_sha256"]) == 64


def test_committed_manifest_snapshot_is_valid() -> None:
    assert DEFAULT_OUTPUT_PATH.exists(), f"Committed snapshot missing at {DEFAULT_OUTPUT_PATH}"
    with open(DEFAULT_OUTPUT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["action_vector"]["action_count"] == 90
    assert data["source_files"]["hub_py"]["sha256"].startswith("bd13cf55")
