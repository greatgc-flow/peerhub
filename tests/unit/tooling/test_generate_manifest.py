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
def test_generator_requires_an_explicit_complete_legacy_archive(
    tmp_path: Path,
) -> None:
    """The historical tool fails clearly instead of probing a retired root."""

    with pytest.raises(FileNotFoundError, match="Legacy hub.py not found"):
        generate_manifest(
            sys_dir=tmp_path / "missing-legacy-sys",
            output_path=tmp_path / "must-not-exist.json",
        )
    assert not (tmp_path / "must-not-exist.json").exists()


def test_committed_manifest_snapshot_is_valid() -> None:
    assert DEFAULT_OUTPUT_PATH.exists(), f"Committed snapshot missing at {DEFAULT_OUTPUT_PATH}"
    with open(DEFAULT_OUTPUT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["action_vector"]["action_count"] == 90
    # Same 2026-09-14 externally-owned Hub pin as the live-generator test.
    assert data["source_files"]["hub_py"]["sha256"].startswith("b9a0f3a3")
