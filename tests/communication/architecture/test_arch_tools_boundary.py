import ast
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.communication.spec import ROOT

pytestmark = pytest.mark.architecture
TOOLS = ROOT / "tools"


def _absolute_imports(tree: ast.AST) -> Iterator[tuple[int, str, str, str]]:
    """Yield the line, imported name, local binding and what that binding refers to."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name if alias.asname else alias.name.partition(".")[0]
                yield node.lineno, alias.name, alias.asname or target, target
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                name = f"{node.module}.{alias.name}"
                yield node.lineno, name, alias.asname or alias.name, name


def _is_private_cli_name(name: str) -> bool:
    prefix = "peerhub.cli."
    return name.startswith(prefix) and any(part.startswith("_") for part in name.removeprefix(prefix).split("."))


def _private_cli_names(tree: ast.AST) -> set[tuple[int, str]]:
    bindings: dict[str, set[str]] = defaultdict(set)
    violations: set[tuple[int, str]] = set()
    for line, name, binding, target in _absolute_imports(tree):
        bindings[binding].add(target)
        if _is_private_cli_name(name):
            violations.add((line, name))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not node.attr.startswith("_"):
            continue
        parts = [node.attr]
        base = node.value
        while isinstance(base, ast.Attribute):
            parts.append(base.attr)
            base = base.value
        if isinstance(base, ast.Name):
            suffix = ".".join(reversed(parts))
            for target in bindings.get(base.id, ()):
                name = f"{target}.{suffix}"
                if _is_private_cli_name(name):
                    violations.add((node.lineno, name))
    return violations


def test_peerhub_does_not_import_repository_tools() -> None:
    violations = []
    for path in sorted((ROOT / "peerhub").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, name, _, _ in _absolute_imports(tree):
            if name.partition(".")[0] == "tools":
                violations.append(f"{path.relative_to(ROOT)}:{line}: {name}")
    assert not violations, "PeerHub imports repository tools:\n" + "\n".join(violations)


def test_repository_tools_use_public_cli_names() -> None:
    violations = []
    for path in sorted(TOOLS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, name in sorted(_private_cli_names(tree)):
            violations.append(f"{path.relative_to(ROOT)}:{line}: {name}")
    assert not violations, "Repository tools use private CLI names:\n" + "\n".join(violations)


def test_package_configuration_excludes_repository_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    from setuptools import Distribution
    from setuptools.config.pyprojecttoml import apply_configuration

    monkeypatch.chdir(ROOT)
    dist = apply_configuration(Distribution(), ROOT / "pyproject.toml")
    build = dist.get_command_obj("build_py")
    build.ensure_finalized()

    selected = [Path(build.get_package_dir(package)) for package in build.packages]
    # The release artifact tests also check manifest-driven inclusion in the wheel and sdist.
    selected.extend(Path(source) / filename
                    for _, source, _, filenames in build.get_data_files_without_manifest()
                    for filename in filenames)
    violations = sorted(str(path) for path in selected if path.resolve().is_relative_to(TOOLS))
    assert not violations, f"Package configuration includes repository tools: {violations}"
