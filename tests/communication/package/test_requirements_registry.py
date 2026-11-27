"""Keep durable requirements tied to regression tests in the checkout."""

import ast
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.package


def test_requirements_registry_names_existing_tests() -> None:
    registry = (ROOT / "docs/requirements.md").read_text(encoding="utf-8")
    rows = [line.strip().removeprefix("|").removesuffix("|").split("|")
            for line in registry.splitlines() if line.lstrip().startswith("|")]
    assert len(rows) > 2, "The requirements registry must contain test rows."
    assert [cell.strip() for cell in rows[0]] == ["ID", "requirement", "test"]

    ids = [row[0].strip() for row in rows[2:]]
    assert len(ids) == len(set(ids)), "Requirement IDs must be unique."
    for requirement_id in ids:  # legacy numbers, or a topic slug so parallel lanes never pick the same next number
        assert re.fullmatch(r"R[1-9]\d*|R-[a-z0-9]+(?:-[a-z0-9]+)*", requirement_id), requirement_id
    for row in rows[2:]:
        requirement_id, _, references = (cell.strip() for cell in row)
        if re.fullmatch(r"MISSING \(tracked #[1-9]\d*\)", references):
            continue
        assert references, f"{requirement_id} has an empty test cell"

        for reference in references.split(","):
            node = reference.strip().strip("`")
            filename, separator, name = node.partition("::")
            path = (ROOT / filename).resolve()
            assert separator and name.startswith("test_"), (
                f"{requirement_id} needs a path::test_name or MISSING (tracked #N), got {node!r}"
            )
            assert path.is_relative_to(ROOT / "tests") and path.suffix == ".py" and path.is_file(), (
                f"{requirement_id} names a missing test file: {filename}"
            )
            module = ast.parse(path.read_text(encoding="utf-8"), filename=filename)
            assert any(isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)) and definition.name == name
                       for definition in module.body), f"{requirement_id} names a missing test: {node}"
