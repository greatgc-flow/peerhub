"""Ping every profile in docs/model-profiles/model-profiles.json once and check WHICH model really answered.

Direct one-shot CLI calls (uses a little quota), each in an empty temp directory:
  cc  claude -p ... --output-format json   -> evidence: the `modelUsage` keys of the result
  cx  codex exec --json ...                -> evidence: model/effort of the turn in the session log (CODEX_HOME/sessions)
  ag  agy -p ... --output-format json      -> evidence: the model named in the CLI log file (weaker: the CLI logs what it selected)
Usage: python -m tools.model_ping [profile_id ...] [--peer cc|cx|ag]     (exit 1 when any profile fails or a different model answered)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "model-profiles" / "model-profiles.json"
PROMPT = "Reply with exactly: PONG"
TIMEOUT_S = 240


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    exe = shutil.which(argv[0])
    if exe is None:
        raise FileNotFoundError(f"{argv[0]} is not on PATH")
    return subprocess.run([exe, *argv[1:]], cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL)


def ping_cc(model: str, effort: str | None, cwd: Path) -> tuple[bool, str, str]:
    argv = ["claude", "-p", PROMPT, "--model", model, "--output-format", "json"] + (["--effort", effort] if effort else [])
    out = json.loads(_run(argv, cwd).stdout)
    used = sorted(out.get("modelUsage", {}))
    if out.get("is_error"):  # e.g. 429 credits_required: the account cannot use this model, which is not a wrong profile
        return False, "", f"API ERROR {out.get('api_error_status')} {out.get('api_error')}: {str(out.get('result'))[:80]}"
    return "PONG" in str(out.get("result", "")), ",".join(used), "ok" if model in used else "MODEL MISMATCH"


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def ping_cx(model: str, effort: str | None, cwd: Path) -> tuple[bool, str, str]:
    argv = ["codex", "exec", "--json", "-m", model, "--skip-git-repo-check", "-s", "read-only"]
    if effort:
        argv += ["-c", f"model_reasoning_effort={effort}"]
    proc = _run(argv + [PROMPT], cwd)
    thread, reply = "", ""
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "thread.started":
            thread = event.get("thread_id", "")
        item = event.get("item") or {}
        if item.get("type") == "agent_message":
            reply = item.get("text", "")
    logs = list((_codex_home() / "sessions").rglob(f"*{thread}*.jsonl")) if thread else []
    for line in (logs[0].read_text(encoding="utf-8").splitlines() if logs else []):
        if '"turn_context"' in line:
            payload = json.loads(line)["payload"]
            seen = f"{payload.get('model')}/{payload.get('effort')}"
            good = payload.get("model") == model and (effort is None or payload.get("effort") == effort)
            return "PONG" in reply, seen, "ok" if good else "MODEL MISMATCH"
    return "PONG" in reply, "", "NO EVIDENCE (session log not found)"


def ping_ag(model: str, effort: str | None, cwd: Path) -> tuple[bool, str, str]:
    log = cwd / "agy.log"
    argv = ["agy.exe" if os.name == "nt" else "agy", "-p", PROMPT, "--model", model, "--output-format", "json", "--log-file", str(log)]
    out = json.loads(_run(argv, cwd).stdout)
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    return out.get("status") == "SUCCESS" and "PONG" in str(out.get("response", "")), model if model in text else "", \
        "ok (log)" if model in text else "NO EVIDENCE (model not in CLI log)"


PINGS = {"cc": ping_cc, "cx": ping_cx, "ag": ping_ag}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("profiles", nargs="*", help="profile ids such as cx.effort (default: all)")
    ap.add_argument("--peer", choices=sorted(PINGS), help="only this peer")
    args = ap.parse_args(argv)
    profiles = json.loads(MANIFEST.read_text(encoding="utf-8"))["profiles"]
    chosen = [p for p in profiles if (not args.profiles or p in args.profiles) and (not args.peer or p.startswith(args.peer + "."))]
    failed = 0
    for pid in chosen:
        peer = pid.split(".")[0]
        spec = profiles[pid]
        with tempfile.TemporaryDirectory(prefix="model_ping_") as tmp:
            try:
                replied, seen, verdict = PINGS[peer](spec["model"], spec.get("reasoning_effort"), Path(tmp))
            except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
                replied, seen, verdict = False, "", f"ERROR {type(exc).__name__}: {exc}"
        good = replied and verdict.startswith("ok")
        failed += not good
        print(f"{'PASS' if good else 'FAIL'}  {pid:<14} want={spec['model']}/{spec.get('reasoning_effort', '-')}  answered={seen or '?'}  {verdict}"
              f"{'' if replied else '  (no PONG)'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
