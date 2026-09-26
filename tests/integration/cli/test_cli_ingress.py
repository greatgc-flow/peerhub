"""`ask` / `broadcast` reject every ingress violation with exit 2 BEFORE dispatching anything."""
from pathlib import Path

import pytest

from peerhub.cli import main


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    assert main(["workspace", "init", "--workspace", str(tmp_path)]) == 0
    return tmp_path


@pytest.mark.parametrize("command,base", [("ask", ["ask", "cx"]), ("broadcast", ["broadcast"])])
def test_zero_and_ambiguous_and_selector_and_empty_are_exit_2(ws, capsys, command, base):
    f = ws / "q.txt"
    f.write_text("hello", encoding="utf-8")
    empty = ws / "empty.txt"
    empty.write_bytes(b"")
    common = ["--workspace", str(ws)]
    assert main(base + common) == 2
    assert "missing input" in capsys.readouterr().err
    assert main(base + ["inline", "--query-file", str(f)] + common) == 2
    assert "ambiguous input" in capsys.readouterr().err
    assert main(base + ["--query-file", "-"] + common) == 2
    assert "unsupported selector" in capsys.readouterr().err
    assert main(base + ["--query-file", str(empty)] + common) == 2
    assert "empty input" in capsys.readouterr().err
    assert main(base + ["--query-file", str(ws)] + common) == 2
    assert "directory" in capsys.readouterr().err
    assert f.read_text(encoding="utf-8") == "hello"  # borrowed file never touched
