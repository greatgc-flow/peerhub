"""Tests for direct binary resolution."""

import shutil
from pathlib import Path

from peerhub.core.binary_resolution import resolve_direct_binary


def test_resolve_claude_cmd_parent_path(tmp_path: Path):
    cmd_path = tmp_path / "npm-global" / "claude.cmd"
    real_exe = tmp_path / "npm-global" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    real_exe.parent.mkdir(parents=True)
    real_exe.touch()
    
    result = resolve_direct_binary(cmd_path)
    assert result == [str(real_exe)]


def test_resolve_claude_cmd_resolved_parent_fallback(tmp_path: Path, monkeypatch):
    link_dir = tmp_path / "link-global"
    real_dir = tmp_path / "real-global"
    
    cmd_path = link_dir / "claude.cmd"
    
    real_exe = real_dir / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    real_exe.parent.mkdir(parents=True)
    real_exe.touch()
    
    original_resolve = Path.resolve
    def mock_resolve(self, *args, **kwargs):
        if self.name == "claude.cmd":
            return real_dir / self.name
        return original_resolve(self, *args, **kwargs)
        
    monkeypatch.setattr(Path, "resolve", mock_resolve)
    
    result = resolve_direct_binary(cmd_path)
    assert result == [str(real_exe)]


def test_resolve_claude_cmd_none(tmp_path: Path):
    cmd_path = tmp_path / "claude.cmd"
    assert resolve_direct_binary(cmd_path) is None


def test_resolve_codex_cmd_js_and_node(tmp_path: Path):
    cmd_path = tmp_path / "npm-global" / "codex.cmd"
    codex_js = tmp_path / "npm-global" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    codex_js.parent.mkdir(parents=True)
    codex_js.touch()
    
    node_exe = tmp_path / "node.exe"
    node_exe.touch()
    
    result = resolve_direct_binary(cmd_path)
    assert result == [str(node_exe), str(codex_js)]


def test_resolve_codex_cmd_uses_path_node_fallback(monkeypatch):
    cmd_path = Path("X:/portable/nodejs/npm-global/codex.cmd")
    codex_js = cmd_path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    path_node = Path("Y:/system-node/node.exe")
    existing = {codex_js, path_node}
    monkeypatch.setattr(Path, "exists", lambda self: self in existing)
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: str(path_node) if name == "node.exe" else None,
    )

    result = resolve_direct_binary(cmd_path)

    assert result == [str(path_node), str(codex_js)]


def test_resolve_codex_cmd_standalone_exe(tmp_path: Path):
    cmd_path = tmp_path / "npm-global" / "codex.cmd"
    codex_exe = (
        tmp_path / "npm-global" / "node_modules" / "@openai" / "codex" / "node_modules"
        / "@openai" / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
    )
    codex_exe.parent.mkdir(parents=True)
    codex_exe.touch()
    
    result = resolve_direct_binary(cmd_path)
    assert result == [str(codex_exe)]


def test_resolve_codex_cmd_none(tmp_path: Path):
    cmd_path = tmp_path / "codex.cmd"
    assert resolve_direct_binary(cmd_path) is None


def test_unrecognized_cmd(tmp_path: Path):
    cmd_path = tmp_path / "other.cmd"
    assert resolve_direct_binary(cmd_path) is None
