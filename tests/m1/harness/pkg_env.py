"""Package-gate harness (Wave 7): builds from a clean copy of the checkout OUTSIDE the repo, installs the built wheel into a
fresh venv, runs commands with a scrubbed environment. Test-side only."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_DROP_ENV = {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP", "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE"}


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() not in _DROP_ENV}
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env.update(extra or {})
    return env


def run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: float = 900.0,
        check: bool = False) -> subprocess.CompletedProcess:
    """Run a command, capture stdout/stderr as UTF-8 text (decode errors replaced: the test decides what to assert)."""
    cp = subprocess.run(cmd, cwd=str(cwd), env=env if env is not None else clean_env(), capture_output=True, timeout=timeout,
                        stdin=subprocess.DEVNULL)
    out = subprocess.CompletedProcess(cp.args, cp.returncode, cp.stdout.decode("utf-8", "replace"),
                                      cp.stderr.decode("utf-8", "replace"))
    if check and out.returncode != 0:
        raise AssertionError(f"command failed ({out.returncode}): {cmd}\n--- stdout\n{out.stdout[-3000:]}\n--- stderr\n{out.stderr[-3000:]}")
    return out


def checkout_files() -> list[str]:
    """Tracked files plus untracked-not-ignored ones: what a clean checkout of the working tree would contain."""
    cp = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=str(REPO), capture_output=True,
                        check=True)
    return sorted({p for p in cp.stdout.decode("utf-8").split("\0") if p and (REPO / p).is_file()})


def copy_checkout(dst: Path) -> Path:
    for rel in checkout_files():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, target)
    return dst


def build_dist(src: Path, out: Path) -> tuple[Path, Path]:
    """sdist from the checkout, then the wheel from the EXTRACTED sdist (so a file missing from the sdist also fails the wheel).
    `python -m build --no-isolation`: the build backend is the dev-environment setuptools (version asserted against pyproject);
    this also works where the stdlib `venv` module is unavailable."""
    import setuptools

    reqs = [r for r in tomllib.loads((src / "pyproject.toml").read_text(encoding="utf-8"))["build-system"]["requires"]]
    from packaging.requirements import Requirement
    for r in map(Requirement, reqs):
        assert r.name == "setuptools" and r.specifier.contains(setuptools.__version__), f"build env lacks {r}"
    out.mkdir(parents=True, exist_ok=True)
    run([sys.executable, "-m", "build", "--no-isolation", "--sdist", "--outdir", str(out), str(src)], cwd=src, check=True)
    (sdist,) = sorted(out.glob("*.tar.gz"))
    unpacked = out.parent / "sdist-unpacked"
    with tarfile.open(sdist) as t:
        t.extractall(unpacked, filter="data")
    (root,) = [p for p in unpacked.iterdir() if p.is_dir()]
    run([sys.executable, "-m", "build", "--no-isolation", "--wheel", "--outdir", str(out), str(root)], cwd=root, check=True)
    (wheel,) = sorted(out.glob("*.whl"))
    return wheel, sdist


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def venv_script(venv: Path, name: str) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin") / (name + (".exe" if os.name == "nt" else ""))


def make_venv(venv: Path) -> Path:
    """Fresh venv with pip. Stdlib `venv` when present; otherwise `virtualenv` (some distributions strip venv/ensurepip)."""
    probe = run([sys.executable, "-c", "import venv, ensurepip"], cwd=venv.parent)
    module = "venv" if probe.returncode == 0 else "virtualenv"
    run([sys.executable, "-m", module, str(venv)], cwd=venv.parent, check=True)
    return venv_python(venv)


def pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def source_version() -> str:
    """Parsed as plain text exactly like .github/workflows/publish.yml does (independent of importing the package)."""
    m = re.search(r"""__version__\s*=\s*["']([^"']+)["']""", (REPO / "peerhub/_version.py").read_text(encoding="utf-8"))
    assert m
    return m.group(1)


def wheel_names(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as z:
        return z.namelist()


def wheel_text(wheel: Path, suffix: str) -> str:
    with zipfile.ZipFile(wheel) as z:
        (name,) = [n for n in z.namelist() if n.endswith(suffix)]
        return z.read(name).decode("utf-8").replace("\r\n", "\n")


def local_os_name() -> str:
    return {"win32": "windows-latest", "linux": "ubuntu-latest", "darwin": "macos-latest"}.get(sys.platform, sys.platform)
