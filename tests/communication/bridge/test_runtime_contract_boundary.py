"""Import-direction and compatibility gates for the logical Execution Kernel (#335).

These tests are deliberately offline. They exercise module ownership and the
historical public import path, without invoking provider CLIs or touching a DB.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from peerhub.extensions import bridge
from peerhub.extensions import runtime_contract as port
from peerhub.extensions.adapters import base as adapter


pytestmark = [pytest.mark.architecture, pytest.mark.unit, pytest.mark.platform_sensitive]
ROOT = Path(__file__).resolve().parents[3]


def test_bridge_compatibility_exports_are_identical_to_runtime_port() -> None:
    """External callers using bridge.* must receive the original types."""
    for name in (
        "RuntimeTargetError",
        "PrespawnError",
        "ContextLostError",
        "RuntimeTarget",
        "SteerableTarget",
        "RuntimeEvent",
    ):
        assert getattr(bridge, name) is getattr(port, name)


def test_adapter_and_bridge_share_the_same_runtime_errors() -> None:
    assert adapter.RuntimeTargetError is port.RuntimeTargetError
    assert adapter.PrespawnError is port.PrespawnError
    assert issubclass(port.ContextLostError, port.PrespawnError)
    error = port.RuntimeTargetError("ambiguous", termination_confirmed=False)
    assert str(error) == "ambiguous"
    assert error.termination_confirmed is False


def test_provider_adapter_cold_import_does_not_import_bridge() -> None:
    """An adapter need not load the implementation that owns persistence."""
    check = (
        "import sys; import peerhub.extensions.adapters.base; "
        "print(int('peerhub.extensions.bridge' in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", check],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=25,
        check=True,
    )
    assert result.stdout.strip() == "0", result.stderr


def _absolute_imports(path: Path) -> list[str]:
    """Inspect static imports; this does not claim to detect dynamic loading."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imports.append(node.module)
            elif node.level >= 2 and node.module:
                # Two dots from peerhub.core.* refer to peerhub.*
                imports.append("peerhub." + node.module)
    return imports


def test_communication_core_has_no_extension_or_cli_imports() -> None:
    """A permanent guard against reversing Core -> Extension dependency."""
    core = ROOT / "peerhub" / "core"
    files = sorted(core.rglob("*.py"))
    assert files
    violations = [
        f"{path.relative_to(ROOT)}: {module}"
        for path in files
        for module in _absolute_imports(path)
        if module == "peerhub.extensions"
        or module.startswith("peerhub.extensions.")
        or module == "peerhub.cli"
        or module.startswith("peerhub.cli.")
    ]
    assert not violations, "\n".join(violations)


def test_runtime_port_and_provider_adapter_do_not_import_bridge() -> None:
    targets = [
        ROOT / "peerhub" / "extensions" / "runtime_contract.py",
        ROOT / "peerhub" / "extensions" / "adapters" / "base.py",
    ]
    violations = [
        f"{path.relative_to(ROOT)}: {module}"
        for path in targets
        for module in _absolute_imports(path)
        if module == "peerhub.extensions.bridge"
        or module.startswith("peerhub.extensions.bridge.")
    ]
    assert not violations, "\n".join(violations)
