"""Package-tier fixtures: Supplied distributions or ONE local build, and ONE fresh-venv install per session under pytest's temp root (outside the repo)."""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from filelock import FileLock
from xdist import is_xdist_worker

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
def package_session_root(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.getbasetemp()
    return root.parent if is_xdist_worker(request) else root


def _prepare_once(session_root: Path, name: str, prepare: Callable[[Path], None]) -> Path:
    ready = session_root / f"{name}.ready"
    with FileLock(str(session_root / f"{name}.lock")):
        if ready.is_file():
            return session_root / ready.read_text(encoding="utf-8")
        # A child may still write here if its worker dies. Leave interrupted
        # attempts to pytest's temp retention and give each retry its own path.
        root = session_root / f"{name}-{uuid4().hex}"
        root.mkdir()
        prepare(root)
        pending = root / ".ready"
        pending.write_text(root.name, encoding="utf-8")
        pending.replace(ready)
        return root


@pytest.fixture(scope="session")
def dist(request: pytest.FixtureRequest) -> Dist:
    supplied = os.environ.get("PEERHUB_DIST_DIR")
    if supplied is None:
        def build(root: Path) -> None:
            src = pkg_env.copy_checkout(root / "checkout")
            pkg_env.build_dist(src, root / "dist")

        root = _prepare_once(request.getfixturevalue("package_session_root"), "pkg-build", build)
        source, directory = root / "checkout", root / "dist"
    else:
        source, directory = pkg_env.REPO, Path(supplied).resolve()

    wheels = sorted(p for p in directory.glob("*.whl") if p.is_file())
    sdists = sorted(p for p in directory.glob("*.tar.gz") if p.is_file())
    if supplied == "" or len(wheels) != 1 or len(sdists) != 1:
        location = f"PEERHUB_DIST_DIR={supplied!r}" if supplied is not None else str(directory)
        raise pytest.UsageError(
            f"{location} must contain exactly one wheel (*.whl) "
            f"and one sdist (*.tar.gz); found {len(wheels)} wheel(s), {len(sdists)} sdist(s)"
        )
    return Dist(source, wheels[0], sdists[0])


@pytest.fixture(scope="session")
def installed(dist: Dist, package_session_root: Path) -> Installed:
    """Fresh venv; ONLY the built wheel (and its declared runtime dependencies) is installed: no editable path, no dev extras."""
    def install(root: Path) -> None:
        py = pkg_env.make_venv(root / "venv")
        pkg_env.run([str(py), "-m", "pip", "install", str(dist.wheel)], cwd=root, check=True)

    venv = _prepare_once(package_session_root, "pkg-venv", install) / "venv"
    return Installed(venv, pkg_env.venv_python(venv))


@pytest.fixture
def outside(tmp_path) -> Path:
    """A working directory that is not the repo (so nothing resolves from the checkout)."""
    d = tmp_path / "cwd"
    d.mkdir()
    return d
