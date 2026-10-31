"""Deterministic contract tests for the same matrix used by both release jobs."""
import json
from pathlib import Path

import pytest

from tools.release_matrix import expected_names, read_matrix, verify_evidence_names


def _fake_junits(directory: Path, matrix: dict) -> None:
    directory.mkdir()
    for prefix in ("g4-matrix", "g2-rc-matrix"):
        for name in expected_names(matrix, prefix):
            (directory / name).write_text("<testsuite tests='1'/>", encoding="utf-8")


def test_declared_matrix_and_full_identity_set(tmp_path):
    matrix = read_matrix()
    _fake_junits(tmp_path / "junit", matrix)
    verify_evidence_names(tmp_path / "junit", matrix)
    assert len(expected_names(matrix, "g2-rc-matrix")) == (
        len(matrix["os"]) * len(matrix["python-version"])
    )


@pytest.mark.parametrize("prefix", ["g4-matrix", "g2-rc-matrix"])
def test_missing_cell_fails_even_with_same_total_file_count(tmp_path, prefix):
    matrix = read_matrix()
    directory = tmp_path / "junit"
    _fake_junits(directory, matrix)
    original = sorted(expected_names(matrix, prefix))[0]
    (directory / original).unlink()
    (directory / f"{prefix}-unknown-py99.xml").write_text("<testsuite/>", encoding="utf-8")
    with pytest.raises(ValueError, match="missing="):
        verify_evidence_names(directory, matrix)


@pytest.mark.parametrize("bad", [
    {"os": ["ubuntu-latest", "ubuntu-latest"], "python-version": ["3.12"]},
    {"os": ["windows-latest"], "python-version": []},
    {"os": ["ubuntu-latest"], "python-version": ["3.12", "3.12"]},
    {"os": ["ubuntu-latest"], "python-version": ["3.12"], "extra": True},
    {"os": "ubuntu-latest", "python-version": ["3.12"]},
])
def test_matrix_rejects_bad_or_duplicate_identity(tmp_path, bad):
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        read_matrix(path)


def test_empty_evidence_file_not_admitted(tmp_path):
    matrix = read_matrix()
    directory = tmp_path / "junit"
    _fake_junits(directory, matrix)
    first = sorted(expected_names(matrix, "g2-rc-matrix"))[0]
    (directory / first).write_bytes(b"")
    with pytest.raises(ValueError, match="empty JUnit"):
        verify_evidence_names(directory, matrix)


def test_publish_workflow_consumes_single_manifest():
    path = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "publish.yml"
    workflow = path.read_text(encoding="utf-8")
    assert workflow.count("fromJSON(needs.support_matrix.outputs.matrix)") == 2
    assert "python -m tools.release_matrix validate --junit-dir junit" in workflow
    assert "-ne 10" not in workflow
