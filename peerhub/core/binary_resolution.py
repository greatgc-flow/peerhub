"""Direct-binary resolution for npm-published .cmd wrapper scripts.

On Windows, invoking claude.cmd/codex.cmd through subprocess.Popen runs
cmd.exe, which mis-parses command lines when PATH/cwd contains a directory
with "&" in it. Resolving to the real underlying .exe/.js bypasses cmd.exe
entirely. Shared by application/bootstrap.py, dispatch/pipe.py, and
telemetry/quota_polling.py -- previously 3 independently-drifted copies of
this same fallback chain (consolidated 2026-09-05 per cx.deepthink audit).
"""

from __future__ import annotations

import shutil
from pathlib import Path


def resolve_direct_binary(cmd_path: Path) -> list[str] | None:
    """Given a resolved path to claude.cmd or codex.cmd, return the direct
    (non-cmd.exe-wrapper) invocation argv prefix, or None if the real
    binary/runtime cannot be located on disk."""
    name = cmd_path.name.lower()

    if name == "claude.cmd":
        real_exe = cmd_path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if not real_exe.exists():
            try:
                real_exe = cmd_path.resolve().parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
            except Exception:
                pass
        if real_exe.exists():
            return [str(real_exe)]
        return None

    if name == "codex.cmd":
        codex_js = cmd_path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        if not codex_js.exists():
            try:
                codex_js = cmd_path.resolve().parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            except Exception:
                pass
        node_exe = cmd_path.parent.parent / "node.exe"
        if not node_exe.exists():
            node_exe = cmd_path.parent / "node.exe"
        if not node_exe.exists():
            which_node = shutil.which("node.exe") or shutil.which("node")
            if which_node:
                node_exe = Path(which_node)
        if codex_js.exists() and node_exe.exists():
            return [str(node_exe), str(codex_js)]

        codex_exe = (
            cmd_path.parent / "node_modules" / "@openai" / "codex" / "node_modules"
            / "@openai" / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
        )
        if not codex_exe.exists():
            try:
                codex_exe = (
                    cmd_path.resolve().parent / "node_modules" / "@openai" / "codex" / "node_modules"
                    / "@openai" / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
                )
            except Exception:
                pass
        if codex_exe.exists():
            return [str(codex_exe)]
        return None

    return None
