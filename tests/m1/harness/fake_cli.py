"""Tiny fake vendor CLI (run as `python fake_cli.py <args>`). Mode via env FAKE_MODE; log via FAKE_LOG; kind via FAKE_KIND.
Modes: ok nonzero hang huge garbage secret_echo secret_fail vendor_error."""
import json
import os
import sys
import time

mode = os.environ.get("FAKE_MODE", "ok")
kind = os.environ.get("FAKE_KIND", "cc")
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print(os.environ.get("FAKE_VERSION", "9.8.7 (fake)"))
    sys.exit(0)
if args[:1] == ["--help"]:
    print("usage: fake [--resume ID] [--conversation ID] resume" if os.environ.get("FAKE_HELP_RESUME", "1") == "1" else "usage: fake")
    sys.exit(0)
prompt = sys.stdin.read() if kind in ("cc", "cx") else args[args.index("-p") + 1]
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": args, "prompt_len": len(prompt), "cwd": os.getcwd(), "prompt": prompt}) + "\n")
secret = os.environ.get("FAKE_SECRET", "")


def emit(text):
    if kind == "cc":
        print(json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}))
        print(json.dumps({"type": "result", "is_error": mode == "vendor_error", "result": text, "session_id": "s1"}))
    elif kind == "cx":
        print(json.dumps({"type": "thread.started", "thread_id": "t1"}))
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
    else:
        print(json.dumps({"response": text, "conversation_id": "c1"}))


if mode == "hang":
    time.sleep(600)
elif mode == "huge":
    sys.stdout.write("x" * 5_000_000)
    sys.stdout.flush()
elif mode == "garbage":
    print("\x00\x01 not json at all {{{")
elif mode == "nonzero":
    sys.stderr.write("boom: failure while handling: " + prompt + "\n")
    sys.exit(3)
elif mode == "secret_fail":
    sys.stderr.write(f"auth error token={secret} Bearer abcdefghijkl1234\n")
    sys.exit(2)
elif mode == "secret_echo":
    emit(f"here is {secret}")
elif mode == "vendor_error":
    emit("quota exceeded")
else:
    emit("OK")
