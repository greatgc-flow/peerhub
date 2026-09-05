from pathlib import Path

from peerhub.application import bootstrap


def test_codex_js_probe_preserves_wrapper_parent_as_cwd(
    monkeypatch,
) -> None:
    cmd_path = Path("X:/portable/nodejs/npm-global/codex.cmd")
    node_exe = Path("X:/portable/nodejs/node.exe")
    codex_js = cmd_path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    monkeypatch.setattr(
        bootstrap,
        "resolve_direct_binary",
        lambda _cmd_path: [str(node_exe), str(codex_js)],
    )

    argv, cwd = bootstrap._resolve_probe_invocation(cmd_path)

    assert argv == (str(node_exe), str(codex_js), "--version")
    assert cwd == cmd_path.parent


def test_direct_exe_probe_uses_binary_parent_as_cwd(monkeypatch) -> None:
    cmd_path = Path("X:/portable/nodejs/npm-global/claude.cmd")
    real_exe = cmd_path.parent / "node_modules" / "claude.exe"
    monkeypatch.setattr(
        bootstrap,
        "resolve_direct_binary",
        lambda _cmd_path: [str(real_exe)],
    )

    argv, cwd = bootstrap._resolve_probe_invocation(cmd_path)

    assert argv == (str(real_exe), "--version")
    assert cwd == real_exe.parent
