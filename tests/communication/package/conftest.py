"""Package-tier fixtures: ONE build and ONE fresh-venv install per session, all under pytest's temp root (outside the repo)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.communication.harness import pkg_env


@dataclass(frozen=True)
class Dist:
    src: Path
    wheel: Path
    sdist: Path


@dataclass(frozen=True)
class Installed:
    venv: Path
    python: Path

    def run(self, args: list[str], *, cwd: Path, env: dict | None = None, timeout: float = 300.0):
        return pkg_env.run([str(self.python), *args], cwd=cwd, env=env, timeout=timeout)

    def script(self, name: str) -> str:
        return str(pkg_env.venv_script(self.venv, name))


@pytest.fixture(scope="session")
def dist(tmp_path_factory) -> Dist:
    root = tmp_path_factory.mktemp("pkg-build")
    src = pkg_env.copy_checkout(root / "checkout")
    wheel, sdist = pkg_env.build_dist(src, root / "dist")
    return Dist(src, wheel, sdist)


@pytest.fixture(scope="session")
def installed(dist, tmp_path_factory) -> Installed:
    """Fresh venv; ONLY the built wheel (and its declared runtime dependencies) is installed: no editable path, no dev extras."""
    root = tmp_path_factory.mktemp("pkg-venv")
    py = pkg_env.make_venv(root / "venv")
    pkg_env.run([str(py), "-m", "pip", "install", str(dist.wheel)], cwd=root, check=True)
    return Installed(root / "venv", py)


@pytest.fixture
def outside(tmp_path) -> Path:
    """A working directory that is not the repo (so nothing resolves from the checkout)."""
    d = tmp_path / "cwd"
    d.mkdir()
    return d
