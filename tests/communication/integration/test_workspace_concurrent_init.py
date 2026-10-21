"""Concurrent first use of a fresh workspace must converge on one generation (#249)."""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

CODE = "import sys; from pathlib import Path; from peerhub.core.workspace import Workspace; print(Workspace(Path(sys.argv[1])).generation())"


def test_concurrent_init_yields_single_generation(tmp_path):
    root = tmp_path / "ws"
    procs = [subprocess.Popen([sys.executable, "-c", CODE, str(root)], cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace") for _ in range(8)]
    outs = [p.communicate() for p in procs]
    assert all(p.returncode == 0 for p in procs), [e for _, e in outs]
    assert len({o.strip() for o, _ in outs}) == 1
    assert not list(root.glob("*.tmp"))
